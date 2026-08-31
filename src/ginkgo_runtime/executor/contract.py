"""RK-08A -- the Executor Contract, and the minimal in-process executor.

The Executor Router **selects**; the Executor Contract **normalizes**. This is
only the second half. No executor is selected here, and the kernel ships exactly
one implementation behind the contract -- an in-process one, so the reference
slice can run with zero containers, zero network and zero credentials.

.. code-block:: text

    RuntimeExecutionRequest v1  ->  [executor]  ->  RuntimeExecutionResult v1

The executor receives a **reference** to the routing decision, not routing
authority: *"It may not re-resolve, substitute or override the decision."* That
is enforced structurally -- ``decision_ref`` carries an id and a hash and nothing
resolvable, so there is no decision here to override.

Normalized terminal states, adopted as written rather than varied:

.. code-block:: text

    queued | running | succeeded | failed | cancelled | timed_out

``queued`` and ``running`` are non-terminal and emit no ``CapabilityResult``.
RK-01 owns the mapping onto the frozen result status, and this executor never
performs it -- ``RuntimeExecutionResult`` has no ``status`` field to put it in.

Every result is credential-scanned before it exists
--------------------------------------------------

``build_execution_result`` is the single place any executor in this kernel builds
a ``RuntimeExecutionResult v1``, which makes it the single place the outbound
credential boundary has to hold. A result carrying credential material -- in its
output, its error, its artifacts or any diagnostic string on it -- is not
emitted at all: :func:`~ginkgo_runtime.executor.credentials.assert_no_credentials`
raises before the document is returned, so there is no scrubbed result, no
partial result and nothing for a downstream to normalise. The rule and the
reasoning live in :mod:`ginkgo_runtime.executor.credentials`.

What is deliberately not here
-----------------------------

**No n8n in this module, and no live one anywhere.** RK-08B-A adds a second
implementation behind this same contract, in :mod:`ginkgo_runtime.executor.n8n`,
and it is offline: its transport is a port and the kernel ships only a recorded
one. Being reachable grants n8n no authority -- the Executor Router selects, this
contract normalizes, and the bridge is one implementation behind it (invariant
11). Queue mode, encryption-key parity and cross-process teardown are RK-08B-B
and are proven by measurement, not by this module.

**No retries.** ``retry_policy.max_attempts`` is carried in the envelope and set
to 1 for the slice. A retry requires *both* a retryable failure class *and* a
materially different next attempt -- and the schema enforces the second half by
requiring ``materially_different_reason`` past attempt 1. A deterministic pure
function has no materially different second attempt by construction, so retrying
it would be a loop with a nicer name.

**No cancellation and no lease.** Both fields are marked ``RESERVED - no v1
consumer`` in the frozen schema: RK-09 is in-process and its teardown is provable
by assertion.

**No wall-clock timeout.** ``bounds.timeout_ms`` is carried and recorded, and the
executor does not enforce it by racing a clock, because it has no clock -- time
is an injected port. A pure in-process transform cannot exceed a bound it never
waits on. When RK-08B introduces a cross-process executor, enforcement belongs
there, where there is something to interrupt.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from ..clock import RuntimeClock
from ..contracts import FrozenContracts
from .credentials import CredentialBoundaryError, assert_no_credentials
from .echo import TOOL_ID, EchoInputError, echo_transform

__all__ = [
    "DEFAULT_TIMEOUT_MS",
    "IN_PROCESS_EXECUTOR_REF",
    "NON_TERMINAL_STATES",
    "RESULT_REFERENCE_FIELDS",
    "TERMINAL_STATES",
    "TOOLS",
    "CredentialBoundaryError",
    "ExecutionEnvelopeError",
    "Executor",
    "InProcessExecutor",
    "Transition",
    "build_execution_request",
    "build_execution_result",
]

REQUEST_SCHEMA_VERSION = "ginkgo.runtime-execution-request.v1"
RESULT_SCHEMA_VERSION = "ginkgo.runtime-execution-result.v1"

#: ``bounds.timeout_ms`` default, from the frozen schema's own ``default``.
DEFAULT_TIMEOUT_MS = 60000

TERMINAL_STATES = ("succeeded", "failed", "cancelled", "timed_out")
NON_TERMINAL_STATES = ("queued", "running")

#: Audit-only identity of the in-process executor. Recorded on the result,
#: separately from any provider reference, and never caller-visible.
IN_PROCESS_EXECUTOR_REF = "rk-in-process-executor"

#: Fields on ``RuntimeExecutionResult v1`` whose *name* is credential-shaped but
#: whose declared content is an identifier, not material.
#:
#: Empty, and asserted rather than omitted: the frozen result declares no
#: credential-bearing field at all, so the outbound scan has nothing to exempt.
#: The constant exists so that adding an exemption is a visible edit here rather
#: than a quiet argument at a call site.
RESULT_REFERENCE_FIELDS: tuple[str, ...] = ()

#: The tool registry. One entry, and adding a second is a review, not a config
#: change: ``system.*`` is a reserved kernel-diagnostics namespace and every
#: business tool lives behind an adapter instead.
TOOLS: Mapping[str, Callable[[Mapping[str, Any]], dict[str, Any]]] = {
    TOOL_ID: echo_transform,
}


class ExecutionEnvelopeError(RuntimeError):
    """The envelope handed to the executor is not a ``RuntimeExecutionRequest v1``.

    Raised rather than returned as a ``failed`` execution: a malformed envelope
    means the *caller of the executor* is broken, and reporting it as an
    execution failure would blame the skill for the kernel's defect.
    """


@dataclass(frozen=True)
class Transition:
    """One state change, with the timestamp and the reason it happened.

    Verbatim from `ai-stack#7`, quoted by `#43`: *"Nenhum status deve existir
    apenas dentro de uma execução n8n. Toda transição precisa ser persistida com
    timestamp, ator e motivo."* The actor is added by RK-06 from the request.
    """

    state: str
    occurred_at: str
    reason: str


class Executor(Protocol):
    """What the kernel requires of anything that runs a skill.

    ``tool_id`` is a separate argument rather than an envelope field, and that is
    the load-bearing part: ``RuntimeExecutionRequest v1`` carries no tool, engine,
    workflow or endpoint field, so implementation selection cannot ride in the
    envelope even by accident. The executor is handed what to run.

    This signature is also the substitutability claim RK-08B's promotion gate
    rests on -- *"same TaskOps job executed by n8n and a stub executor with
    equivalent normalized result"*. Two implementations that satisfy one Protocol
    can be swapped in a test; two that merely resemble each other cannot.
    """

    def execute(
        self, envelope: Mapping[str, Any], *, tool_id: str, payload: Mapping[str, Any]
    ) -> tuple[Mapping[str, Any], tuple[Transition, ...]]:
        """``RuntimeExecutionRequest v1`` in; the result and its transitions out."""
        ...


def build_execution_request(
    *,
    execution_id: str,
    request: Mapping[str, Any],
    decision: Mapping[str, Any],
    input_fingerprint: str | None,
    deadline: str | None = None,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    max_cost: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the envelope from the admitted request and the frozen decision.

    Everything here is either carried from the request or referenced from the
    decision. Nothing is chosen: the executor is handed what to run, never asked
    which thing to run.
    """
    evidence = request.get("evidence") or {}
    envelope: dict[str, Any] = {
        "schema_version": REQUEST_SCHEMA_VERSION,
        "execution_id": execution_id,
        "request_id": request["request_id"],
        "correlation_id": request["correlation_id"],
        "idempotency_key": request["idempotency_key"],
        "decision_ref": {
            "decision_id": decision["decision_id"],
            "decision_hash": decision["decision_hash"],
        },
        "attempt": {"attempt_number": 1},
        "bounds": {
            "deadline": deadline,
            "timeout_ms": timeout_ms,
            "max_cost": dict(max_cost) if max_cost else None,
        },
        # One attempt. A pure deterministic transform has no materially
        # different second attempt, and the schema requires a reason for one.
        "retry_policy": {
            "max_attempts": 1,
            "backoff": {"strategy": "none"},
            "retryable_categories": [],
        },
        # Empty, and asserted rather than omitted. The reference slice runs with
        # zero credentials, and an absent field would look like an oversight.
        "credential_refs": [],
        "input_fingerprint": input_fingerprint,
        "evidence": {
            "required": bool(evidence.get("required", True)),
            "independent_validation_required": False,
        },
    }
    if request.get("task_id"):
        envelope["task_id"] = request["task_id"]
    return envelope


@dataclass
class InProcessExecutor:
    """Runs one tool in this process. No container, no socket, no credential."""

    contracts: FrozenContracts
    clock: RuntimeClock
    tools: Mapping[str, Callable[[Mapping[str, Any]], dict[str, Any]]] = field(
        default_factory=lambda: dict(TOOLS)
    )

    def execute(
        self, envelope: Mapping[str, Any], *, tool_id: str, payload: Mapping[str, Any]
    ) -> tuple[dict[str, Any], tuple[Transition, ...]]:
        """Run ``tool_id`` over ``payload`` and normalise the outcome.

        ``tool_id`` comes from the routing decision, not from the envelope and
        never from the caller: ``RuntimeExecutionRequest`` carries no tool field,
        which is what keeps implementation selection out of the execution
        envelope entirely.
        """
        violations = self.contracts.runtime_execution_request.violations(envelope)
        if violations:
            raise ExecutionEnvelopeError(
                "not a RuntimeExecutionRequest v1 at: %s"
                % ", ".join(v.locator for v in violations[:6])
            )

        transitions = [
            Transition("queued", self.clock.now(), "accepted by the in-process executor")
        ]
        started_at = self.clock.now()
        transitions.append(Transition("running", started_at, "tool invoked"))

        tool = self.tools.get(tool_id)
        if tool is None:
            ended_at = self.clock.now()
            transitions.append(
                Transition("failed", ended_at, "no tool registered for %s" % tool_id)
            )
            return (
                self._result(
                    envelope,
                    state="failed",
                    started_at=started_at,
                    ended_at=ended_at,
                    error={
                        "code": "TOOL_NOT_REGISTERED",
                        "message": "no tool is registered for the resolved tool id",
                        "category": "INTERNAL",
                        "retryable": False,
                    },
                ),
                tuple(transitions),
            )

        try:
            output = tool(payload)
        except EchoInputError as exc:
            ended_at = self.clock.now()
            transitions.append(Transition("failed", ended_at, "tool rejected its input"))
            return (
                self._result(
                    envelope,
                    state="failed",
                    started_at=started_at,
                    ended_at=ended_at,
                    error={
                        "code": "TOOL_INPUT_REJECTED",
                        "message": str(exc),
                        "category": "VALIDATION",
                        "retryable": False,
                    },
                ),
                tuple(transitions),
            )
        except Exception as exc:
            ended_at = self.clock.now()
            transitions.append(Transition("failed", ended_at, "tool raised"))
            return (
                self._result(
                    envelope,
                    state="failed",
                    started_at=started_at,
                    ended_at=ended_at,
                    error={
                        "code": "TOOL_RAISED",
                        # The type only. A tool's exception message is its own
                        # text and may quote anything it was handed.
                        "message": "the tool raised %s" % type(exc).__name__,
                        "category": "INTERNAL",
                        "retryable": False,
                    },
                ),
                tuple(transitions),
            )

        ended_at = self.clock.now()
        transitions.append(Transition("succeeded", ended_at, "tool returned"))
        return (
            self._result(
                envelope,
                state="succeeded",
                started_at=started_at,
                ended_at=ended_at,
                output=output,
            ),
            tuple(transitions),
        )

    # -- building the result ---------------------------------------------

    def _result(
        self,
        envelope: Mapping[str, Any],
        *,
        state: str,
        started_at: str,
        ended_at: str,
        output: Any = None,
        error: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return build_execution_result(
            self.contracts,
            envelope,
            state=state,
            started_at=started_at,
            ended_at=ended_at,
            executor_ref=IN_PROCESS_EXECUTOR_REF,
            output=output,
            error=error,
        )


def build_execution_result(
    contracts: FrozenContracts,
    envelope: Mapping[str, Any],
    *,
    state: str,
    started_at: str,
    ended_at: str,
    executor_ref: str,
    output: Any = None,
    error: Mapping[str, Any] | None = None,
    independent_validation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build and self-validate one ``RuntimeExecutionResult v1``.

    Shared by every executor on purpose. RK-08B's promotion gate asks that two
    implementations return *equivalent normalized results*; the cheapest way to
    make that true is for one function to build both, so the only fields that can
    differ are the ones an executor is entitled to differ on -- ``executor_ref``,
    the state it reached, and what it produced.

    ``executor_ref`` is recorded for audit only. It is never routing authority,
    never caller-visible, and never appears in a ``RuntimeRouteDecision``.
    """
    result: dict[str, Any] = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "execution_id": envelope["execution_id"],
        "request_id": envelope["request_id"],
        "correlation_id": envelope["correlation_id"],
        "idempotency_key": envelope["idempotency_key"],
        "decision_ref": dict(envelope["decision_ref"]),
        "state": state,
        "terminal": state in TERMINAL_STATES,
        "replayed": False,
        "partial": False,
        # An OBJECT, not a boolean: the frozen contract records a terminal
        # failure after retry exhaustion rather than dropping it silently.
        "dead_lettered": {"dead_lettered": False, "attempts_exhausted": None},
        "telemetry": {
            "started_at": started_at,
            "ended_at": ended_at,
            "duration_ms": _duration_ms(started_at, ended_at),
            "attempts": 1,
            "lease_released": True,
            "cost_events": [],
        },
        "artifacts": [],
        # Recorded for audit ONLY, and separately from any provider
        # reference. Never routing authority, never caller-visible.
        "executor_ref": executor_ref,
        "independent_validation": (
            dict(independent_validation)
            if independent_validation is not None
            else {"required": False, "satisfied": None}
        ),
    }
    if output is not None:
        result["output"] = output
    result["error"] = dict(error) if error else None

    # The outbound credential boundary, at the one place every executor builds a
    # result. Before the schema check, because a document carrying credential
    # material must not be emitted whether or not it is otherwise well formed --
    # and before the return, because the only safe number of such documents to
    # emit is zero. `output`, `error`, `artifacts` and every diagnostic string on
    # the result are in scope; there is no path that puts one on a result without
    # coming through here.
    assert_no_credentials(
        result,
        surface="the RuntimeExecutionResult built by %s" % executor_ref,
        reference_fields=RESULT_REFERENCE_FIELDS,
    )

    violations = contracts.runtime_execution_result.violations(result)
    if violations:
        raise ExecutionEnvelopeError(
            "the executor built a result that is not RuntimeExecutionResult v1 at: %s"
            % ", ".join(v.locator for v in violations[:6])
        )
    return result


def _duration_ms(started_at: str, ended_at: str) -> int:
    start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    end = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
    return max(0, int((end - start).total_seconds() * 1000))
