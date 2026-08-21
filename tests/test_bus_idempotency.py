"""Request identity and idempotency -- reference slice P7 and N14.

``idempotency_key`` makes one promise: retrying is safe. Proving that needs both
halves, and the second half is the one that is usually missing --

* **P7** same key, same payload, same result, and **no second dispatch**;
* **N14** same key, different payload, ``CONFLICT`` -- neither replayed nor run.
"""

from __future__ import annotations

import pytest

from ginkgo_runtime.bus import IdempotencyLedger, identify
from ginkgo_runtime.bus.identity import FINGERPRINT_FIELDS, VOLATILE_FIELDS

from .conftest import capability_request


def test_p7_a_replay_returns_the_same_result(bus, downstream):
    """P7, first half."""
    first = bus.submit(capability_request())
    second = bus.submit(capability_request())

    assert first.accepted and second.accepted
    assert dict(second.result) == dict(first.result)
    assert second.replayed is True
    assert first.replayed is False


def test_p7_a_replay_never_reaches_the_downstream(bus, downstream):
    """P7, second half -- and the half that actually matters.

    Returning the right bytes while executing twice is exactly the failure the
    key exists to prevent, and it is invisible if you only compare results. The
    stand-in counts its calls, so the second submission proves nothing ran.

    RK-06 is not implemented here, but this is what "writes no second evidence
    record" reduces to at the RK-01 boundary: nothing downstream was invoked, so
    nothing downstream could have written anything.
    """
    bus.submit(capability_request())
    assert downstream.call_count == 1

    for _ in range(5):
        bus.submit(capability_request())
    assert downstream.call_count == 1


def test_a_retry_may_carry_a_new_request_id(bus, downstream):
    """The whole reason ``request_id`` is outside the fingerprint.

    A retry is a *new request* for the *same operation*, so it legitimately
    carries a new ``request_id``. Hashing it would make every retry a payload
    mismatch: every retry would return CONFLICT and the mechanism would protect
    nothing while appearing to work.
    """
    first = bus.submit(capability_request(request_id="req-A"))
    second = bus.submit(capability_request(request_id="req-B"))

    assert second.replayed is True
    assert downstream.call_count == 1
    # The replayed result is the FIRST one, verbatim. It is not re-stamped with
    # the retry's request_id -- the caller is being handed the original answer.
    assert second.result["request_id"] == "req-A"
    assert dict(second.result) == dict(first.result)


@pytest.mark.parametrize("volatile", ["request_id", "correlation_id"])
def test_volatile_fields_are_outside_the_fingerprint(volatile):
    baseline = identify(capability_request())
    changed = identify(capability_request(**{volatile: "changed-value"}))
    assert changed.payload_fingerprint == baseline.payload_fingerprint


@pytest.mark.parametrize("semantic", ["input", "constraints", "governance", "evidence"])
def test_semantic_fields_are_inside_the_fingerprint(semantic):
    baseline = identify(capability_request())
    altered = {
        "input": {"message": "different"},
        "constraints": {"fallback_policy": "same_semantics_only"},
        "governance": {
            "approval_context": {"approval_ids": ["a-1"]},
            "external_gate_context": {},
        },
        "evidence": {"required": False},
    }[semantic]
    changed = identify(capability_request(**{semantic: altered}))
    assert changed.payload_fingerprint != baseline.payload_fingerprint


def test_every_field_of_the_frozen_request_is_classified(contracts):
    """A field is either part of the payload or volatile. Never both, never neither.

    Checked against the frozen schema's full property set rather than against a
    fixture, so an optional field nobody sends -- ``task_id``, ``execution_id`` --
    cannot sit unclassified until the day someone sends it and silently changes
    what "the same request" means.
    """
    assert set(FINGERPRINT_FIELDS).isdisjoint(VOLATILE_FIELDS)
    declared = set(contracts.capability_request.schema["properties"])
    assert set(FINGERPRINT_FIELDS) | set(VOLATILE_FIELDS) == declared


def test_n14_a_conflicting_payload_is_rejected(bus, downstream):
    """N14."""
    bus.submit(capability_request())
    conflict = bus.submit(capability_request(input={"message": "something else"}))

    assert not conflict.accepted
    assert conflict.error["code"] == "IDEMPOTENCY_CONFLICT"
    assert conflict.error["category"] == "CONFLICT"


def test_n14_a_conflict_neither_replays_nor_executes(bus, downstream):
    """The fail-closed half: the old result is withheld and the new one does not run."""
    bus.submit(capability_request())
    conflict = bus.submit(capability_request(input={"message": "something else"}))

    assert conflict.result is None
    assert downstream.call_count == 1


def test_a_conflict_does_not_disturb_the_stored_result(bus, downstream):
    """A rejected conflict must not overwrite what the key already promised."""
    first = bus.submit(capability_request())
    bus.submit(capability_request(input={"message": "something else"}))
    again = bus.submit(capability_request())

    assert dict(again.result) == dict(first.result)
    assert again.replayed is True
    assert downstream.call_count == 1


def test_the_ledger_is_scoped_per_workspace(bus, downstream):
    """An unscoped key space would leak a result across a tenant boundary.

    The same key, the same payload, a different workspace: a different operation,
    dispatched on its own.
    """
    bus.submit(capability_request())
    other = bus.submit(
        capability_request(
            caller={
                "actor_id": "actor-1",
                "actor_type": "API",
                "tenant_id": "tenant-1",
                "workspace_id": "workspace-2",
            }
        )
    )
    assert other.accepted
    assert other.replayed is False
    assert downstream.call_count == 2


def test_a_different_actor_reusing_a_key_is_a_conflict(bus, downstream):
    """Within one workspace, that is a collision rather than a retry."""
    bus.submit(capability_request())
    other = bus.submit(
        capability_request(
            caller={
                "actor_id": "actor-2",
                "actor_type": "API",
                "tenant_id": "tenant-1",
                "workspace_id": "workspace-1",
            }
        )
    )
    assert other.error["code"] == "IDEMPOTENCY_CONFLICT"
    assert downstream.call_count == 1


def test_a_rejected_request_claims_no_key(bus, downstream):
    """Admission runs before identity, so an inadmissible request never takes a key.

    Otherwise a malformed request could burn a caller's key and turn every
    subsequent legitimate retry into a CONFLICT.
    """
    bad = bus.submit(capability_request(constraints={"fallback_policy": "explicit"}))
    assert not bad.accepted

    good = bus.submit(capability_request())
    assert good.accepted
    assert good.replayed is False
    assert downstream.call_count == 1


def test_the_ledger_never_overwrites_a_key():
    """A key is written once. A second write would silently change what a retry returns."""
    ledger = IdempotencyLedger()
    identity = identify(capability_request())

    ledger.remember(identity, {"status": "SUCCESS", "marker": "first"})
    ledger.remember(identity, {"status": "ERROR", "marker": "second"})

    assert ledger.lookup(identity).result["marker"] == "first"
    assert len(ledger) == 1


def test_a_float_in_business_input_does_not_crash_the_boundary(bus, downstream):
    """Adversarial-review regression. ``{"price": 19.99}`` is an ordinary request."""
    first = bus.submit(capability_request(input={"price": 19.99, "currency": "BRL"}))
    assert first.accepted

    replay = bus.submit(
        capability_request(request_id="req-B", input={"price": 19.99, "currency": "BRL"})
    )
    assert replay.replayed is True
    assert downstream.call_count == 1

    changed = bus.submit(
        capability_request(request_id="req-C", input={"price": 19.98, "currency": "BRL"})
    )
    assert changed.error["code"] == "IDEMPOTENCY_CONFLICT"
