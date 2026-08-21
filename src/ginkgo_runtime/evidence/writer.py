"""RK-06 -- the Evidence / Audit Writer.

The terminal deliverable of the one-request chain: **exactly one**
``contracts/core/evidence.schema.json`` v1 record per completed request, plus one
``contracts/core/event.schema.json`` v1 record per state transition.

``event.schema.json`` is the seventh frozen core schema and is *"currently
referenced by no descriptor anywhere in the ecosystem"* (BLK-09). This is its
first consumer.

What it never does
------------------

**It never defines its own evidence record.** `ai-stack#43` calls redefining the
frozen schema *"the single most repeated defect in the ecosystem"* -- `#7`'s
``goal_evidence`` table does it, and ``ginkgo-connect`` carries a fully disjoint
model with its own ``EVIDENCE_TYPES`` and ``EVIDENCE_STRENGTHS``. This module
writes ``Evidence v1`` and validates what it wrote.

**It never writes a secret, credential, raw endpoint or provider payload.**
``error.schema.json``'s own description is the rule. The evidence record carries
an input *fingerprint*, never the input; a policy *decision ref*, never the
policy snapshot; and an ``executor_ref`` recorded **separately** from any
provider reference.

**It never promotes anything to Knowledge.** Evidence != Knowledge (invariant
12); promotion happens only through ``knowledge.*`` on the bus (invariant 13).
Every record is written with ``knowledge_promotion.status: NOT_REQUESTED`` and
there is no code path that sets anything else. No "Learning Layer" exists here.

**It never writes a second record for a replayed idempotency key.** The ledger is
keyed on the same identity RK-01 uses, and a second write for a key that already
has a record is refused rather than silently ignored -- silence would make
"exactly once" unfalsifiable.

**It holds no TaskOps state** (invariant 15) and nothing here is authoritative
runtime state (invariant 9); a durable deployment persists these records in
PostgreSQL.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..canonical import fingerprint
from ..clock import RuntimeClock
from ..contracts import FrozenContracts
from ..executor.contract import Transition

__all__ = [
    "EVENT_VERSION",
    "NEGATIVE_PROOFS",
    "EvidenceLedger",
    "EvidencePacket",
    "EvidenceWriteError",
]

EVENT_VERSION = "v1"

#: Explicit negative proofs, recorded alongside the positives.
#:
#: `ai-stack#43` requires them and ``reference-slice.md`` section 6 lists them:
#: a green result is ``TEST`` / ``EVAL`` evidence, never ``API_OBSERVATION``, and
#: the record has to say so itself. An evidence record that only carries what
#: succeeded invites a promotion claim that outruns it.
NEGATIVE_PROOFS: Mapping[str, Any] = {
    "credentials_in_ci": "none",
    "live_provider_validation_proven": False,
    "mutations": "OFF",
    "network": "OFF",
}


class EvidenceWriteError(RuntimeError):
    """Evidence could not be written, or a second record was attempted."""


@dataclass(frozen=True)
class EvidencePacket:
    """One completed request's audit trail: one record, N events."""

    evidence: Mapping[str, Any]
    events: tuple[Mapping[str, Any], ...]

    @property
    def evidence_id(self) -> str:
        return str(self.evidence["evidence_id"])


@dataclass
class EvidenceLedger:
    """In-process store. Invariant 9 puts the durable one in PostgreSQL."""

    contracts: FrozenContracts
    clock: RuntimeClock
    _by_key: dict[tuple[str, str, str], EvidencePacket] = field(
        default_factory=dict, repr=False
    )

    # -- the write --------------------------------------------------------

    def record(
        self,
        *,
        request: Mapping[str, Any],
        decision: Mapping[str, Any],
        execution: Mapping[str, Any],
        result: Mapping[str, Any],
        transitions: Sequence[Transition],
    ) -> EvidencePacket:
        """Write exactly one evidence record and one event per transition."""
        key = self._key(request)
        if key in self._by_key:
            raise EvidenceWriteError(
                "an evidence record already exists for idempotency_key %r in this "
                "workspace; a replay writes no second record"
                % request["idempotency_key"]
            )

        evidence = self._evidence(request, decision, execution, result)
        events = tuple(
            self._event(request, execution, transition, evidence["evidence_id"])
            for transition in transitions
        )

        self._validate(evidence, events)
        packet = EvidencePacket(evidence=evidence, events=events)
        self._by_key[key] = packet
        return packet

    def packet_for(self, request: Mapping[str, Any]) -> EvidencePacket | None:
        return self._by_key.get(self._key(request))

    @property
    def record_count(self) -> int:
        return len(self._by_key)

    def all_packets(self) -> tuple[EvidencePacket, ...]:
        return tuple(self._by_key.values())

    # -- construction ------------------------------------------------------

    def _key(self, request: Mapping[str, Any]) -> tuple[str, str, str]:
        caller = request["caller"]
        return (caller["tenant_id"], caller["workspace_id"], request["idempotency_key"])

    def _evidence(
        self,
        request: Mapping[str, Any],
        decision: Mapping[str, Any],
        execution: Mapping[str, Any],
        result: Mapping[str, Any],
    ) -> dict[str, Any]:
        resolved = decision["resolved"]
        telemetry = execution.get("telemetry") or {}
        error = result.get("error") or {}

        # The minimum recorded set from `ai-stack#43`, in `provenance` -- the one
        # place the frozen schema leaves open. Nothing here is a redefinition of
        # the record; the record is Evidence v1 and this is its provenance.
        provenance: dict[str, Any] = {
            "adapter_ref": resolved.get("adapter_id"),
            "algorithm_version": decision["algorithm_version"],
            "binding_ref": resolved.get("binding_id"),
            "capability": dict(request["capability"]),
            "catalog_version": decision["catalog"]["catalog_version"],
            "correlation_id": request["correlation_id"],
            "error_class": error.get("category"),
            "execution_id": execution["execution_id"],
            "execution_mode": decision["execution_mode"],
            "executor_ref": execution.get("executor_ref"),
            "input_fingerprint": decision.get("input_fingerprint"),
            "policy_decision_ref": "%s@%s#%s"
            % (
                decision["policy"]["policy_id"],
                decision["policy"]["policy_version"],
                decision["policy"]["policy_snapshot_hash"][:16],
            ),
            "provider_binding_ref": resolved.get("provider"),
            "request_id": request["request_id"],
            "result_status": result["status"],
            "route_decision_hash": decision["decision_hash"],
            "skill_id": resolved.get("skill_id"),
            "skill_version": resolved.get("skill_version"),
            "started_at": telemetry.get("started_at"),
            "ended_at": telemetry.get("ended_at"),
            "duration_ms": telemetry.get("duration_ms"),
            "cost_events": list(telemetry.get("cost_events") or []),
            "artifact_hashes": [
                artifact.get("sha256") for artifact in execution.get("artifacts") or []
            ],
            "tool_ref": resolved.get("tool_id"),
            **NEGATIVE_PROOFS,
        }

        # Deterministic id: same request, same decision, same execution, same id.
        # A random id would make two runs of the certificate produce two evidence
        # records that differ in a field nobody could re-derive.
        evidence_id = "ev-%s" % fingerprint(
            {
                "decision_hash": decision["decision_hash"],
                "execution_id": execution["execution_id"],
                "idempotency_key": request["idempotency_key"],
                "result_status": result["status"],
            }
        )[:32]

        return {
            "evidence_id": evidence_id,
            "record_type": "evidence",
            # TEST, never API_OBSERVATION. `reference-slice.md` section 6:
            # "a green result is TEST / EVAL evidence, never API_OBSERVATION."
            "kind": "TEST",
            "claim": (
                "%s v%s resolved to skill %s@%s and executed to %s in %s mode, "
                "with no provider, adapter, credential, network call or write."
                % (
                    request["capability"]["name"],
                    request["capability"]["contract_version"].lstrip("v"),
                    resolved.get("skill_id"),
                    resolved.get("skill_version"),
                    result["status"],
                    decision["execution_mode"],
                )
            ),
            "source": {"type": "RUNTIME", "ref": execution["execution_id"], "uri": None},
            "provenance": provenance,
            "collected_at": self.clock.now(),
            "classification": request.get("constraints", {}).get(
                "data_classification", "INTERNAL"
            ),
            "integrity": {
                "sha256": fingerprint(provenance),
                "immutable_ref": decision["decision_hash"],
            },
            # Evidence != Knowledge. There is no code path that sets anything
            # else here, and a test asserts it across every packet.
            "knowledge_promotion": {"status": "NOT_REQUESTED", "knowledge_ref": None},
        }

    def _event(
        self,
        request: Mapping[str, Any],
        execution: Mapping[str, Any],
        transition: Transition,
        evidence_id: str,
    ) -> dict[str, Any]:
        caller = request["caller"]
        return {
            "event_id": "evt-%s"
            % fingerprint(
                {
                    "execution_id": execution["execution_id"],
                    "occurred_at": transition.occurred_at,
                    "state": transition.state,
                }
            )[:32],
            "event_type": "runtime.execution.%s" % transition.state,
            "event_version": EVENT_VERSION,
            "occurred_at": transition.occurred_at,
            "correlation_id": request["correlation_id"],
            "request_id": request["request_id"],
            "execution_id": execution["execution_id"],
            # Timestamp, actor and reason on every transition -- the acceptance
            # criterion `#43` quotes verbatim from `#7`.
            "actor": {
                "actor_id": caller["actor_id"],
                "actor_type": caller["actor_type"],
                "tenant_id": caller["tenant_id"],
                "workspace_id": caller["workspace_id"],
            },
            "payload": {"reason": transition.reason, "state": transition.state},
            "evidence_refs": [evidence_id],
        }

    def _validate(
        self, evidence: Mapping[str, Any], events: Sequence[Mapping[str, Any]]
    ) -> None:
        violations = self.contracts.evidence.violations(evidence)
        if violations:
            raise EvidenceWriteError(
                "the writer built a record that is not Evidence v1 at: %s"
                % ", ".join(v.locator for v in violations[:6])
            )
        for event in events:
            violations = self.contracts.event.violations(event)
            if violations:
                raise EvidenceWriteError(
                    "the writer built an event that is not Event v1 at: %s"
                    % ", ".join(v.locator for v in violations[:6])
                )
