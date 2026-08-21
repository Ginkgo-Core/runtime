"""RK-01 -- the Capability Bus.

Named exactly ``Capability Bus``, with no qualifier, and there is exactly one.

The single lawful entry point (invariant 2). Every caller class -- WhatsApp, n8n,
Hermes, OpenClaw, Sylvia, API, MCP -- enters identically; the Bus reads
``caller.actor_type`` for audit and never for privilege. Reference slice P8 is
that test.

.. code-block:: text

    CapabilityRequest v1
      -> admission        (schema · caller-agnosticism · contract version)
      -> identity         (fingerprint · idempotency)
      -> dispatch         (the internal routing interface -- one seam)
      -> normalization    (RuntimeExecutionResult -> CapabilityResult v1)
      -> caller projection

**Never.** Selects a skill, adapter or provider -- that is RK-02. Evaluates
lifecycle, approval or gates -- that is RK-04. Executes anything -- RK-08A.
Persists evidence -- RK-06. None of those four is implemented in this repository,
and the Bus reaches them through one Protocol with one method, which is not wide
enough to express any of their decisions.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ..catalog.build import default_root
from ..catalog.manifest import load_manifest
from ..contracts import FrozenContracts, load_frozen_contracts
from .admission import admit
from .downstream import DownstreamOutcome, RuntimeDownstream
from .errors import BusError, rejection
from .identity import IdempotencyLedger, RequestIdentity, identify
from .normalization import caller_projection, normalize

__all__ = ["CapabilityBus", "CapabilityOutcome"]


@dataclass(frozen=True)
class CapabilityOutcome:
    """What the boundary returns: a result, or a rejection. Never both."""

    #: ``CapabilityResult v1``, validated. ``None`` when the request was rejected.
    result: Mapping[str, Any] | None
    #: ``Error v1``, validated. ``None`` when the request produced a result.
    error: Mapping[str, Any] | None
    #: ``True`` when this answer came from the idempotency ledger rather than a
    #: fresh dispatch. Reference slice P7.
    replayed: bool = False

    @property
    def accepted(self) -> bool:
        return self.result is not None

    @property
    def status(self) -> str | None:
        return None if self.result is None else str(self.result["status"])

    def for_caller(self) -> Mapping[str, Any] | None:
        """The unprivileged projection: ``routing.adapter_id`` and ``routing.provider`` withheld."""
        return None if self.result is None else caller_projection(self.result)


@dataclass
class CapabilityBus:
    """The universal boundary (invariant 2)."""

    downstream: RuntimeDownstream
    contracts: FrozenContracts
    ledger: IdempotencyLedger = field(default_factory=IdempotencyLedger)

    @classmethod
    def from_pinned_contracts(
        cls, downstream: RuntimeDownstream, root: str | None = None
    ) -> CapabilityBus:
        """Build a Bus whose contracts come from the SHA-pinned snapshot.

        The same manifest, integrity gate and offline guarantee the Runtime
        Catalog uses. No network, no unpinned file.
        """
        manifest = load_manifest(default_root() if root is None else root)
        return cls(downstream=downstream, contracts=load_frozen_contracts(manifest.read))

    # -- the boundary ----------------------------------------------------

    def submit(self, request: Any) -> CapabilityOutcome:
        """Carry one request across the boundary and back."""
        inadmissible = admit(request, self.contracts)
        if inadmissible is not None:
            return self._reject(inadmissible)

        identity = identify(request)

        if self.ledger.conflicts(identity):
            # N14. The old result is not replayed and the new request is not
            # dispatched: the key promised one payload and this is another.
            return self._reject(
                rejection(
                    "IDEMPOTENCY_CONFLICT",
                    "idempotency_key %r was already used in this workspace with a "
                    "different payload" % identity.idempotency_key,
                    details={
                        "idempotency_key": identity.idempotency_key,
                        "payload_fingerprint": identity.payload_fingerprint,
                    },
                )
            )

        replay = self.ledger.lookup(identity)
        if replay is not None:
            # P7: the same result, and no second dispatch. The downstream is never
            # called, so nothing downstream can write a second evidence record.
            return CapabilityOutcome(result=replay.result, error=None, replayed=True)

        try:
            outcome = self.downstream.dispatch(request)
        except Exception as exc:
            # A downstream that raises is a contract violation, not a result. The
            # Bus normalizes it rather than letting a stack trace out of the
            # boundary -- and reports only the exception's TYPE. Its message is
            # downstream text that may quote a connection string or a provider
            # payload, which error.schema.json forbids (reference slice N15).
            return self._reject(
                rejection(
                    "DOWNSTREAM_CONTRACT_VIOLATION",
                    "the routing interface raised %s instead of returning an "
                    "outcome" % type(exc).__name__,
                    details={"exception_type": type(exc).__name__},
                )
            )

        if not isinstance(outcome, DownstreamOutcome):
            return self._reject(
                rejection(
                    "DOWNSTREAM_CONTRACT_VIOLATION",
                    "the routing interface returned %s, not a DownstreamOutcome"
                    % type(outcome).__name__,
                    details={"returned_type": type(outcome).__name__},
                )
            )

        result, failure = normalize(request, outcome, self.contracts)
        if failure is not None:
            return self._reject(failure)

        assert result is not None  # normalize returns exactly one of the two
        self._remember(identity, result)
        return CapabilityOutcome(result=result, error=None, replayed=False)

    # -- internals -------------------------------------------------------

    def _remember(self, identity: RequestIdentity, result: Mapping[str, Any]) -> None:
        self.ledger.remember(identity, result)

    def _reject(self, error: BusError) -> CapabilityOutcome:
        body = error.as_dict()
        body["message"] = self._clamp(body["message"])
        violations = self.contracts.error.violations(body)
        if violations:
            # The Bus's own error envelope failed the frozen Error v1 contract.
            # That is a defect in this code, and it is raised rather than returned:
            # emitting a malformed error would hide the very thing that broke.
            raise AssertionError(
                "RK-01 built an error that is not Error v1: %s"
                % "; ".join(str(v) for v in violations)
            )
        return CapabilityOutcome(result=None, error=body, replayed=False)

    def _clamp(self, message: str) -> str:
        """Keep ``message`` inside the frozen ``maxLength``.

        A request naming four hundred forbidden fields produces a message longer
        than the contract permits, and the Bus would then fail its own error
        envelope -- letting any caller crash the boundary with a legal request.
        The limit is read from ``error.schema.json``, not written here.
        """
        limit = self.contracts.error_message_max_length
        if len(message) <= limit:
            return message
        marker = " ... (truncated)"
        return message[: limit - len(marker)] + marker
