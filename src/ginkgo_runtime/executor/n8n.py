"""RK-08B-A -- the n8n executor bridge, offline.

n8n is an **executor**. It is not the control plane, it is not the router, it
holds no state the kernel trusts and it decides nothing (invariant 11). This
module is what that sentence looks like in code: a second implementation behind
the RK-08A Executor Contract, with the same signature as the in-process one, so
the two are substitutable rather than merely similar.

.. code-block:: text

    RuntimeExecutionRequest v1 -> [n8n executor] -> RuntimeExecutionResult v1

**A-half only.** This half is offline. The transport is a port and the kernel
ships exactly one implementation of it -- :class:`RecordedTransport`, which
replays observations handed to it. No socket is opened here, no host is named
here, no credential value is read here, and there is no code path that could do
any of those things: the module imports nothing that can. Detecting and reaching
a live n8n is RK-08B-B, and it is a separate change with a separate gate.

What the bridge refuses to do
-----------------------------

**It never takes routing authority.** ``tool_id`` arrives as an argument, from
the ``RuntimeRouteDecision``. The binding it resolves to -- which workflow, which
credential references -- lives in an internal registry, exactly as the
in-process executor's tool registry does. Nothing about it is caller-supplied and
nothing about it enters the envelope: ``RuntimeExecutionRequest v1`` lists
``engine``, ``workflow_id``, ``node_id``, ``endpoint``, ``credential`` and nine
more in ``x-ginkgo-forbidden-fields``, and sets ``additionalProperties: false``
so the ban is structural rather than advisory.

**It never passes an n8n payload through to the caller.** The output is
*projected* from one declared path in the observation, never forwarded whole. A
pass-through would carry ``workflowId``, node names, run data and possibly
credential material into ``CapabilityResult.output``, which invariant 17 exists
to prevent. And the projection is checked: a projected output that still carries
a forbidden or n8n-internal key fails the execution rather than being quietly
scrubbed, because a scrub hides a misconfigured binding and a failure names it.

**It never quotes n8n's own error text.** A provider's error message is the
provider's text and may quote anything it was handed, including the payload and
the credential that failed. ``error.schema.json`` says it in its own description:
*"Secrets, credentials, raw endpoints and provider payloads MUST NOT appear in
message or details."* So the bridge reports the failure class it observed and
nothing the provider wrote -- the same discipline
:class:`~ginkgo_runtime.executor.contract.InProcessExecutor` applies when it
reports only an exception's type.

**It never guesses a status.** n8n's own vocabulary is mapped explicitly in
:data:`N8N_STATUS_TO_STATE`; anything outside it is a ``failed`` execution with
``INTERNAL``. Fail closed. Treating an unrecognised status as success is how a
bridge reports a crash as a result.

**It never claims its own success is proof.** ``independent_validation`` is
carried honestly: when the envelope requires it, an injected validator decides
and is named on the result; when no validator is injected, ``satisfied`` stays
``None`` and RK-01's veto turns the run into an ``ERROR``. An executor asserting
its own success is exactly what ``GL-07`` forbids.

Time, and why nothing sleeps
----------------------------

The poll budget is derived from ``bounds.timeout_ms`` and the binding's poll
interval, and it is counted in polls rather than measured against a clock. The
kernel's clock is a port; a bridge that raced a real clock would make the E2E
certificate unreproducible, and a bridge that slept would make the test suite
slow for no proof. So a run that never reaches a terminal status exhausts its
budget and becomes ``timed_out`` with ``category: TIMEOUT`` -- the mapping the
frozen contract declares, reached deterministically. Enforcing a wall-clock bound
against a live engine is RK-08B-B's problem, where there is something to
interrupt.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..clock import RuntimeClock
from ..contracts import FrozenContracts
from .contract import (
    NON_TERMINAL_STATES,
    ExecutionEnvelopeError,
    Transition,
    build_execution_result,
)

__all__ = [
    "N8N_EXECUTOR_REF",
    "N8N_INTERNAL_KEYS",
    "N8N_STATUS_TO_STATE",
    "N8nBinding",
    "N8nExecutor",
    "N8nObservation",
    "N8nTransport",
    "N8nTransportError",
    "RecordedTransport",
    "project_output",
]

#: Audit-only identity, recorded on the result beside no provider reference at
#: all. Never routing authority, never caller-visible, never in a decision.
N8N_EXECUTOR_REF = "rk-n8n-executor"

#: n8n's execution vocabulary, mapped onto the frozen executor states. Four
#: other state ladders exist in this ecosystem and none of them is this one:
#: ``executor-contract.md`` section 3 names all four so they cannot be confused.
#:
#: ``waiting`` is n8n's paused-execution status. It is non-terminal here, which
#: is the honest reading: a workflow parked on a webhook has not finished, and
#: calling it succeeded would report a pause as a result.
N8N_STATUS_TO_STATE: Mapping[str, str] = {
    "new": "queued",
    "running": "running",
    "waiting": "running",
    "success": "succeeded",
    "error": "failed",
    "crashed": "failed",
    "canceled": "cancelled",
    "cancelled": "cancelled",
}

#: n8n's own internal identifiers. They are not secrets, and they are still
#: banned from a projected output: they name the engine and its wiring, which is
#: precisely what a caller must never receive (invariant 17).
N8N_INTERNAL_KEYS = (
    "workflow_id",
    "workflow_name",
    "execution_id",
    "node",
    "nodes",
    "node_id",
    "run_data",
    "result_data",
    "credentials",
    "credential_id",
)

MAX_STATUS_ECHO = 64


class N8nTransportError(RuntimeError):
    """The transport could not obtain an observation.

    Distinct from a workflow that ran and failed: this one means the bridge never
    learned an outcome, which is a ``PROVIDER`` failure of the executor, not a
    failure of the skill.
    """


@dataclass(frozen=True)
class N8nObservation:
    """One thing the bridge learned about an execution.

    ``payload`` is whatever the transport observed, unfiltered. It is the raw
    engine document on purpose -- so that the projection and the leak check have
    something real to be tested against, rather than a payload pre-cleaned by the
    fixture that then proves nothing.
    """

    status: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    handle: str | None = None


@dataclass(frozen=True)
class N8nBinding:
    """The internal binding: which workflow, which credential references, how to read it.

    Internal in the strict sense -- resolved from the routing decision's
    ``tool_id``, never supplied by a caller, never written into the envelope and
    never present on the result. ``workflow_ref`` is an opaque reference the
    adapter layer resolves; it is not a URL and this module never treats it as
    one.
    """

    binding_id: str
    workflow_ref: str
    #: Where the normalized output sits inside an observation payload. One
    #: declared path, so the projection is a lookup rather than a guess.
    output_path: tuple[str, ...]
    #: Credential IDENTIFIERS. A value here would be a contract violation, and
    #: the bridge never resolves one -- it hands references to the transport.
    credential_refs: tuple[str, ...] = ()
    poll_interval_ms: int = 500


class N8nTransport(Protocol):
    """The only way this module can learn anything about an n8n execution.

    A port, so that the offline half is offline by construction rather than by
    convention: the kernel ships :class:`RecordedTransport` and nothing else, and
    the live implementation RK-08B-B introduces satisfies this same Protocol
    without any change here.
    """

    def start(
        self,
        *,
        workflow_ref: str,
        payload: Mapping[str, Any],
        correlation_id: str,
        idempotency_key: str,
        credential_refs: Sequence[str],
    ) -> N8nObservation:
        """Request the execution and return the first observation."""
        ...

    def poll(self, *, workflow_ref: str, handle: str | None) -> N8nObservation:
        """Return the next observation for an execution already started."""
        ...


@dataclass
class RecordedTransport:
    """Replays a recorded sequence of observations. Opens nothing.

    The recordings are **shaped like** n8n's execution documents and were written
    by hand, not captured from a running instance. That is stated rather than
    implied: they prove the bridge's normalisation, its projection and its leak
    check, and they prove nothing whatsoever about a real engine's behaviour.
    Re-recording them against a live instance, and diffing the shape, is the
    first item of RK-08B-B.
    """

    script: Mapping[str, Sequence[N8nObservation]]
    #: Workflow references whose transport is unavailable, so the failure class
    #: the bridge has no other way to reach can be exercised.
    unavailable: frozenset[str] = frozenset()
    #: Every call made, in order. A test asserts on what the bridge sent -- in
    #: particular that it sent references and never values.
    calls: list[tuple[str, ...]] = field(default_factory=list)
    _cursor: dict[str, int] = field(default_factory=dict, repr=False)

    def start(
        self,
        *,
        workflow_ref: str,
        payload: Mapping[str, Any],
        correlation_id: str,
        idempotency_key: str,
        credential_refs: Sequence[str],
    ) -> N8nObservation:
        self.calls.append(
            ("start", workflow_ref, correlation_id, idempotency_key, *credential_refs)
        )
        if workflow_ref in self.unavailable:
            raise N8nTransportError("no transport is configured for %s" % workflow_ref)
        return self._next(workflow_ref)

    def poll(self, *, workflow_ref: str, handle: str | None) -> N8nObservation:
        self.calls.append(("poll", workflow_ref, handle or ""))
        if workflow_ref in self.unavailable:
            raise N8nTransportError("no transport is configured for %s" % workflow_ref)
        return self._next(workflow_ref)

    def _next(self, workflow_ref: str) -> N8nObservation:
        recorded = self.script.get(workflow_ref)
        if not recorded:
            raise N8nTransportError("nothing recorded for %s" % workflow_ref)
        index = self._cursor.get(workflow_ref, 0)
        self._cursor[workflow_ref] = min(index + 1, len(recorded) - 1)
        return recorded[index]


def project_output(payload: Mapping[str, Any], path: Sequence[str]) -> Any:
    """Read one declared path out of an observation. Raises ``KeyError`` if absent.

    Deliberately not a search. A bridge that looked for the output would find
    something eventually, and what it found would be whatever the engine happened
    to put there.
    """
    current: Any = payload
    for segment in path:
        if not isinstance(current, Mapping) or segment not in current:
            raise KeyError(".".join(path))
        current = current[segment]
    return current


def _normalised_key(key: str) -> str:
    return key.replace("_", "").replace("-", "").lower()


def _leaked_keys(value: Any, banned: frozenset[str], found: set[str]) -> set[str]:
    """Every banned key reachable in ``value``. Keys only -- values are data."""
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if isinstance(key, str) and _normalised_key(key) in banned:
                found.add(key)
            _leaked_keys(nested, banned, found)
    elif isinstance(value, list | tuple):
        for item in value:
            _leaked_keys(item, banned, found)
    return found


@dataclass
class N8nExecutor:
    """Runs one skill through n8n, behind the RK-08A Executor Contract.

    Same signature as :class:`~ginkgo_runtime.executor.contract.InProcessExecutor`,
    and the same result builder, so RK-08B's promotion gate -- *"same TaskOps job
    executed by n8n and a stub executor with equivalent normalized result"* -- is
    a swap in a test rather than a comparison of two hand-written documents.
    """

    contracts: FrozenContracts
    clock: RuntimeClock
    transport: N8nTransport
    #: ``tool_id`` -> binding. Internal, like the in-process tool registry.
    #: Adding an entry is a review, not a configuration change.
    bindings: Mapping[str, N8nBinding] = field(default_factory=dict)
    #: Decides whether a ``succeeded`` execution is independently validated. Left
    #: unset, a run whose envelope requires validation reports ``satisfied:
    #: None`` and RK-01 refuses to call it a success.
    validator: Callable[[Mapping[str, Any], Any], bool] | None = None
    validator_ref: str | None = None

    def execute(
        self, envelope: Mapping[str, Any], *, tool_id: str, payload: Mapping[str, Any]
    ) -> tuple[dict[str, Any], tuple[Transition, ...]]:
        """Run the workflow bound to ``tool_id`` and normalise the outcome."""
        violations = self.contracts.runtime_execution_request.violations(envelope)
        if violations:
            raise ExecutionEnvelopeError(
                "not a RuntimeExecutionRequest v1 at: %s"
                % ", ".join(v.locator for v in violations[:6])
            )

        transitions = [
            Transition("queued", self.clock.now(), "accepted by the n8n executor bridge")
        ]
        started_at = self.clock.now()
        transitions.append(Transition("running", started_at, "execution requested"))

        binding = self.bindings.get(tool_id)
        if binding is None:
            # The same code the in-process executor reports for the same
            # semantic: the resolved tool has no implementation behind this
            # executor. Two executors disagreeing on the name of one condition
            # would make the equivalence gate meaningless.
            return self._terminal(
                envelope,
                transitions,
                started_at,
                state="failed",
                reason="no binding registered for %s" % tool_id,
                error={
                    "code": "TOOL_NOT_REGISTERED",
                    "message": "no tool is registered for the resolved tool id",
                    "category": "INTERNAL",
                    "retryable": False,
                },
            )

        try:
            observation = self.transport.start(
                workflow_ref=binding.workflow_ref,
                payload=payload,
                correlation_id=envelope["correlation_id"],
                idempotency_key=envelope["idempotency_key"],
                # References. The bridge holds no value and resolves none: the
                # adapter layer does that, and this list is what it is handed.
                credential_refs=list(binding.credential_refs)
                or list(envelope.get("credential_refs") or []),
            )
            state = self._state_for(observation.status)
            polls_left = self._poll_budget(envelope, binding)
            while state in NON_TERMINAL_STATES and polls_left > 0:
                observation = self.transport.poll(
                    workflow_ref=binding.workflow_ref, handle=observation.handle
                )
                state = self._state_for(observation.status)
                polls_left -= 1
        except N8nTransportError:
            return self._terminal(
                envelope,
                transitions,
                started_at,
                state="failed",
                reason="the executor transport reported no outcome",
                error={
                    "code": "N8N_TRANSPORT_UNAVAILABLE",
                    # The exception's text is not quoted: it is the transport's
                    # own string and may name a host or carry a payload.
                    "message": "the executor transport did not return an observation",
                    "category": "PROVIDER",
                    # The class is retryable; retry_policy.max_attempts still
                    # governs whether anything retries. This flag describes the
                    # failure, it does not authorise a second attempt.
                    "retryable": True,
                },
            )

        if state is None:
            return self._terminal(
                envelope,
                transitions,
                started_at,
                state="failed",
                reason="unrecognised executor status",
                error={
                    "code": "N8N_STATUS_UNKNOWN",
                    "message": "the executor reported status %r, which is not in the "
                    "mapped vocabulary; an unmapped status is never read as success"
                    % observation.status[:MAX_STATUS_ECHO],
                    "category": "INTERNAL",
                    "retryable": False,
                },
            )

        if state in NON_TERMINAL_STATES:
            return self._terminal(
                envelope,
                transitions,
                started_at,
                state="timed_out",
                reason="the poll budget was exhausted before a terminal status",
                error={
                    "code": "N8N_EXECUTION_TIMEOUT",
                    "message": "the execution did not reach a terminal status within "
                    "the bound carried on the envelope",
                    # Declared by the frozen contract for this state, not chosen
                    # here: `timed_out` requires `category: TIMEOUT`.
                    "category": "TIMEOUT",
                    "retryable": True,
                },
            )

        if state == "failed":
            return self._terminal(
                envelope,
                transitions,
                started_at,
                state="failed",
                reason="the workflow reported %s" % observation.status[:MAX_STATUS_ECHO],
                error={
                    "code": "N8N_WORKFLOW_FAILED",
                    # No provider text. See the module docstring.
                    "message": "the executed workflow terminated in a failure state",
                    "category": "PROVIDER",
                    "retryable": False,
                },
            )

        if state == "cancelled":
            return self._terminal(
                envelope,
                transitions,
                started_at,
                state="cancelled",
                reason="the execution was cancelled",
                # Cancellation is a first-class transition, not an error, and the
                # frozen contract requires no error object for it. RK-01 maps it
                # to BLOCKED on its own.
            )

        return self._succeeded(envelope, transitions, started_at, binding, observation)

    # -- the succeeded path ------------------------------------------------

    def _succeeded(
        self,
        envelope: Mapping[str, Any],
        transitions: list[Transition],
        started_at: str,
        binding: N8nBinding,
        observation: N8nObservation,
    ) -> tuple[dict[str, Any], tuple[Transition, ...]]:
        try:
            output = project_output(observation.payload, binding.output_path)
        except KeyError:
            return self._terminal(
                envelope,
                transitions,
                started_at,
                state="failed",
                reason="the binding's output path is absent from the observation",
                error={
                    "code": "N8N_OUTPUT_MISSING",
                    "message": "the binding declares an output path the executed "
                    "workflow did not produce",
                    "category": "INTERNAL",
                    "retryable": False,
                },
            )

        banned = frozenset(
            _normalised_key(name)
            for name in tuple(N8N_INTERNAL_KEYS) + self._forbidden_envelope_fields()
        )
        leaked = sorted(_leaked_keys(output, banned, set()))
        if leaked:
            return self._terminal(
                envelope,
                transitions,
                started_at,
                state="failed",
                reason="the projected output carries engine-internal keys",
                error={
                    "code": "N8N_OUTPUT_LEAK",
                    "message": "the projected output carries keys a caller must never "
                    "receive: %s" % ", ".join(leaked[:6]),
                    "category": "INTERNAL",
                    "retryable": False,
                },
            )

        required = bool((envelope.get("evidence") or {}).get("independent_validation_required"))
        validation: dict[str, Any] = {"required": required, "satisfied": None}
        if required and self.validator is not None:
            validation["satisfied"] = bool(self.validator(envelope, output))
            # Named only when it actually ran. A `validator_ref` beside a
            # validation that never happened is a claim about evidence that does
            # not exist, and the block exists to stop exactly that kind of claim.
            if self.validator_ref is not None:
                validation["validator_ref"] = self.validator_ref
        return self._terminal(
            envelope,
            transitions,
            started_at,
            state="succeeded",
            reason="the workflow reported success",
            output=output,
            independent_validation=validation,
        )

    # -- shared tail -------------------------------------------------------

    def _terminal(
        self,
        envelope: Mapping[str, Any],
        transitions: list[Transition],
        started_at: str,
        *,
        state: str,
        reason: str,
        output: Any = None,
        error: Mapping[str, Any] | None = None,
        independent_validation: Mapping[str, Any] | None = None,
    ) -> tuple[dict[str, Any], tuple[Transition, ...]]:
        ended_at = self.clock.now()
        transitions.append(Transition(state, ended_at, reason))
        result = build_execution_result(
            self.contracts,
            envelope,
            state=state,
            started_at=started_at,
            ended_at=ended_at,
            executor_ref=N8N_EXECUTOR_REF,
            output=output,
            error=error,
            independent_validation=independent_validation,
        )
        return result, tuple(transitions)

    def _state_for(self, status: str) -> str | None:
        """The normalized state, or ``None`` for a status outside the vocabulary."""
        if not isinstance(status, str):
            return None
        return N8N_STATUS_TO_STATE.get(status.strip().lower())

    def _poll_budget(self, envelope: Mapping[str, Any], binding: N8nBinding) -> int:
        timeout_ms = int((envelope.get("bounds") or {}).get("timeout_ms") or 0)
        interval = max(1, binding.poll_interval_ms)
        return max(1, timeout_ms // interval)

    def _forbidden_envelope_fields(self) -> tuple[str, ...]:
        """``x-ginkgo-forbidden-fields``, read from the frozen envelope contract.

        Read rather than restated, for the same reason RK-01 reads the status
        mapping: a second copy of a contract's list is a copy that drifts.
        """
        schema = getattr(self.contracts.runtime_execution_request, "schema", None)
        declared = (schema or {}).get("x-ginkgo-forbidden-fields") or ()
        return tuple(str(name) for name in declared)
