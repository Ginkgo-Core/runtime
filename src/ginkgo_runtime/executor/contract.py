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

What the in-process reference deliberately does not do
------------------------------------------------------

RK-08B's n8n implementation lives beside this vendor-neutral contract. It uses
the same request/result builders and adds no selection field to either envelope.
The reference executor remains a pure in-process proof of that shared boundary.

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
from .echo import TOOL_ID, EchoInputError, echo_transform

__all__ = [
    "DEFAULT_TIMEOUT_MS",
    "TERMINAL_STATES",
    "TOOLS",
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
    """What the kernel requires of anything that runs a skill."""

    def execute(
        self, request: Mapping[str, Any], **resolved: Any
    ) -> tuple[Mapping[str, Any], tuple[Transition, ...]]:
        """Execute a canonical envelope with executor-private resolved arguments.

        The keyword arguments are already resolved upstream. They are not a
        selection surface and never become fields on ``RuntimeExecutionRequest``.
        """
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


_OUTPUT_MISSING = object()


def build_execution_result(
    *,
    contracts: FrozenContracts,
    envelope: Mapping[str, Any],
    state: str,
    started_at: str,
    ended_at: str,
    executor_ref: str,
    output: Any = _OUTPUT_MISSING,
    error: Mapping[str, Any] | None = None,
    artifacts: tuple[Mapping[str, Any], ...] = (),
    evidence_telemetry_refs: tuple[str, ...] = (),
    independent_validation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build and validate the one canonical execution-result shape.

    Executor implementations supply only their outcome and telemetry. Identity,
    correlation, idempotency, the route-decision reference, terminal semantics,
    and schema validation all remain centralized in the Executor Contract.
    """
    attempt = envelope.get("attempt") or {}
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
        "dead_lettered": {"dead_lettered": False, "attempts_exhausted": None},
        "telemetry": {
            "started_at": started_at,
            "ended_at": ended_at,
            "duration_ms": _duration_ms(started_at, ended_at),
            "attempts": attempt.get("attempt_number", 1),
            "lease_released": True,
            "cost_events": [],
        },
        "artifacts": [dict(artifact) for artifact in artifacts],
        "executor_ref": executor_ref,
        "independent_validation": dict(
            independent_validation
            or {
                "required": bool(
                    (envelope.get("evidence") or {}).get(
                        "independent_validation_required", False
                    )
                ),
                "satisfied": None,
            }
        ),
        "error": dict(error) if error else None,
    }
    if envelope.get("task_id"):
        result["task_id"] = envelope["task_id"]
    if output is not _OUTPUT_MISSING:
        result["output"] = output
    if evidence_telemetry_refs:
        result["evidence_telemetry_refs"] = list(evidence_telemetry_refs)

    violations = contracts.runtime_execution_result.violations(result)
    if violations:
        raise ExecutionEnvelopeError(
            "the executor built a result that is not RuntimeExecutionResult v1 at: %s"
            % ", ".join(v.locator for v in violations[:6])
        )
    return result


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
        kwargs: dict[str, Any] = {}
        if output is not None:
            kwargs["output"] = output
        return build_execution_result(
            contracts=self.contracts,
            envelope=envelope,
            state=state,
            started_at=started_at,
            ended_at=ended_at,
            executor_ref="rk-in-process-executor",
            error=error,
            **kwargs,
        )


def _duration_ms(started_at: str, ended_at: str) -> int:
    start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    end = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
    return max(0, int((end - start).total_seconds() * 1000))
