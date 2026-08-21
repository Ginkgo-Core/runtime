"""Reproducibility: same input, same catalog, same policy — same ``decision_hash``.

`ai-stack#40`'s Done-when: *"`RuntimeRouteDecision` is emitted with a reproducible
`decision_hash` (identical inputs ⇒ identical hash)"*.

Three hashes answer three questions, and the tests keep them separate:
``input_fingerprint`` is *what was asked about*, ``decision_inputs_hash`` is
*what question was asked*, ``decision_hash`` is *what answer was given*.
"""

from __future__ import annotations

import pytest

from ginkgo_runtime.router import ALGORITHM_VERSION, VOLATILE_DECISION_FIELDS, StaticPolicy
from ginkgo_runtime.router.decision import compute_decision_hash

from .conftest import (
    ROUTABLE_DESCRIPTOR,
    capability_request,
    domain_router,
    routable_catalog,
    routing_context,
)


def test_two_identical_routings_produce_the_same_decision_hash(routable):
    first = routable.route(capability_request(), routing_context())
    second = routable.route(capability_request(), routing_context())
    assert first == second


def test_the_hash_survives_a_different_request_and_decision_identity(routable):
    """The property the requirement actually asks for.

    ``decision_id``, ``resolved_at``, ``request_id`` and ``correlation_id`` differ
    on every call by construction. If they were hashed, ``decision_hash`` would be
    a unique id -- it would still validate, still look reproducible, and never
    once be equal for two identical routings.
    """
    first = routable.route(
        capability_request(request_id="req-A", correlation_id="corr-A"),
        routing_context(decision_id="dec-A", resolved_at="2026-08-21T12:00:00Z"),
    )
    second = routable.route(
        capability_request(request_id="req-B", correlation_id="corr-B"),
        routing_context(decision_id="dec-B", resolved_at="2027-01-01T00:00:00Z"),
    )

    assert first["decision_hash"] == second["decision_hash"]
    assert first["decision_inputs_hash"] == second["decision_inputs_hash"]
    assert first["decision_id"] != second["decision_id"]


def test_every_volatile_field_is_genuinely_outside_the_hash(routable):
    decision = routable.route(capability_request(), routing_context())
    baseline = decision["decision_hash"]

    for field in VOLATILE_DECISION_FIELDS:
        if field == "decision_hash":
            continue
        mutated = dict(decision)
        mutated[field] = "something-else-entirely"
        assert compute_decision_hash(mutated) == baseline, field


def test_the_hash_is_stable_across_cold_processes(tmp_path):
    """An in-process repeat can hide state that survives between calls."""
    import json
    import subprocess
    import sys

    from .conftest import REPO_ROOT, make_snapshot

    root = make_snapshot(
        tmp_path, [("features/one.yaml", "skill_descriptor", None, ROUTABLE_DESCRIPTOR)]
    )
    script = (
        "import json,sys;"
        "sys.path.insert(0, %r); sys.path.insert(0, %r);"
        "from tests.conftest import capability_request, routing_context, domain_router;"
        "from ginkgo_runtime.catalog.build import build_catalog;"
        "from ginkgo_runtime.catalog.manifest import load_manifest;"
        "from ginkgo_runtime.contracts import load_frozen_contracts;"
        "c = load_frozen_contracts(load_manifest(%r).read);"
        "r = domain_router(build_catalog(%r), c);"
        "print(r.route(capability_request(), routing_context())['decision_hash'])"
        % (
            __import__("os").path.join(REPO_ROOT, "src"),
            REPO_ROOT,
            REPO_ROOT,
            root,
        )
    )
    runs = [
        subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, check=True
        ).stdout.strip()
        for _ in range(2)
    ]
    assert runs[0] == runs[1]
    assert len(runs[0]) == 64
    assert json  # keeps the import honest under lint


@pytest.mark.parametrize(
    "change",
    [
        {"input": {"message": "different"}},
        {"constraints": {"fallback_policy": "same_semantics_only"}},
        {"capability": {"name": "system.echo.execute", "contract_version": "v1"}},
    ],
)
def test_a_changed_request_changes_the_hash_unless_nothing_changed(routable, change):
    baseline = routable.route(capability_request(), routing_context())["decision_hash"]
    changed = routable.route(capability_request(**change), routing_context())["decision_hash"]

    unchanged = change.get("capability", {}).get("name") == "system.echo.execute" and set(
        change
    ) == {"capability"}
    assert (changed == baseline) is unchanged


def test_a_different_execution_mode_changes_the_hash(tmp_path, contracts):
    descriptor = ROUTABLE_DESCRIPTOR.replace(
        "lifecycle_status: VALIDATED", "lifecycle_status: PRODUCTION"
    ).replace("production_eligible: false", "production_eligible: true")
    router = domain_router(routable_catalog(tmp_path, descriptor), contracts)

    non_production = router.route(capability_request(), routing_context())
    production = router.route(
        capability_request(), routing_context(execution_mode="production")
    )
    assert non_production["decision_hash"] != production["decision_hash"]
    assert non_production["decision_inputs_hash"] != production["decision_inputs_hash"]


def test_a_different_catalog_changes_the_hash(tmp_path, contracts):
    """The catalog is part of the question: a different projection is a different answer."""
    first = domain_router(routable_catalog(tmp_path / "a"), contracts).route(
        capability_request(), routing_context()
    )
    altered = ROUTABLE_DESCRIPTOR.replace("skill_version: 1.0.0", "skill_version: 1.0.1")
    second = domain_router(routable_catalog(tmp_path / "b", altered), contracts).route(
        capability_request(), routing_context()
    )

    assert first["catalog"]["catalog_version"] != second["catalog"]["catalog_version"]
    assert first["decision_hash"] != second["decision_hash"]
    assert first["decision_inputs_hash"] != second["decision_inputs_hash"]


def test_a_different_policy_snapshot_changes_the_hash(tmp_path, contracts):
    catalog = routable_catalog(tmp_path)
    first = domain_router(
        catalog, contracts, StaticPolicy.allowing(snapshot={"rules": ["a"]})
    ).route(capability_request(), routing_context())
    second = domain_router(
        catalog, contracts, StaticPolicy.allowing(snapshot={"rules": ["b"]})
    ).route(capability_request(), routing_context())

    assert first["policy"]["policy_snapshot_hash"] != second["policy"]["policy_snapshot_hash"]
    assert first["decision_hash"] != second["decision_hash"]
    assert first["decision_inputs_hash"] != second["decision_inputs_hash"]


def test_the_pinned_commits_are_recorded_on_every_decision(routable):
    """*"This is what makes a routing decision reproducible after a restart."*"""
    decision = routable.route(capability_request(), routing_context())
    refs = decision["catalog"]["pinned_refs"]

    assert refs
    for ref in refs:
        assert len(ref["commit"]) == 40
        assert all(c in "0123456789abcdef" for c in ref["commit"])
    assert len({ref["repository"] for ref in refs}) == len(refs)


def test_the_algorithm_version_is_part_of_the_question(routable):
    """A silent change in how routing resolves cannot reuse an old hash."""
    decision = routable.route(capability_request(), routing_context())
    assert decision["algorithm_version"] == ALGORITHM_VERSION


def test_the_input_fingerprint_never_carries_the_input(routable):
    """*"SHA-256 over canonical JSON of the normalized business input. Never the input itself."*"""
    decision = routable.route(
        capability_request(input={"secret_order_note": "sensitive-business-text"}),
        routing_context(),
    )
    assert len(decision["input_fingerprint"]) == 64
    assert "sensitive-business-text" not in repr(decision)


def test_the_input_fingerprint_tolerates_business_floats(routable):
    """Prices are ordinary business input, and the router must not crash on one."""
    decision = routable.route(
        capability_request(input={"price": 19.99}), routing_context()
    )
    assert decision["resolved"]["outcome"] == "ROUTED"
    assert len(decision["input_fingerprint"]) == 64
