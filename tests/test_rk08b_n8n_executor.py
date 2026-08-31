"""RK-08B-A — the n8n executor bridge, offline.

Every test here runs the bridge over a recorded transport. Nothing opens a
socket, nothing names a host, nothing reads a credential value, and the first
test asserts the first of those by execution rather than by reading the module
back.

The recorded documents are shaped like n8n's execution records and were written
by hand. They prove what the bridge does with such a document. They prove
nothing about a live engine — that is RK-08B-B, and it is deliberately not here.
"""

from __future__ import annotations

import json
import socket

import pytest

from ginkgo_runtime.bus.normalization import INDEPENDENT_VALIDATION_ERROR_CODE, normalize
from ginkgo_runtime.clock import SteppingClock
from ginkgo_runtime.executor import (
    N8N_EXECUTOR_REF,
    N8N_STATUS_TO_STATE,
    ExecutionEnvelopeError,
    InProcessExecutor,
    N8nBinding,
    N8nExecutor,
    N8nObservation,
    RecordedTransport,
    build_execution_request,
)

from .conftest import capability_request, routed_outcome

WORKFLOW_REF = "wf-callima-echo-0001"
TOOL_ID = "rk-echo-transform"
CREDENTIAL_REF = "cred-ref-evolution-01"

#: A string that must never survive a round trip through the bridge. It stands
#: in for the credential material and provider payloads that a real n8n
#: execution document routinely carries.
SECRET = "AKIAIOSFODNN7EXAMPLE-super-secret-value"

#: The one message the recorded workflow echoes. Kept as a constant so the
#: recorded output stays consistent with the payload the bridge is handed.
MESSAGE = "hello from the reference slice"

BINDING = N8nBinding(
    binding_id="n8n:rk-reference-echo@1",
    workflow_ref=WORKFLOW_REF,
    output_path=("data", "resultData", "ginkgo", "output"),
    credential_refs=(CREDENTIAL_REF,),
    poll_interval_ms=500,
)


def engine_document(status="success", output=None, error_message=SECRET):
    """An n8n execution document, engine internals and all.

    The internals are present on purpose. A fixture pre-cleaned of ``workflowId``
    and ``runData`` would make the projection and the leak check pass without
    proving anything.
    """
    return {
        "id": "8231",
        "workflowId": "Xp7dQ2eKm9",
        "workflowData": {
            "name": "callima-echo",
            "nodes": [{"name": "Webhook", "credentials": {"httpHeaderAuth": SECRET}}],
        },
        "mode": "trigger",
        "status": status,
        "startedAt": "2026-08-21T12:00:00.000Z",
        "stoppedAt": "2026-08-21T12:00:00.400Z",
        "data": {
            "resultData": {
                "lastNodeExecuted": "Respond",
                "runData": {"Webhook": [{"data": {"main": [[{"json": {"token": SECRET}}]]}}]},
                "error": {"message": error_message} if status in ("error", "crashed") else None,
                # Where the binding declares the normalized output lives.
                "ginkgo": {
                    "output": output
                    if output is not None
                    else {"echoed": MESSAGE, "length": len(MESSAGE), "tool_id": TOOL_ID}
                },
            }
        },
    }


def envelope(request=None, **overrides):
    """A conformant ``RuntimeExecutionRequest v1`` for the echo job."""
    request = capability_request() if request is None else request
    built = build_execution_request(
        execution_id="exec-0001",
        request=request,
        decision={"decision_id": "dec-0001", "decision_hash": "a" * 64},
        input_fingerprint=None,
    )
    built.update(overrides)
    return built


def bridge(contracts, script=None, *, bindings=None, unavailable=(), **kwargs):
    transport = RecordedTransport(
        script=script
        if script is not None
        else {WORKFLOW_REF: [N8nObservation("success", engine_document())]},
        unavailable=frozenset(unavailable),
    )
    executor = N8nExecutor(
        contracts=contracts,
        clock=SteppingClock(),
        transport=transport,
        bindings={TOOL_ID: BINDING} if bindings is None else bindings,
        **kwargs,
    )
    return executor, transport


def run(contracts, script=None, *, payload=None, env=None, **kwargs):
    executor, transport = bridge(contracts, script, **kwargs)
    result, transitions = executor.execute(
        env if env is not None else envelope(),
        tool_id=TOOL_ID,
        payload=payload if payload is not None else {"message": MESSAGE},
    )
    return result, transitions, transport


# ===========================================================================
# Offline by construction
# ===========================================================================


def test_the_bridge_opens_no_socket(contracts, monkeypatch):
    """The A-half is offline, asserted by execution rather than by inspection."""

    def blocked(*args, **kwargs):
        raise AssertionError("the n8n bridge attempted network I/O")

    monkeypatch.setattr(socket, "socket", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    result, _, _ = run(contracts)
    assert result["state"] == "succeeded"


def test_the_bridge_records_its_own_audit_identity(contracts):
    result, _, _ = run(contracts)
    assert result["executor_ref"] == N8N_EXECUTOR_REF
    assert result["executor_ref"] != "rk-in-process-executor"


# ===========================================================================
# The happy path, and what must not come back with it
# ===========================================================================


def test_a_successful_workflow_normalises_to_succeeded(contracts):
    result, transitions, _ = run(contracts)
    assert result["state"] == "succeeded"
    assert result["terminal"] is True
    assert result["error"] is None
    assert result["output"] == {"echoed": MESSAGE, "length": len(MESSAGE), "tool_id": TOOL_ID}
    assert [t.state for t in transitions] == ["queued", "running", "succeeded"]


def test_the_engine_document_never_reaches_the_result(contracts):
    """Invariant 17. The output is projected from one declared path, not forwarded."""
    result, _, _ = run(contracts)
    serialised = json.dumps(result)
    for leaked in (SECRET, "workflowId", "runData", "callima-echo", "lastNodeExecuted"):
        assert leaked not in serialised


def test_the_workflow_reference_never_reaches_the_result(contracts):
    result, _, _ = run(contracts)
    assert WORKFLOW_REF not in json.dumps(result)
    assert BINDING.binding_id not in json.dumps(result)


def test_a_pass_through_binding_fails_closed(contracts):
    """A binding pointed at the engine's own block is a defect, not a scrub."""
    leaky = N8nBinding(
        binding_id=BINDING.binding_id,
        workflow_ref=WORKFLOW_REF,
        output_path=("data", "resultData"),
    )
    result, _, _ = run(contracts, bindings={TOOL_ID: leaky})
    assert result["state"] == "failed"
    assert result["error"]["code"] == "N8N_OUTPUT_LEAK"
    assert result["error"]["category"] == "INTERNAL"
    assert "output" not in result
    assert SECRET not in json.dumps(result)


def test_a_missing_output_path_fails_closed(contracts):
    absent = N8nBinding(
        binding_id=BINDING.binding_id,
        workflow_ref=WORKFLOW_REF,
        output_path=("data", "resultData", "ginkgo", "not_produced"),
    )
    result, _, _ = run(contracts, bindings={TOOL_ID: absent})
    assert result["state"] == "failed"
    assert result["error"]["code"] == "N8N_OUTPUT_MISSING"


# ===========================================================================
# The status vocabulary
# ===========================================================================


@pytest.mark.parametrize(("status", "state"), sorted(N8N_STATUS_TO_STATE.items()))
def test_every_mapped_status_reaches_its_declared_state(contracts, status, state):
    """The four ladders are not interchangeable; this one is mapped explicitly."""
    script = {WORKFLOW_REF: [N8nObservation(status, engine_document(status))]}
    result, _, _ = run(contracts, script)
    # A status that is non-terminal for n8n is non-terminal here too, and the
    # bridge exhausts its budget rather than inventing an outcome.
    expected = "timed_out" if state in ("queued", "running") else state
    assert result["state"] == expected


@pytest.mark.parametrize("status", ["completed", "finished", "ok", "", "UNKNOWN_STATE"])
def test_an_unmapped_status_is_never_read_as_success(contracts, status):
    script = {WORKFLOW_REF: [N8nObservation(status, engine_document())]}
    result, _, _ = run(contracts, script)
    assert result["state"] == "failed"
    assert result["error"]["code"] == "N8N_STATUS_UNKNOWN"
    assert result["error"]["category"] == "INTERNAL"


def test_a_run_that_never_terminates_times_out_within_its_budget(contracts):
    script = {WORKFLOW_REF: [N8nObservation("running", engine_document("running"))]}
    result, transitions, transport = run(contracts, script)
    assert result["state"] == "timed_out"
    assert result["error"]["category"] == "TIMEOUT"
    assert [t.state for t in transitions] == ["queued", "running", "timed_out"]
    # 60000 ms over a 500 ms interval. Bounded, and bounded by the envelope.
    assert len(transport.calls) == 1 + 120


def test_the_poll_budget_comes_from_the_envelope_bound(contracts):
    script = {WORKFLOW_REF: [N8nObservation("waiting", engine_document("waiting"))]}
    env = envelope()
    env["bounds"]["timeout_ms"] = 5000
    result, _, transport = run(contracts, script, env=env)
    assert result["state"] == "timed_out"
    assert len(transport.calls) == 1 + 10


def test_a_transport_that_returns_nothing_is_a_provider_failure(contracts):
    result, _, _ = run(contracts, unavailable=(WORKFLOW_REF,))
    assert result["state"] == "failed"
    assert result["error"]["code"] == "N8N_TRANSPORT_UNAVAILABLE"
    assert result["error"]["category"] == "PROVIDER"
    assert result["error"]["retryable"] is True


def test_the_bridge_never_quotes_the_engines_error_text(contracts):
    """`error.schema.json`: provider payloads must not appear in message or details."""
    script = {WORKFLOW_REF: [N8nObservation("error", engine_document("error"))]}
    result, _, _ = run(contracts, script)
    assert result["state"] == "failed"
    assert result["error"]["code"] == "N8N_WORKFLOW_FAILED"
    assert result["error"]["category"] == "PROVIDER"
    assert SECRET not in json.dumps(result)


def test_cancellation_is_a_state_not_an_error(contracts):
    script = {WORKFLOW_REF: [N8nObservation("canceled", engine_document("canceled"))]}
    result, transitions, _ = run(contracts, script)
    assert result["state"] == "cancelled"
    assert result["terminal"] is True
    assert result["error"] is None
    assert transitions[-1].state == "cancelled"


# ===========================================================================
# The envelope, and what the bridge is allowed to be handed
# ===========================================================================


def test_a_malformed_envelope_is_refused_before_anything_runs(contracts):
    executor, transport = bridge(contracts)
    broken = envelope()
    del broken["correlation_id"]
    with pytest.raises(ExecutionEnvelopeError):
        executor.execute(broken, tool_id=TOOL_ID, payload={"message": MESSAGE})
    assert transport.calls == []


def test_only_credential_references_cross_the_transport_boundary(contracts):
    """Everything the transport was handed, serialised, not just the identifiers.

    The earlier version of this test read ``transport.calls`` and asserted
    ``SECRET not in start``. It proved neither half of its own name. ``calls``
    holds identifiers and never held the ``payload``, which is the only surface a
    credential would actually ride in on; and ``in`` over a tuple compares
    elements, so ``"prefix-" + SECRET`` would have passed it. Both are fixed
    here: the assertion is a substring check over everything that crossed.
    """
    _, _, transport = run(contracts)

    dispatched = transport.dispatched[0]
    assert dispatched["call"] == "start"
    assert dispatched["credential_refs"] == [CREDENTIAL_REF]
    assert SECRET not in json.dumps(transport.dispatched)
    assert CREDENTIAL_REF in json.dumps(transport.dispatched)


def test_a_credential_value_never_becomes_a_credential_reference(contracts):
    """The negative control the assertion above needs to mean anything.

    An absent secret is only evidence if a present one would have been caught.
    Here the binding holds a value where the contract requires an identifier, and
    the boundary refuses it before the transport is called at all — so the clean
    dispatch above is a boundary holding, not a fixture that never had a secret
    in it.
    """
    from ginkgo_runtime.executor import CredentialBoundaryError

    executor, transport = bridge(
        contracts,
        bindings={
            TOOL_ID: N8nBinding(
                binding_id=BINDING.binding_id,
                workflow_ref=WORKFLOW_REF,
                output_path=BINDING.output_path,
                credential_refs=(SECRET,),
            )
        },
    )
    with pytest.raises(CredentialBoundaryError) as excinfo:
        executor.execute(envelope(), tool_id=TOOL_ID, payload={"message": MESSAGE})

    assert transport.calls == []
    assert transport.dispatched == []
    assert SECRET not in str(excinfo.value)


def test_the_envelope_carries_no_engine_identity(contracts):
    """The ban is structural: the frozen envelope has nowhere to put one."""
    built = envelope()
    forbidden = contracts.runtime_execution_request.schema["x-ginkgo-forbidden-fields"]
    for name in forbidden:
        assert name not in built
    assert WORKFLOW_REF not in json.dumps(built)


def test_an_unbound_tool_reports_the_same_condition_as_the_in_process_executor(contracts):
    """Two executors must not name one condition two different ways."""
    n8n_result, _, _ = run(contracts, bindings={})
    in_process = InProcessExecutor(contracts=contracts, clock=SteppingClock(), tools={})
    stub_result, _ = in_process.execute(
        envelope(), tool_id=TOOL_ID, payload={"message": MESSAGE}
    )
    assert n8n_result["error"]["code"] == stub_result["error"]["code"] == "TOOL_NOT_REGISTERED"
    assert n8n_result["state"] == stub_result["state"] == "failed"


# ===========================================================================
# Independent validation — GL-07, generalised
# ===========================================================================


def test_an_unvalidated_success_is_not_reported_as_success(contracts):
    """The executor's own `succeeded` is not sufficient evidence of success."""
    env = envelope()
    env["evidence"]["independent_validation_required"] = True
    result, _, _ = run(contracts, env=env)
    assert result["state"] == "succeeded"
    # No `validator_ref`: none ran, and naming one that did not run would be a
    # claim about evidence that does not exist.
    assert result["independent_validation"] == {"required": True, "satisfied": None}

    normalized, failure = normalize(
        capability_request(), routed_outcome(execution=result), contracts
    )
    assert failure is None
    assert normalized["status"] == "ERROR"
    assert normalized["error"]["code"] == INDEPENDENT_VALIDATION_ERROR_CODE


def test_an_injected_validator_can_satisfy_the_requirement(contracts):
    env = envelope()
    env["evidence"]["independent_validation_required"] = True
    result, _, _ = run(
        contracts,
        env=env,
        validator=lambda envelope_, output: output.get("length") == len(MESSAGE),
        validator_ref="rk-echo-length-validator",
    )
    assert result["independent_validation"] == {
        "required": True,
        "satisfied": True,
        "validator_ref": "rk-echo-length-validator",
    }
    normalized, failure = normalize(
        capability_request(), routed_outcome(execution=result), contracts
    )
    assert failure is None
    assert normalized["status"] == "SUCCESS"


# ===========================================================================
# Offline by construction, checked statically
# ===========================================================================


def test_the_bridge_imports_no_network_module():
    """The A-half cannot reach a network, and it cannot acquire the ability to.

    Blocking sockets at run time proves this run made no call. This proves the
    stronger thing: the module never imports anything that could, so a future
    edit that reaches for one has to add the import and fail here first.
    """
    import ast
    import pathlib

    import ginkgo_runtime.executor.n8n as module

    tree = ast.parse(pathlib.Path(module.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])

    assert not imported & {
        "socket",
        "ssl",
        "http",
        "urllib",
        "requests",
        "httpx",
        "asyncio",
        "subprocess",
        "os",
    }
