"""Shared fixtures.

Two kinds of test live in this suite and they prove different things:

* tests over the **real pinned corpus** prove what the catalog actually does to
  the three registries as they stand at the pinned commits;
* tests over a **synthetic snapshot** prove one rule in isolation, including
  rules no current record triggers. Those are built with :func:`make_snapshot`,
  which writes a manifest and its files exactly the way the real one is written,
  so a synthetic root exercises the same loader, the same integrity gate and the
  same projection.

Both are offline. Neither reads or writes anything outside the test's own
directory and the repository's read-only snapshot.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil

import pytest

from ginkgo_runtime.catalog.authority import (
    CAPABILITY_REQUEST_SCHEMA,
    SKILL_DESCRIPTOR_SCHEMA,
)
from ginkgo_runtime.catalog.build import build_catalog, default_root

REPO_ROOT = default_root()

#: The two frozen schemas every build needs, and where they sit in the snapshot.
AUTHORITY_SOURCES = (
    ("Ginkgo-Core/ai-stack", CAPABILITY_REQUEST_SCHEMA, "ai-stack"),
    ("Ginkgo-Core/ai-stack", SKILL_DESCRIPTOR_SCHEMA, "ai-stack"),
)

FAKE_COMMIT_A = "a" * 40
FAKE_COMMIT_B = "b" * 40


def git_blob_sha(data: bytes) -> str:
    """The git object id for ``data``, the same value the GitHub API reports."""
    header = b"blob %d\0" % len(data)
    return hashlib.sha1(header + data).hexdigest()


def _entry(repo, path, slug, role, record_class, collection_key, commit_sha, data):
    return {
        "blob_sha": git_blob_sha(data),
        "collection_key": collection_key,
        "commit_sha": commit_sha,
        "path": path,
        "record_class": record_class,
        "ref": "main",
        "repo": repo,
        "role": role,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
        "snapshot_path": "catalog/sources/%s/%s" % (slug, path),
    }


def make_snapshot(root, sources, commit_sha=FAKE_COMMIT_A, include_authority=True):
    """Write a manifest and its pinned files under ``root``. Returns ``str(root)``.

    ``sources`` is a list of ``(path, record_class, collection_key, text)``
    tuples, all attributed to ``Ginkgo-Core/ginkgo-agents`` unless the path is
    given as a ``(repo, slug, path)`` triple.
    """
    root = str(root)
    entries = []

    if include_authority:
        for repo, path, slug in AUTHORITY_SOURCES:
            origin = os.path.join(REPO_ROOT, "catalog", "sources", slug, *path.split("/"))
            with open(origin, "rb") as handle:
                data = handle.read()
            destination = os.path.join(root, "catalog", "sources", slug, *path.split("/"))
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            shutil.copyfile(origin, destination)
            entries.append(
                _entry(repo, path, slug, "authority", None, None, commit_sha, data)
            )

    for source in sources:
        path, record_class, collection_key, text = source
        repo, slug = "Ginkgo-Core/ginkgo-agents", "ginkgo-agents"
        data = text.encode("utf-8") if isinstance(text, str) else text
        destination = os.path.join(root, "catalog", "sources", slug, *path.split("/"))
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        with open(destination, "wb") as handle:
            handle.write(data)
        entries.append(
            _entry(repo, path, slug, "projected", record_class, collection_key, commit_sha, data)
        )

    entries.sort(key=lambda e: (e["repo"], e["path"]))
    manifest_path = os.path.join(root, "catalog", "sources.manifest.json")
    os.makedirs(os.path.dirname(manifest_path), exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(
            {
                "schema_version": "ginkgo.runtime-catalog.source-manifest.v1",
                "description": "synthetic snapshot for tests",
                "sources": entries,
            },
            handle,
            indent=2,
            sort_keys=True,
        )
        handle.write("\n")
    return root


def one_record(tmp_path, yaml_text, **kwargs):
    """Build a catalog whose only projected source is one skill descriptor."""
    root = make_snapshot(
        tmp_path, [("features/record.yaml", "skill_descriptor", None, yaml_text)], **kwargs
    )
    return build_catalog(root)


@pytest.fixture(scope="session")
def catalog():
    """The real catalog, built from the repository's pinned snapshot."""
    return build_catalog(REPO_ROOT)


@pytest.fixture(scope="session")
def report(catalog):
    from ginkgo_runtime.catalog.build import rejection_report

    return rejection_report(catalog)


# ---------------------------------------------------------------------------
# RK-01 Capability Bus fixtures
# ---------------------------------------------------------------------------


def capability_request(**overrides):
    """A minimal request that validates against the frozen ``CapabilityRequest v1``.

    Every negative test starts from a document that is known-good, so a failure
    is attributable to the one field the test changed and to nothing else.
    """
    request = {
        "request_id": "req-0001",
        "correlation_id": "corr-0001",
        "caller": {
            "actor_id": "actor-1",
            "actor_type": "API",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
        },
        "capability": {"name": "system.echo.execute", "contract_version": "v1"},
        "input": {"message": "hello"},
        "constraints": {"fallback_policy": "none"},
        "governance": {"approval_context": {}, "external_gate_context": {}},
        "evidence": {"required": True},
        "idempotency_key": "idem-0001",
    }
    request.update(overrides)
    return request


def execution_result(**overrides):
    """A minimal document that validates against ``RuntimeExecutionResult v1``."""
    result = {
        "schema_version": "ginkgo.runtime-execution-result.v1",
        "execution_id": "exec-0001",
        "request_id": "req-0001",
        "correlation_id": "corr-0001",
        "idempotency_key": "idem-0001",
        "decision_ref": {"decision_id": "dec-0001", "decision_hash": "a" * 64},
        "state": "succeeded",
        "terminal": True,
        "telemetry": {
            "started_at": "2026-08-21T12:00:00Z",
            "ended_at": "2026-08-21T12:00:01Z",
            "duration_ms": 1000,
            "attempts": 1,
        },
        "output": {"message": "hello"},
    }
    result.update(overrides)
    return result


def routed_outcome(execution=None, **decision_overrides):
    """A ``ROUTED`` decision plus its execution, both conformant."""
    from ginkgo_runtime.bus import DownstreamOutcome, RouteView

    decision = {
        "outcome": "ROUTED",
        "domain": "system",
        "skill_id": "rk-reference-echo",
        "skill_version": "1.0.0",
        "adapter_id": None,
        "provider": None,
        "routing_reason": "scripted deterministic route",
        "evidence_refs": ("ev-0001",),
    }
    decision.update(decision_overrides)
    return DownstreamOutcome(
        decision=RouteView(**decision),
        execution=execution_result() if execution is None else execution,
    )


@pytest.fixture(scope="session")
def contracts():
    """The frozen contracts, loaded from the repository's pinned snapshot."""
    from ginkgo_runtime.catalog.manifest import load_manifest
    from ginkgo_runtime.contracts import load_frozen_contracts

    return load_frozen_contracts(load_manifest(REPO_ROOT).read)


@pytest.fixture
def downstream():
    """A deterministic stand-in scripted for ``system.echo.execute``."""
    from ginkgo_runtime.bus import DeterministicDownstream

    return DeterministicDownstream(by_capability={"system.echo.execute": routed_outcome()})


@pytest.fixture
def bus(downstream, contracts):
    from ginkgo_runtime.bus import CapabilityBus

    return CapabilityBus(downstream=downstream, contracts=contracts)


# ---------------------------------------------------------------------------
# RK-02 Domain Router fixtures
# ---------------------------------------------------------------------------

#: A descriptor that declares everything RK-02 needs to pin a route.
#:
#: It has to be synthetic. At the pinned commits **not one** admitted record
#: declares `side_effect_class`, and every one is `routing_eligible: false`, so
#: the real corpus cannot exercise a ROUTED outcome at all. That is a finding
#: about the registries, not about the router -- `test_rk02_pinned_corpus.py`
#: asserts it -- and the positive path still has to be tested.
#:
#: It is projected through the real RK-07 pipeline from a real manifest, so this
#: is not a hand-built catalog object: it is the same loader, integrity gate and
#: admission code the pinned corpus goes through.
ROUTABLE_DESCRIPTOR = """
id: rk-reference-echo
kind: deterministic_domain_skill
version: 1.0.0
lifecycle_status: VALIDATED
capability:
  name: system.echo.execute
  contract_version: v1
fallback_policy: none
side_effect_class: local_transform
implementation_binding:
  skill_id: rk-reference-echo
  skill_version: 1.0.0
  tool_id: rk-echo-transform
  adapter_id: null
  adapter_not_applicable_reason: >
    Deterministic in-process transform. No integration boundary exists, so
    inventing an adapter identity would misrepresent the implementation.
  provider: null
  provider_not_applicable_reason: >
    No external or local provider is invoked. The tool is a pure function.
routing:
  discoverable: true
  routing_eligible: true
  production_eligible: false
"""


def routable_catalog(tmp_path, descriptor=None, name="record.yaml"):
    """Build a catalog containing one fully-declared, routable descriptor."""
    from ginkgo_runtime.catalog.build import build_catalog

    root = make_snapshot(
        tmp_path,
        [("features/%s" % name, "skill_descriptor", None, descriptor or ROUTABLE_DESCRIPTOR)],
    )
    return build_catalog(root)


def routing_context(**overrides):
    from ginkgo_runtime.router import RoutingContext

    defaults = {
        "execution_mode": "non_production",
        "decision_id": "dec-0001",
        "resolved_at": "2026-08-21T12:00:00Z",
    }
    defaults.update(overrides)
    return RoutingContext(**defaults)


def domain_router(catalog, contracts, policy=None):
    from ginkgo_runtime.router import DomainRouter, StaticPolicy

    return DomainRouter(
        catalog=catalog,
        policy=policy or StaticPolicy.allowing(),
        contracts=contracts,
    )


@pytest.fixture
def routable(tmp_path, contracts):
    """A router over a one-record routable catalog."""
    return domain_router(routable_catalog(tmp_path), contracts)


# ---------------------------------------------------------------------------
# RK-04 Policy + Lifecycle Gate fixtures
# ---------------------------------------------------------------------------


def gate_record(status="VALIDATED", routing_eligible=None, production_eligible=False, **extra):
    """A catalog record shaped the way RK-07 projects one.

    ``routing_eligible`` defaults to whatever the stage's ceiling permits, so a
    test that only names a stage gets the most permissive record that stage
    allows -- which is what makes a BLOCK attributable to the stage rather than
    to an incidentally restrictive flag.
    """
    from ginkgo_runtime.gate import CEILINGS

    ceiling = CEILINGS.get(status, (False, False))
    record = {
        "record_id": "rk-reference-echo",
        "lifecycle_status": status,
        "fallback_policy": "none",
        "side_effect_class": "local_transform",
        "routing": {
            "discoverable": True,
            "routing_eligible": ceiling[0] if routing_eligible is None else routing_eligible,
            "production_eligible": production_eligible,
        },
    }
    record.update(extra)
    return record


def gate_policy(**overrides):
    from ginkgo_runtime.gate import PolicyContext

    defaults = {
        "policy_id": "rk04-test-policy",
        "policy_version": "1",
        "decision": "ALLOW",
        "snapshot": {"rules": [], "note": "supplied verdict; RK-04 evaluates no business rule"},
    }
    defaults.update(overrides)
    return PolicyContext(**defaults)


def gate_request(record=None, execution_mode="non_production", **overrides):
    from ginkgo_runtime.gate import GateRequest

    fields = {
        "record": gate_record() if record is None else record,
        "execution_mode": execution_mode,
        "policy": gate_policy(),
    }
    fields.update(overrides)
    return GateRequest(**fields)


@pytest.fixture
def gate(contracts):
    """``evaluate`` bound to the pinned contracts."""
    from ginkgo_runtime.gate import evaluate

    def run(request):
        return evaluate(request, contracts)

    return run


# ---------------------------------------------------------------------------
# The reference slice (RK-08A + RK-09 + RK-06 + RK-10)
# ---------------------------------------------------------------------------


def slice_request(**overrides):
    """The canonical ``system.echo.execute`` request."""
    request = capability_request(
        request_id="req-slice-0001",
        correlation_id="corr-slice-0001",
        capability={"name": "system.echo.execute", "contract_version": "v1"},
        input={"message": "hello from the reference slice"},
        idempotency_key="idem-slice-0001",
    )
    request.update(overrides)
    return request


@pytest.fixture
def reference_kernel():
    """The whole kernel, assembled for a certification run."""
    from ginkgo_runtime.slice import build_reference_kernel

    return build_reference_kernel()


@pytest.fixture
def slice_run(reference_kernel):
    """One completed pass through the whole chain."""
    return reference_kernel.submit(slice_request())
