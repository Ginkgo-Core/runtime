"""RK-08B's promotion gate, quoted exactly and executed.

`executor-contract.md` section 6, adopted verbatim from `ai-stack#32`:

    same TaskOps job executed by n8n and a stub executor with equivalent
    normalized result

So that is what runs here. One envelope, one payload, two executors, and then the
question the gate actually asks: **can the caller tell which one ran?**

The answer has to be no for the success path, and the assertion is not a summary
of the two results — it is the two results, compared whole. Anything the two
executors are entitled to differ on is named explicitly and nothing else is
tolerated, because a comparison with a generous exclusion list proves whatever it
excludes.

What they are entitled to differ on:

* ``executor_ref`` — the audit identity, which exists precisely to record which
  executor ran. If it matched, the audit trail would be the thing that broke.
* the *reasons* on their transitions — an audit trail that could not distinguish
  "accepted by the in-process executor" from "accepted by the n8n executor
  bridge" would be worse, not better.

Everything else — state, terminality, output, error, telemetry, decision
reference, idempotency, dead-lettering, independent validation — is compared as
one document, and the ``CapabilityResult`` the Bus builds from each is compared
whole with no exclusions at all.
"""

from __future__ import annotations

import pytest

from ginkgo_runtime.bus.normalization import caller_projection, normalize
from ginkgo_runtime.clock import SteppingClock
from ginkgo_runtime.executor import (
    IN_PROCESS_EXECUTOR_REF,
    N8N_EXECUTOR_REF,
    InProcessExecutor,
    N8nExecutor,
    N8nObservation,
    RecordedTransport,
)

from .conftest import capability_request, routed_outcome
from .test_rk08b_n8n_executor import (
    BINDING,
    MESSAGE,
    TOOL_ID,
    WORKFLOW_REF,
    engine_document,
    envelope,
)

JOB_PAYLOAD = {"message": MESSAGE}

#: What a workflow that implements this skill returns. Written out rather than
#: obtained by calling the reference tool: a recording generated from the thing
#: it is compared against would make the gate pass by construction.
RECORDED_OUTPUT = {"echoed": MESSAGE, "length": len(MESSAGE), "tool_id": TOOL_ID}


def both_executors(contracts, *, script=None, tools=None, bindings=None):
    """The same job, run by each executor, each with its own fresh clock.

    Fresh clocks rather than one shared clock: sharing would let the second run
    read timestamps the first advanced, and the two results would differ for a
    reason that has nothing to do with either executor.
    """
    stub = InProcessExecutor(contracts=contracts, clock=SteppingClock())
    if tools is not None:
        stub.tools = tools
    n8n = N8nExecutor(
        contracts=contracts,
        clock=SteppingClock(),
        transport=RecordedTransport(
            script=script
            if script is not None
            else {
                WORKFLOW_REF: [
                    N8nObservation("success", engine_document(output=RECORDED_OUTPUT))
                ]
            }
        ),
        bindings={TOOL_ID: BINDING} if bindings is None else bindings,
    )
    env = envelope()
    stub_result, stub_transitions = stub.execute(env, tool_id=TOOL_ID, payload=JOB_PAYLOAD)
    n8n_result, n8n_transitions = n8n.execute(env, tool_id=TOOL_ID, payload=JOB_PAYLOAD)
    return (stub_result, stub_transitions), (n8n_result, n8n_transitions)


def test_the_promotion_gate_one_job_two_executors_one_result(contracts):
    """The gate itself. Everything but the audit identity is identical."""
    (stub_result, _), (n8n_result, _) = both_executors(contracts)

    assert stub_result["executor_ref"] == IN_PROCESS_EXECUTOR_REF
    assert n8n_result["executor_ref"] == N8N_EXECUTOR_REF

    assert {k: v for k, v in stub_result.items() if k != "executor_ref"} == {
        k: v for k, v in n8n_result.items() if k != "executor_ref"
    }


def test_the_caller_cannot_tell_which_executor_ran(contracts):
    """The stronger claim, and the one invariant 17 actually cares about.

    ``CapabilityResult v1`` has no field for an executor at all, so the two
    normalized results are compared with **no** exclusions.
    """
    (stub_result, _), (n8n_result, _) = both_executors(contracts)
    request = capability_request()

    stub_view, stub_failure = normalize(request, routed_outcome(execution=stub_result), contracts)
    n8n_view, n8n_failure = normalize(request, routed_outcome(execution=n8n_result), contracts)

    assert stub_failure is None and n8n_failure is None
    assert stub_view["status"] == "SUCCESS"
    assert stub_view == n8n_view
    assert caller_projection(stub_view) == caller_projection(n8n_view)


def test_both_executors_walk_the_same_state_ladder(contracts):
    """Same states, in the same order — and deliberately different reasons."""
    (_, stub_transitions), (_, n8n_transitions) = both_executors(contracts)

    assert [t.state for t in stub_transitions] == [t.state for t in n8n_transitions]
    assert [t.occurred_at for t in stub_transitions] == [t.occurred_at for t in n8n_transitions]
    assert [t.reason for t in stub_transitions] != [t.reason for t in n8n_transitions]


def test_equivalence_holds_on_a_failure_both_can_reach(contracts):
    """A gate proved only on the happy path is a gate proved on half the machine."""
    (stub_result, _), (n8n_result, _) = both_executors(contracts, tools={}, bindings={})

    assert stub_result["state"] == n8n_result["state"] == "failed"
    assert {k: v for k, v in stub_result.items() if k != "executor_ref"} == {
        k: v for k, v in n8n_result.items() if k != "executor_ref"
    }


def test_the_gate_is_falsifiable(contracts):
    """A workflow that returns something else must break the equivalence.

    Without this, the gate would pass for a bridge that ignored the engine
    entirely and returned a constant.
    """
    drifted = {
        WORKFLOW_REF: [N8nObservation("success", engine_document(output={"echoed": "no"}))]
    }
    (stub_result, _), (n8n_result, _) = both_executors(contracts, script=drifted)

    assert stub_result["output"] != n8n_result["output"]
    assert {k: v for k, v in stub_result.items() if k != "executor_ref"} != {
        k: v for k, v in n8n_result.items() if k != "executor_ref"
    }


@pytest.mark.parametrize("field", ["state", "terminal", "output", "telemetry", "decision_ref"])
def test_the_fields_the_gate_depends_on_are_actually_present(contracts, field):
    """Guards the comparison itself: an absent field compares equal to nothing."""
    (stub_result, _), (n8n_result, _) = both_executors(contracts)
    assert field in stub_result
    assert field in n8n_result
