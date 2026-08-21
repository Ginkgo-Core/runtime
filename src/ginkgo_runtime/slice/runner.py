"""The reference slice, wired end to end.

.. code-block:: text

    CapabilityRequest v1
      -> RK-01 Capability Bus
      -> RK-02 Domain Router
      -> RK-04 Policy + Lifecycle Gate
      -> RK-08A Executor Contract
      -> rk-reference-echo  (tool: rk-echo-transform)
      -> RuntimeExecutionResult v1
      -> RuntimeExecutionResult -> CapabilityResult v1
      -> RK-06: exactly one Evidence v1 + one Event v1 per transition

Zero network, zero credentials, zero writes, zero containers, zero providers.

Where the evidence write sits, and why
--------------------------------------

RK-01 must never persist evidence -- that is RK-06's, and the sequence in
``reference-slice.md`` section 3 has the *executor* handing telemetry to the
writer rather than the Bus. So :class:`KernelDownstream` calls RK-06 after the
execution returns, and the Bus never touches it.

That placement is also what makes "exactly once" true rather than merely
intended. RK-01's ledger short-circuits a replay **before** dispatch, so a
replayed request never reaches the downstream, never reaches the executor, and
therefore cannot reach RK-06. Exactly-once is a consequence of where the seam
is, not of a check inside the writer -- though the writer refuses a second write
too, because a guarantee that rests on one mechanism is a guarantee nobody can
falsify.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ..bus import DownstreamOutcome
from ..canonical import fingerprint
from ..clock import RuntimeClock
from ..contracts import FrozenContracts
from ..evidence.writer import EvidenceLedger, EvidencePacket
from ..executor.contract import InProcessExecutor, build_execution_request
from ..router import DomainRouter, RoutingContext, to_route_view

__all__ = ["KernelDownstream", "SliceRun"]


@dataclass
class KernelDownstream:
    """Everything behind RK-01's one seam: route, gate, execute, record.

    RK-01 sees one method. It cannot observe which component refused, cannot
    reach the executor directly, and cannot influence the routing decision --
    the signature offers none of it.
    """

    router: DomainRouter
    executor: InProcessExecutor
    evidence: EvidenceLedger
    contracts: FrozenContracts
    clock: RuntimeClock
    execution_mode: str = "certification"
    certification_authorization_ref: str | None = None
    #: Injected, never generated, so the whole slice stays a pure function of its
    #: inputs and the certificate can be re-derived rather than re-observed.
    #:
    #: The two ids are *derived from the request* rather than fixed. A constant
    #: satisfies determinism and breaks identity: two genuinely different
    #: requests got the same ``execution_id``, and evidence provenance records
    #: it, so an auditor reading two records would see one execution. Derivation
    #: keeps both properties -- the same request always produces the same id, and
    #: different requests never share one.
    id_prefixes: tuple[str, str] = ("dec", "exec")
    resolved_at: str = "2026-08-21T12:00:00+00:00"

    decisions: list[Mapping[str, Any]] = field(default_factory=list)
    executions: list[Mapping[str, Any]] = field(default_factory=list)

    def _identity(self, request: Mapping[str, Any]) -> str:
        """A stable per-request discriminator for the decision and execution ids."""
        caller = request["caller"]
        return fingerprint(
            {
                "idempotency_key": request["idempotency_key"],
                "request_id": request["request_id"],
                "tenant_id": caller["tenant_id"],
                "workspace_id": caller["workspace_id"],
            }
        )[:24]

    def dispatch(self, request: Mapping[str, Any]) -> DownstreamOutcome:
        discriminator = self._identity(request)
        decision_id = "%s-%s" % (self.id_prefixes[0], discriminator)
        execution_id = "%s-%s" % (self.id_prefixes[1], discriminator)

        decision = self.router.route(
            request,
            RoutingContext(
                execution_mode=self.execution_mode,
                decision_id=decision_id,
                resolved_at=self.resolved_at,
                certification_authorization_ref=self.certification_authorization_ref,
            ),
        )
        self.decisions.append(decision)

        resolved = decision["resolved"]
        if resolved["outcome"] != "ROUTED":
            # The gate or the ladder refused. Nothing runs, and nothing is
            # recorded: there is no execution to write evidence about.
            return DownstreamOutcome(decision=to_route_view(decision), execution=None)

        envelope = build_execution_request(
            execution_id=execution_id,
            request=request,
            decision=decision,
            input_fingerprint=decision.get("input_fingerprint"),
            deadline=(request.get("constraints") or {}).get("deadline"),
            max_cost=(request.get("constraints") or {}).get("max_cost"),
        )
        execution, transitions = self.executor.execute(
            envelope,
            tool_id=resolved["tool_id"],
            payload=request.get("input") or {},
        )
        self.executions.append(execution)

        outcome = DownstreamOutcome(decision=to_route_view(decision), execution=execution)

        # RK-01 builds the CapabilityResult; RK-06 needs its status, so the
        # normalisation is asked for here rather than duplicated.
        from ..bus.normalization import normalize

        result, failure = normalize(request, outcome, self.contracts)
        if failure is None and result is not None:
            packet = self.evidence.record(
                request=request,
                decision=decision,
                execution=execution,
                result=result,
                transitions=transitions,
            )
            # The result the Bus builds carries the evidence reference, so the
            # caller can find the record without being handed it.
            outcome = DownstreamOutcome(
                decision=to_route_view(decision, evidence_refs=(packet.evidence_id,)),
                execution=execution,
            )
        return outcome


@dataclass(frozen=True)
class SliceRun:
    """One pass through the whole chain, with every artifact it produced."""

    request: Mapping[str, Any]
    outcome: Any
    decision: Mapping[str, Any] | None
    execution: Mapping[str, Any] | None
    packet: EvidencePacket | None

    @property
    def status(self) -> str | None:
        return self.outcome.status

    @property
    def caller_view(self) -> Mapping[str, Any] | None:
        return self.outcome.for_caller()

    def artifact_fingerprints(self) -> dict[str, str]:
        """Content digests of everything the run produced.

        Two runs of the same slice with the same inputs produce the same four
        values. That is the claim ``deterministic evidence/input fingerprints``
        reduces to, and it is checkable rather than asserted.
        """
        digests: dict[str, str] = {}
        if self.decision is not None:
            digests["route_decision"] = fingerprint(dict(self.decision))
            digests["input_fingerprint"] = str(self.decision.get("input_fingerprint"))
        if self.execution is not None:
            digests["execution_result"] = fingerprint(dict(self.execution))
        if self.packet is not None:
            digests["evidence"] = fingerprint(dict(self.packet.evidence))
            digests["events"] = fingerprint([dict(e) for e in self.packet.events])
        if self.outcome.result is not None:
            digests["capability_result"] = fingerprint(dict(self.outcome.result))
        return digests
