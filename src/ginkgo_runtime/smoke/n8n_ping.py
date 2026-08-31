"""The PING -> PONG live smoke, and the three gates in front of it.

.. code-block:: text

    {"value": "PING"} -> Runtime -> Executor Contract -> N8nExecutor
                      -> N8nHttpTransport -> StdlibHttpClient
                      -> local n8n -> ginkgo-rk08b-ping -> {"value": "PONG"}
                      -> RuntimeExecutionResult v1 -> dispatch evidence

The smoke proves one thing -- that the transport built in RK-08B-B reaches a real
n8n and that what comes back normalises the way the offline tests say it does. It
is not a capability test, not a workflow test and not a Callima test, and the
pinned workflow is three nodes with no credential, no HTTP Request node and no
persistence precisely so that it cannot accidentally become one.

Why the gates are gates and not warnings
----------------------------------------

**Opt-in, by an environment variable that is not set anywhere in this
repository.** ``GINKGO_N8N_LIVE_SMOKE=1``. Not a flag, because a flag is one
typo away from a CI job that dispatches to whatever ``localhost:5678`` happens to
be on a developer's machine, and not a default because there is no correct
default for "send traffic to the operator's automation host".

**An endpoint that is named, never discovered.** There is no probing, no port
scan and no fallback to a conventional address. If the base URL is not supplied,
the smoke stops and *reports the missing pre-condition* rather than guessing --
guessing is how a smoke test ends up talking to production.

**A workflow document that is what the pin says it is.** The committed workflow
is hashed and compared before anything is dispatched. A workflow edited in the
n8n UI is a different workflow, and a smoke that passed against one would be
evidence about something that is not in this repository.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..catalog.build import default_root
from ..catalog.manifest import load_manifest
from ..clock import SystemClock
from ..contracts import load_frozen_contracts
from ..executor import (
    EnvironmentCredentialResolver,
    N8nBinding,
    N8nEndpoint,
    N8nExecutor,
    N8nHttpTransport,
    WorkflowPin,
    build_execution_request,
    dispatch_evidence,
)

__all__ = [
    "OPT_IN_VARIABLE",
    "PING_INPUT",
    "PONG_OUTPUT",
    "SMOKE_BINDING_ID",
    "SMOKE_CORRELATION_ID",
    "SMOKE_IDEMPOTENCY_KEY",
    "SMOKE_TOOL_ID",
    "SMOKE_WORKFLOW_IDENTITY",
    "SMOKE_WORKFLOW_PATH",
    "SMOKE_WORKFLOW_REF",
    "WORKFLOW_ARTIFACT",
    "Precondition",
    "SmokeRefused",
    "build_smoke_executor",
    "check_preconditions",
    "run_live_smoke",
    "smoke_request",
    "workflow_artifact_sha256",
]

#: The opt-in. Set nowhere in this repository, nowhere in CI, and nowhere in any
#: test -- ``tests/test_rk08b_live_smoke.py`` asserts the last of those by
#: running the refusal path with the variable absent and with it set to
#: everything except the one value that opts in.
OPT_IN_VARIABLE = "GINKGO_N8N_LIVE_SMOKE"

#: The base URL, if supplied through the environment rather than on the command
#: line. There is no default and there is no discovery.
BASE_URL_VARIABLE = "GINKGO_N8N_BASE_URL"

#: The governed credential reference, if the operator's instance requires one.
#: Empty means an unauthenticated local webhook, which is the reference
#: deployment. A reference named here resolves through
#: :class:`~ginkgo_runtime.executor.EnvironmentCredentialResolver` at dispatch.
CREDENTIAL_REF_VARIABLE = "GINKGO_N8N_CREDENTIAL_REF"

WORKFLOW_ARTIFACT = ("smoke", "n8n", "ginkgo-rk08b-ping.workflow.json")

SMOKE_WORKFLOW_REF = "wf-ginkgo-rk08b-ping"
SMOKE_WORKFLOW_PATH = "webhook/ginkgo-rk08b-ping"
#: Baked into the workflow's `Set` node and never sent by the transport, so
#: verifying it is a verification rather than an echo.
SMOKE_WORKFLOW_IDENTITY = "ginkgo.rk08b.ping@1"
SMOKE_BINDING_ID = "n8n:ginkgo-rk08b-ping@1"
SMOKE_TOOL_ID = "rk-n8n-ping"

SMOKE_CORRELATION_ID = "corr-rk08b-b-smoke"
SMOKE_IDEMPOTENCY_KEY = "idem-rk08b-b-smoke"

PING_INPUT: Mapping[str, Any] = {"value": "PING"}
PONG_OUTPUT: Mapping[str, Any] = {"value": "PONG"}


class SmokeRefused(RuntimeError):
    """A pre-condition was not satisfied, so nothing was dispatched.

    Distinct from a smoke that ran and failed. This one means the runtime never
    contacted anything, which is the outcome the gates exist to produce.
    """


@dataclass(frozen=True)
class Precondition:
    """One named pre-condition and whether it holds.

    ``detail`` says what to do about it. It never quotes anything read out of the
    environment: an operator who mistyped a token does not need it printed back.

    ``blocking`` separates *"you have not done this yet"* from *"this cannot be
    checked from here"*. Whether the workflow is imported and active in the
    target instance is the second kind -- the only way to find out is to dispatch,
    and a smoke that claimed to have verified it would be claiming to know
    something it does not.
    """

    name: str
    satisfied: bool
    detail: str
    blocking: bool = True

    @property
    def verdict(self) -> str:
        if self.satisfied:
            return "PASS"
        return "MISSING" if self.blocking else "UNVERIFIABLE"

    def as_line(self) -> str:
        return "%s %s -- %s" % (self.verdict, self.name, self.detail)


def workflow_artifact_sha256(root: str | None = None) -> str:
    """The digest of the committed workflow document, read as bytes.

    Bytes, not parsed JSON: the pin is over the file, and a digest computed from
    a re-serialised document would drift with whichever JSON writer produced it.
    """
    path = os.path.join(default_root() if root is None else root, *WORKFLOW_ARTIFACT)
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def check_preconditions(
    environ: Mapping[str, str],
    *,
    base_url: str | None = None,
    root: str | None = None,
) -> tuple[Precondition, ...]:
    """Report every gate, contacting nothing.

    Returns them all rather than stopping at the first failure: an operator who
    has to set three things wants to be told three things once.
    """
    checks: list[Precondition] = []

    opted_in = environ.get(OPT_IN_VARIABLE) == "1"
    checks.append(
        Precondition(
            "opt-in",
            opted_in,
            "set %s=1 to authorise a live dispatch; it is set nowhere in this "
            "repository" % OPT_IN_VARIABLE,
        )
    )

    resolved_url = base_url or environ.get(BASE_URL_VARIABLE) or ""
    endpoint_ok = False
    endpoint_detail = (
        "supply the n8n base URL with --base-url or %s; there is no default and "
        "no discovery, and the smoke will not guess an endpoint" % BASE_URL_VARIABLE
    )
    if resolved_url:
        try:
            N8nEndpoint(base_url=resolved_url)
        except ValueError as refused:
            endpoint_detail = "the supplied base URL is not usable: %s" % refused
        else:
            endpoint_ok = True
            endpoint_detail = "an explicit, credential-free base URL is configured"
    checks.append(Precondition("endpoint", endpoint_ok, endpoint_detail))

    artifact_ok = False
    artifact_detail = "the pinned workflow document is missing from the checkout"
    try:
        digest = workflow_artifact_sha256(root)
    except OSError:
        pass
    else:
        artifact_ok = True
        artifact_detail = "%s sha256=%s" % ("/".join(WORKFLOW_ARTIFACT), digest)
    checks.append(Precondition("workflow-artifact", artifact_ok, artifact_detail))

    reference = environ.get(CREDENTIAL_REF_VARIABLE) or ""
    if not reference:
        credential_check = Precondition(
            "credential-reference",
            True,
            "no credential reference is bound; the pinned workflow needs none and "
            "an unauthenticated local webhook is the reference deployment",
        )
    elif environ.get(EnvironmentCredentialResolver().variable_for(reference)):
        credential_check = Precondition(
            "credential-reference",
            True,
            "%s names a governed reference; its material is read at dispatch, "
            "written into one request header, and never recorded anywhere"
            % CREDENTIAL_REF_VARIABLE,
        )
    else:
        credential_check = Precondition(
            "credential-reference",
            False,
            "%s names a reference that does not resolve; the dispatch fails "
            "closed rather than proceeding unauthenticated"
            % CREDENTIAL_REF_VARIABLE,
        )
    checks.append(credential_check)

    checks.append(
        Precondition(
            "workflow-imported",
            False,
            "cannot be checked from here: whether %s is imported and ACTIVE in the "
            "target instance is only knowable by dispatching. A 404 is reported as "
            "N8N_WORKFLOW_NOT_FOUND and an unpinned identity as "
            "N8N_WORKFLOW_MISMATCH; neither is guessed at" % SMOKE_WORKFLOW_IDENTITY,
            blocking=False,
        )
    )

    return tuple(checks)


def smoke_request() -> dict[str, Any]:
    """The fixed CapabilityRequest the smoke is built from."""
    return {
        "request_id": "req-rk08b-b-smoke",
        "correlation_id": SMOKE_CORRELATION_ID,
        "caller": {
            "actor_id": "rk-08b-b-smoke",
            "actor_type": "API",
            "tenant_id": "tenant-local",
            "workspace_id": "workspace-local",
        },
        "capability": {"name": "system.echo.execute", "contract_version": "v1"},
        "input": dict(PING_INPUT),
        "constraints": {"fallback_policy": "none"},
        "governance": {"approval_context": {}, "external_gate_context": {}},
        "evidence": {"required": True},
        "idempotency_key": SMOKE_IDEMPOTENCY_KEY,
    }


def build_smoke_executor(
    *,
    base_url: str,
    client: Any,
    environ: Mapping[str, str],
    root: str | None = None,
) -> tuple[N8nExecutor, N8nHttpTransport]:
    """Assemble the bridge for the smoke. ``client`` is injected, never built here.

    The HTTP client is a parameter so that this whole function -- pins, binding,
    endpoint, digest check and all -- is exercised by the offline test suite
    against a fake client. The CLI is the only place a real
    :class:`~ginkgo_runtime.executor.n8n_http_client.StdlibHttpClient` is
    constructed, and it is constructed after the gates have passed.
    """
    resolved_root = default_root() if root is None else root
    contracts = load_frozen_contracts(load_manifest(resolved_root).read)

    pin = WorkflowPin(
        workflow_ref=SMOKE_WORKFLOW_REF,
        path=SMOKE_WORKFLOW_PATH,
        declared_identity=SMOKE_WORKFLOW_IDENTITY,
        artifact_sha256=workflow_artifact_sha256(resolved_root),
    )
    transport = N8nHttpTransport(
        endpoint=N8nEndpoint(base_url=base_url),
        client=client,
        pins={SMOKE_WORKFLOW_REF: pin},
        resolver=EnvironmentCredentialResolver(environ=dict(environ)),
    )
    reference = environ.get(CREDENTIAL_REF_VARIABLE) or ""
    binding = N8nBinding(
        binding_id=SMOKE_BINDING_ID,
        workflow_ref=SMOKE_WORKFLOW_REF,
        # One declared path. `ginkgo.output` is the only part of the response the
        # caller ever sees; everything else the engine wrote stays behind the
        # projection, exactly as it does for a recorded observation.
        output_path=("ginkgo", "output"),
        credential_refs=(reference,) if reference else (),
    )
    executor = N8nExecutor(
        contracts=contracts,
        clock=SystemClock(),
        transport=transport,
        bindings={SMOKE_TOOL_ID: binding},
        # The smoke's own independent validator: PONG, and nothing else, is a
        # pass. GL-07 -- the executor's own `succeeded` is not evidence of
        # success, and a live smoke that trusted the engine's status would be
        # proving that n8n can say "success".
        validator=lambda envelope, output: output == dict(PONG_OUTPUT),
        validator_ref="rk08b-b-ping-pong-validator",
    )
    return executor, transport


def run_live_smoke(
    environ: Mapping[str, str],
    *,
    base_url: str | None = None,
    client: Any = None,
    root: str | None = None,
) -> dict[str, Any]:
    """Run the smoke, or refuse. Returns the report; raises :class:`SmokeRefused`.

    ``client`` is injected by the tests and left ``None`` by the CLI, which is
    the only caller that imports
    :mod:`ginkgo_runtime.executor.n8n_http_client` -- and it imports it *here*,
    after the gates, so that a refused smoke has not so much as loaded the module
    that owns the socket.
    """
    failed = [
        check
        for check in check_preconditions(environ, base_url=base_url, root=root)
        if check.blocking and not check.satisfied
    ]
    if failed:
        raise SmokeRefused(
            "the live smoke was refused; nothing was dispatched. Unsatisfied: %s"
            % "; ".join(check.as_line() for check in failed)
        )

    resolved_url = base_url or environ[BASE_URL_VARIABLE]
    if client is None:  # pragma: no cover - exercised only by a live run
        from ..executor.n8n_http_client import StdlibHttpClient

        client = StdlibHttpClient()

    executor, transport = build_smoke_executor(
        base_url=resolved_url, client=client, environ=environ, root=root
    )
    request = smoke_request()
    envelope = build_execution_request(
        execution_id="exec-rk08b-b-smoke",
        request=request,
        decision={"decision_id": "dec-rk08b-b-smoke", "decision_hash": "0" * 64},
        input_fingerprint=None,
    )
    envelope["evidence"]["independent_validation_required"] = True

    result, transitions = executor.execute(
        envelope, tool_id=SMOKE_TOOL_ID, payload=dict(PING_INPUT)
    )

    validated = result.get("independent_validation") or {}
    passed = (
        result["state"] == "succeeded"
        and result.get("output") == dict(PONG_OUTPUT)
        and validated.get("satisfied") is True
    )
    return {
        "passed": passed,
        "evidence": dispatch_evidence(transport.journal, result),
        "output": result.get("output"),
        "state": result["state"],
        "independent_validation": dict(validated),
        "transitions": [
            {"state": t.state, "occurred_at": t.occurred_at, "reason": t.reason}
            for t in transitions
        ],
    }
