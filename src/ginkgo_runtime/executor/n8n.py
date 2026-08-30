"""RK-08B/A -- governed, offline n8n executor adapter foundation.

The adapter has deliberately little authority. It receives an already-valid
``RuntimeExecutionRequest v1`` and an already-resolved workflow artifact pin,
verifies both, dispatches one transport request, and returns the existing
``RuntimeExecutionResult v1``. It cannot select a capability, skill, binding,
provider, model, fallback, credential, or workflow.

Only the deterministic in-memory transport exists in this slice. There is no
HTTP client, endpoint, credential resolution, retry loop, TaskOps persistence,
or Evidence writer here.
"""

from __future__ import annotations

import copy
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any, Protocol

from ..canonical import MAX_DEPTH, fingerprint
from ..clock import RuntimeClock
from ..contracts import FrozenContracts
from .artifact import WorkflowArtifactDescriptor, WorkflowArtifactPin
from .contract import (
    ExecutionEnvelopeError,
    Transition,
    build_execution_result,
)

__all__ = [
    "EXECUTION_DISPOSITIONS",
    "N8N_STATE_MAP",
    "InMemoryN8nTransport",
    "N8nExecutorAdapter",
    "N8nTransport",
    "N8nTransportCancelled",
    "N8nTransportError",
    "N8nTransportRequest",
    "N8nTransportTimeout",
    "normalize_n8n_state",
]

EXECUTION_DISPOSITIONS = ("never_started", "may_have_executed")

N8N_STATE_MAP: Mapping[str, str] = MappingProxyType(
    {
        "new": "queued",
        "waiting": "queued",
        "queued": "queued",
        "running": "running",
        "success": "succeeded",
        "succeeded": "succeeded",
        "error": "failed",
        "failed": "failed",
        "crashed": "failed",
        "canceled": "cancelled",
        "cancelled": "cancelled",
        "timeout": "timed_out",
        "timed_out": "timed_out",
    }
)

_CREDENTIAL_HANDLE = re.compile(r"^credref:[A-Za-z0-9][A-Za-z0-9._:/-]{0,247}$")
_TRANSPORT_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,254}$")
_TELEMETRY_REF = re.compile(r"^[a-z][a-z0-9+.-]*:[A-Za-z0-9][A-Za-z0-9._~:/@#-]{0,510}$")
_RAW_CREDENTIAL_FIELD_PREFIXES = (
    "access_token",
    "api_key",
    "authorization",
    "cookie",
    "credential",
    "password",
    "private_key",
    "refresh_token",
    "secret",
)
_RESPONSE_REQUIRED = frozenset(
    {
        "artifact",
        "correlation_id",
        "decision_ref",
        "ended_at",
        "execution_id",
        "execution_ref",
        "idempotency_key",
        "request_id",
        "started_at",
        "state",
    }
)
_RESPONSE_ALLOWED = _RESPONSE_REQUIRED | {"output", "telemetry_refs"}
_MISSING = object()


class MalformedN8nResult(RuntimeError):
    """Untrusted transport output does not satisfy the adapter boundary."""


class N8nTransportError(RuntimeError):
    """A transport failure with explicit execution ambiguity.

    The exception accepts no upstream message. Adapter errors must never copy a
    raw executor body, endpoint, credential, or exception string into a result.
    """

    def __init__(self, disposition: str = "may_have_executed") -> None:
        if disposition not in EXECUTION_DISPOSITIONS:
            raise ValueError("invalid execution disposition")
        self.disposition = disposition
        super().__init__("n8n transport failed")


class N8nTransportTimeout(N8nTransportError):
    """The bounded transport operation timed out."""


class N8nTransportCancelled(N8nTransportError):
    """The bounded transport operation was cancelled."""


@dataclass(frozen=True, repr=False)
class N8nTransportRequest:
    """Narrow executor-private dispatch shape; never a caller contract."""

    transport_workflow_ref: str = field(repr=False)
    artifact_ref: str
    artifact_version: str
    artifact_content_sha256: str
    artifact_config_sha256: str | None
    execution_id: str
    request_id: str
    correlation_id: str
    task_id: str | None
    idempotency_key: str
    decision_ref: Mapping[str, str]
    payload: Mapping[str, Any] = field(repr=False)
    credential_refs: tuple[str, ...] = field(repr=False)
    timeout_ms: int
    deadline: str | None
    cancel_token: str | None = field(repr=False)
    attempt_number: int

    def __repr__(self) -> str:
        return (
            "N8nTransportRequest(artifact_ref=%r, execution_id=%r, request_id=%r)"
            % (self.artifact_ref, self.execution_id, self.request_id)
        )


class N8nTransport(Protocol):
    """The only operation the adapter requires from an n8n transport."""

    def execute(self, request: N8nTransportRequest) -> Mapping[str, Any]:
        """Dispatch a pinned workflow and return untrusted executor output."""
        ...


class InMemoryN8nTransport:
    """Deterministic scripted transport for tests and offline conformance.

    Scripts are keyed by the private transport workflow reference. Identity,
    pin, and correlation echoes default from the request. A script can override
    them deliberately to exercise fail-closed validation. Responses never vary
    with call count, and this class implements no replay or idempotency policy.
    """

    def __init__(
        self,
        outcomes: Mapping[str, Mapping[str, Any] | Exception],
    ) -> None:
        copied: dict[str, Mapping[str, Any] | Exception] = {}
        for workflow_ref, outcome in outcomes.items():
            copied[workflow_ref] = (
                copy.deepcopy(dict(outcome)) if isinstance(outcome, Mapping) else outcome
            )
        self._outcomes = MappingProxyType(copied)
        self.calls: list[N8nTransportRequest] = []

    def __repr__(self) -> str:
        return "InMemoryN8nTransport(outcomes=%d, calls=%d)" % (
            len(self._outcomes),
            len(self.calls),
        )

    def execute(self, request: N8nTransportRequest) -> Mapping[str, Any]:
        self.calls.append(request)
        scripted = self._outcomes.get(request.transport_workflow_ref)
        if scripted is None:
            raise N8nTransportError("never_started")
        if isinstance(scripted, Exception):
            raise scripted

        response = copy.deepcopy(dict(scripted))
        response.setdefault(
            "execution_ref",
            "mem-%s"
            % fingerprint(
                {
                    "artifact_ref": request.artifact_ref,
                    "execution_id": request.execution_id,
                    "idempotency_key": request.idempotency_key,
                }
            )[:24],
        )
        response.setdefault("execution_id", request.execution_id)
        response.setdefault("request_id", request.request_id)
        response.setdefault("correlation_id", request.correlation_id)
        response.setdefault("idempotency_key", request.idempotency_key)
        response.setdefault("decision_ref", dict(request.decision_ref))
        response.setdefault(
            "artifact",
            {
                "artifact_ref": request.artifact_ref,
                "version": request.artifact_version,
                "content_sha256": request.artifact_content_sha256,
                "config_sha256": request.artifact_config_sha256,
            },
        )
        response.setdefault("started_at", "2026-08-21T12:00:00+00:00")
        response.setdefault("ended_at", "2026-08-21T12:00:00.250000+00:00")
        response.setdefault("telemetry_refs", [])
        return response


@dataclass(frozen=True)
class _ParsedTransportResult:
    state: str
    execution_ref: str
    started_at: str
    ended_at: str
    output: Any
    telemetry_refs: tuple[str, ...]


@dataclass(repr=False)
class N8nExecutorAdapter:
    """Validate, dispatch, and normalize one already-resolved workflow pin."""

    contracts: FrozenContracts
    clock: RuntimeClock
    transport: N8nTransport = field(repr=False)
    artifacts: tuple[WorkflowArtifactDescriptor, ...]
    _by_ref: Mapping[str, WorkflowArtifactDescriptor] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        descriptors = tuple(self.artifacts)
        by_ref: dict[str, WorkflowArtifactDescriptor] = {}
        for descriptor in descriptors:
            if not isinstance(descriptor, WorkflowArtifactDescriptor):
                raise TypeError("artifacts must contain WorkflowArtifactDescriptor values")
            if descriptor.artifact_ref in by_ref:
                raise ValueError("duplicate workflow artifact_ref")
            by_ref[descriptor.artifact_ref] = descriptor
        self.artifacts = descriptors
        self._by_ref = MappingProxyType(by_ref)

    def __repr__(self) -> str:
        return "N8nExecutorAdapter(artifacts=%d)" % len(self._by_ref)

    def execute(
        self,
        envelope: Mapping[str, Any],
        *,
        artifact_pin: WorkflowArtifactPin,
        payload: Mapping[str, Any],
    ) -> tuple[dict[str, Any], tuple[Transition, ...]]:
        """Execute one exact pin without selecting or re-resolving anything."""
        violations = self.contracts.runtime_execution_request.violations(envelope)
        if violations:
            raise ExecutionEnvelopeError(
                "not a RuntimeExecutionRequest v1 at: %s"
                % ", ".join(v.locator for v in violations[:6])
            )

        accepted_at = self.clock.now()
        if not isinstance(artifact_pin, WorkflowArtifactPin):
            return self._failure(
                envelope,
                accepted_at=accepted_at,
                code="INVALID_WORKFLOW_ARTIFACT_PIN",
                message="the resolved workflow artifact pin is invalid",
                category="VALIDATION",
                disposition="never_started",
                reason="invalid workflow artifact pin",
            )

        descriptor = self._by_ref.get(artifact_pin.artifact_ref)
        if descriptor is None:
            return self._failure(
                envelope,
                accepted_at=accepted_at,
                code="UNKNOWN_WORKFLOW_ARTIFACT",
                message="the resolved workflow artifact is not known to this executor",
                category="INTERNAL",
                disposition="never_started",
                reason="workflow artifact not found",
            )
        if descriptor.pin != artifact_pin:
            return self._failure(
                envelope,
                accepted_at=accepted_at,
                code="WORKFLOW_ARTIFACT_PIN_MISMATCH",
                message="the workflow artifact version or hash does not match its pin",
                category="CONFLICT",
                disposition="never_started",
                reason="workflow artifact pin mismatch",
                descriptor=descriptor,
            )
        if not isinstance(payload, Mapping):
            return self._failure(
                envelope,
                accepted_at=accepted_at,
                code="INVALID_EXECUTOR_PAYLOAD",
                message="the executor payload must be an object",
                category="VALIDATION",
                disposition="never_started",
                reason="invalid executor payload",
                descriptor=descriptor,
            )

        credential_refs = envelope.get("credential_refs") or []
        if any(
            not isinstance(ref, str) or _CREDENTIAL_HANDLE.fullmatch(ref) is None
            for ref in credential_refs
        ):
            return self._failure(
                envelope,
                accepted_at=accepted_at,
                code="RAW_CREDENTIAL_MATERIAL_REJECTED",
                message="credential inputs must be opaque credential-reference handles",
                category="VALIDATION",
                disposition="never_started",
                reason="credential reference boundary rejected input",
                descriptor=descriptor,
            )

        request = self._transport_request(
            envelope,
            descriptor=descriptor,
            payload=payload,
            credential_refs=tuple(credential_refs),
        )
        try:
            response = self.transport.execute(request)
        except N8nTransportTimeout as exc:
            return self._failure(
                envelope,
                accepted_at=accepted_at,
                code="N8N_TRANSPORT_TIMEOUT",
                message="the executor transport exceeded its configured timeout",
                category="TIMEOUT",
                disposition=exc.disposition,
                reason="executor transport timed out",
                descriptor=descriptor,
                state="timed_out",
                retryable=True,
                dispatched=exc.disposition == "may_have_executed",
            )
        except N8nTransportCancelled as exc:
            return self._failure(
                envelope,
                accepted_at=accepted_at,
                code="N8N_EXECUTION_CANCELLED",
                message="the executor transport reported cancellation",
                category="CONFLICT",
                disposition=exc.disposition,
                reason="executor transport cancelled",
                descriptor=descriptor,
                state="cancelled",
                dispatched=exc.disposition == "may_have_executed",
            )
        except N8nTransportError as exc:
            return self._failure(
                envelope,
                accepted_at=accepted_at,
                code="N8N_TRANSPORT_ERROR",
                message="the executor transport failed",
                category="INTERNAL",
                disposition=exc.disposition,
                reason="executor transport failed",
                descriptor=descriptor,
                retryable=exc.disposition == "never_started",
                dispatched=exc.disposition == "may_have_executed",
            )
        except Exception:
            return self._failure(
                envelope,
                accepted_at=accepted_at,
                code="N8N_TRANSPORT_ERROR",
                message="the executor transport failed",
                category="INTERNAL",
                disposition="may_have_executed",
                reason="executor transport failed",
                descriptor=descriptor,
                dispatched=True,
            )

        try:
            parsed = _parse_response(response, request)
        except MalformedN8nResult:
            return self._failure(
                envelope,
                accepted_at=accepted_at,
                code="MALFORMED_EXECUTOR_RESULT",
                message="the executor returned a malformed result",
                category="INTERNAL",
                disposition="may_have_executed",
                reason="executor result rejected",
                descriptor=descriptor,
                dispatched=True,
            )
        return self._normalized(envelope, accepted_at, descriptor, parsed)

    def _transport_request(
        self,
        envelope: Mapping[str, Any],
        *,
        descriptor: WorkflowArtifactDescriptor,
        payload: Mapping[str, Any],
        credential_refs: tuple[str, ...],
    ) -> N8nTransportRequest:
        bounds = envelope["bounds"]
        cancellation = envelope.get("cancellation") or {}
        attempt = envelope.get("attempt") or {}
        return N8nTransportRequest(
            transport_workflow_ref=descriptor.transport_workflow_ref,
            artifact_ref=descriptor.artifact_ref,
            artifact_version=descriptor.version,
            artifact_content_sha256=descriptor.content_sha256,
            artifact_config_sha256=descriptor.config_sha256,
            execution_id=envelope["execution_id"],
            request_id=envelope["request_id"],
            correlation_id=envelope["correlation_id"],
            task_id=envelope.get("task_id"),
            idempotency_key=envelope["idempotency_key"],
            decision_ref=MappingProxyType(dict(envelope["decision_ref"])),
            payload=MappingProxyType(dict(payload)),
            credential_refs=credential_refs,
            timeout_ms=bounds["timeout_ms"],
            deadline=bounds.get("deadline"),
            cancel_token=cancellation.get("cancel_token"),
            attempt_number=attempt.get("attempt_number", 1),
        )

    def _normalized(
        self,
        envelope: Mapping[str, Any],
        accepted_at: str,
        descriptor: WorkflowArtifactDescriptor,
        parsed: _ParsedTransportResult,
    ) -> tuple[dict[str, Any], tuple[Transition, ...]]:
        transitions = [Transition("queued", accepted_at, "accepted by the n8n adapter")]
        if parsed.state != "queued":
            transitions.append(Transition("running", parsed.started_at, "transport dispatched"))
        if parsed.state not in ("queued", "running"):
            transitions.append(
                Transition(parsed.state, parsed.ended_at, "executor result normalized")
            )

        error = _reported_error(parsed.state)
        refs = _evidence_refs(descriptor, parsed)
        kwargs: dict[str, Any] = {}
        if parsed.state == "succeeded" and parsed.output is not _MISSING:
            kwargs["output"] = parsed.output
        result = build_execution_result(
            contracts=self.contracts,
            envelope=envelope,
            state=parsed.state,
            started_at=parsed.started_at,
            ended_at=parsed.ended_at,
            executor_ref="rk-n8n-executor-adapter",
            error=error,
            artifacts=_result_artifacts(descriptor),
            evidence_telemetry_refs=refs,
            **kwargs,
        )
        return result, tuple(transitions)

    def _failure(
        self,
        envelope: Mapping[str, Any],
        *,
        accepted_at: str,
        code: str,
        message: str,
        category: str,
        disposition: str,
        reason: str,
        descriptor: WorkflowArtifactDescriptor | None = None,
        state: str = "failed",
        retryable: bool = False,
        dispatched: bool = False,
    ) -> tuple[dict[str, Any], tuple[Transition, ...]]:
        ended_at = self.clock.now()
        transitions = [Transition("queued", accepted_at, "accepted by the n8n adapter")]
        if dispatched:
            transitions.append(Transition("running", accepted_at, "transport dispatched"))
        transitions.append(Transition(state, ended_at, reason))
        result = build_execution_result(
            contracts=self.contracts,
            envelope=envelope,
            state=state,
            started_at=accepted_at,
            ended_at=ended_at,
            executor_ref="rk-n8n-executor-adapter",
            error={
                "code": code,
                "message": message,
                "category": category,
                "retryable": retryable,
                "details": {"execution_disposition": disposition},
            },
            artifacts=_result_artifacts(descriptor) if descriptor else (),
            evidence_telemetry_refs=(
                ("workflow-provenance-sha256:%s" % descriptor.provenance_fingerprint,)
                if descriptor
                else ()
            ),
        )
        return result, tuple(transitions)


def normalize_n8n_state(state: object) -> str:
    """Map a known n8n-like state onto the frozen executor vocabulary."""
    if not isinstance(state, str) or state not in N8N_STATE_MAP:
        raise MalformedN8nResult("unknown executor state")
    return N8N_STATE_MAP[state]


def _parse_response(
    response: object,
    request: N8nTransportRequest,
) -> _ParsedTransportResult:
    if not isinstance(response, Mapping):
        raise MalformedN8nResult("executor result must be an object")
    if set(response) != _RESPONSE_ALLOWED and not (
        _RESPONSE_REQUIRED <= set(response) <= _RESPONSE_ALLOWED
    ):
        raise MalformedN8nResult("executor result fields are invalid")

    for field_name in (
        "execution_id",
        "request_id",
        "correlation_id",
        "idempotency_key",
    ):
        if response.get(field_name) != getattr(request, field_name):
            raise MalformedN8nResult("executor result identity mismatch")
    decision_ref = response.get("decision_ref")
    if not isinstance(decision_ref, Mapping) or dict(decision_ref) != dict(request.decision_ref):
        raise MalformedN8nResult("executor result decision mismatch")

    expected_artifact = {
        "artifact_ref": request.artifact_ref,
        "version": request.artifact_version,
        "content_sha256": request.artifact_content_sha256,
        "config_sha256": request.artifact_config_sha256,
    }
    artifact = response.get("artifact")
    if not isinstance(artifact, Mapping) or dict(artifact) != expected_artifact:
        raise MalformedN8nResult("executor result artifact mismatch")

    execution_ref = response.get("execution_ref")
    if not isinstance(execution_ref, str) or _TRANSPORT_REF.fullmatch(execution_ref) is None:
        raise MalformedN8nResult("executor execution reference is invalid")
    state = normalize_n8n_state(response.get("state"))
    started_at = _timestamp(response.get("started_at"))
    ended_at = _timestamp(response.get("ended_at"))
    if _datetime(ended_at) < _datetime(started_at):
        raise MalformedN8nResult("executor timestamps are reversed")

    telemetry = response.get("telemetry_refs", [])
    if not isinstance(telemetry, list | tuple) or any(
        not isinstance(ref, str) or _TELEMETRY_REF.fullmatch(ref) is None
        for ref in telemetry
    ):
        raise MalformedN8nResult("executor telemetry references are invalid")
    if len(set(telemetry)) != len(telemetry):
        raise MalformedN8nResult("executor telemetry references are duplicated")

    output = response.get("output", _MISSING)
    if output is not _MISSING:
        _validate_output(output)
    return _ParsedTransportResult(
        state=state,
        execution_ref=execution_ref,
        started_at=started_at,
        ended_at=ended_at,
        output=output,
        telemetry_refs=tuple(telemetry),
    )


def _timestamp(value: object) -> str:
    if not isinstance(value, str):
        raise MalformedN8nResult("executor timestamp is invalid")
    parsed = _datetime(value)
    if parsed.tzinfo is None:
        raise MalformedN8nResult("executor timestamp has no timezone")
    return value


def _datetime(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MalformedN8nResult("executor timestamp is invalid") from exc


def _validate_output(value: Any, depth: int = 0) -> None:
    if depth > MAX_DEPTH:
        raise MalformedN8nResult("executor output is too deeply nested")
    if value is None or isinstance(value, str | bool | int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise MalformedN8nResult("executor output has a non-finite number")
        return
    if isinstance(value, list):
        for item in value:
            _validate_output(item, depth + 1)
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise MalformedN8nResult("executor output has a non-string key")
            lowered = key.lower()
            if any(lowered.startswith(prefix) for prefix in _RAW_CREDENTIAL_FIELD_PREFIXES):
                raise MalformedN8nResult("executor output contains credential material")
            _validate_output(item, depth + 1)
        return
    raise MalformedN8nResult("executor output is not JSON-compatible")


def _reported_error(state: str) -> Mapping[str, Any] | None:
    details = {"execution_disposition": "may_have_executed"}
    if state == "failed":
        return {
            "code": "N8N_EXECUTION_FAILED",
            "message": "the executor reported a failed workflow execution",
            "category": "INTERNAL",
            "retryable": False,
            "details": details,
        }
    if state == "timed_out":
        return {
            "code": "N8N_EXECUTION_TIMED_OUT",
            "message": "the executor reported a timed-out workflow execution",
            "category": "TIMEOUT",
            "retryable": True,
            "details": details,
        }
    if state == "cancelled":
        return {
            "code": "N8N_EXECUTION_CANCELLED",
            "message": "the executor reported a cancelled workflow execution",
            "category": "CONFLICT",
            "retryable": False,
            "details": details,
        }
    return None


def _result_artifacts(
    descriptor: WorkflowArtifactDescriptor,
) -> tuple[Mapping[str, Any], ...]:
    identity = "%s@%s" % (descriptor.artifact_ref, descriptor.version)
    artifacts: list[Mapping[str, Any]] = [
        {
            "artifact_ref": identity,
            "sha256": descriptor.content_sha256,
            "media_type": "application/vnd.n8n.workflow+json",
        }
    ]
    if descriptor.config_sha256:
        artifacts.append(
            {
                "artifact_ref": "%s#config" % identity,
                "sha256": descriptor.config_sha256,
                "media_type": "application/vnd.ginkgo.executor-config+json",
            }
        )
    return tuple(artifacts)


def _evidence_refs(
    descriptor: WorkflowArtifactDescriptor,
    parsed: _ParsedTransportResult,
) -> tuple[str, ...]:
    refs = [
        "workflow-provenance-sha256:%s" % descriptor.provenance_fingerprint,
        "n8n-execution:%s" % parsed.execution_ref,
        *parsed.telemetry_refs,
    ]
    return tuple(dict.fromkeys(refs))
