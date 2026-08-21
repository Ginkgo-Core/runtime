"""The Policy port. An interface, and deliberately not an implementation.

RK-04 is the Policy + Lifecycle Gate and it is **not built here** — `ai-stack#41`
owns it. But `RuntimeRouteDecision` requires a `policy` block, so RK-02 needs
somewhere to get one from.

The split is the one the architecture already states, and it is worth stating
precisely because it is easy to blur:

> **RK-04 decides; RK-02 records the pinned verdict and stops.**
> *"The `BLOCKED_POLICY`, `BLOCKED_APPROVAL` and `BLOCKED_EXTERNAL_GATE` outcomes
> on `RuntimeRouteDecision` are RK-02 reporting RK-04's decision in the audit
> record, never RK-02 re-deciding it."* — `capability-runtime-kernel-v1.md` §5

So this module defines what RK-02 *requires* of a policy authority and nothing
about how one reaches its answer. :class:`StaticPolicy` returns exactly the
verdict it was constructed with; it evaluates nothing, and it is here so the
router can be tested without RK-04 existing.

**`policy_snapshot_hash` is computed here, not accepted here.** The port returns
the pinned policy *snapshot* — the actual state the verdict was reached against —
and RK-02 hashes it canonically. Taking a hash the port asserted would let a
policy authority claim provenance for a snapshot nobody can check; hashing the
snapshot means the decision proves what policy it actually saw.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..canonical import fingerprint

__all__ = [
    "APPROVAL_STATES",
    "GATE_STATES",
    "PASSING_APPROVAL_STATES",
    "PASSING_GATE_STATES",
    "PolicyContractError",
    "PolicyPort",
    "PolicyQuestion",
    "PolicyVerdict",
    "StaticPolicy",
]

#: ``contracts/core/approval.schema.json``, frozen. Reproduced as a tuple only for
#: the port's own sanity check; the authority is the schema and RK-01's result
#: envelope validates against it.
APPROVAL_STATES = ("NOT_REQUIRED", "PENDING", "APPROVED", "REJECTED", "EXPIRED")

#: ``contracts/core/external-gate.schema.json``, frozen.
GATE_STATES = ("CLEAR", "BLOCKED", "UNKNOWN", "EXPIRED")

#: The only approval states that do not block. Invariant 5 keeps this independent
#: of the gate set below: an approval never clears a gate.
PASSING_APPROVAL_STATES = ("NOT_REQUIRED", "APPROVED")

#: The only gate state that does not block. ``UNKNOWN`` blocks -- fail-closed is
#: the whole point of a gate, and ``ARCHITECTURE-INVARIANTS`` spells out
#: ``APPROVED + UNKNOWN => BLOCKED``.
PASSING_GATE_STATES = ("CLEAR",)


@dataclass(frozen=True)
class PolicyQuestion:
    """What RK-02 hands the policy authority.

    Everything here is either the public capability or *internal* context derived
    by Identity/Policy. Nothing a caller supplied about implementation appears,
    because nothing a caller supplies about implementation survives RK-01.
    """

    capability_name: str
    capability_contract_version: str
    tenant_id: str
    workspace_id: str
    actor_type: str
    execution_mode: str
    data_classification: str | None = None
    side_effect_class: str | None = None
    skill_id: str | None = None


@dataclass(frozen=True)
class PolicyVerdict:
    """What the policy authority hands back. RK-02 records it and stops."""

    policy_id: str
    policy_version: str
    decision: str
    #: The pinned policy state the verdict was reached against. RK-02 hashes this
    #: into ``policy.policy_snapshot_hash``; it never reaches the decision in
    #: cleartext, and it never reaches a caller at all.
    snapshot: Mapping[str, Any] = field(default_factory=dict)
    approval_required: bool = False
    approval_state: str = "NOT_REQUIRED"
    approval_refs: Sequence[str] = ()
    external_gates: Sequence[Mapping[str, Any]] = ()

    def snapshot_hash(self) -> str:
        """SHA-256 over a type-tagged canonical encoding of the pinned snapshot.

        Type-tagged rather than the catalog's stricter encoder, because policy
        snapshots carry numeric thresholds: ``commerce-evaluation-gates-v1``
        declares ``minimum_multiplier: 5.0``. Refusing floats here crashed the
        router on a real policy.
        """
        return fingerprint(dict(self.snapshot))

    @property
    def allowed(self) -> bool:
        return self.decision == "ALLOW"

    def blocking_gates(self) -> tuple[Mapping[str, Any], ...]:
        """Gates that are not ``CLEAR``.

        Evaluated with no reference to approval whatsoever. That independence is
        invariant 5, and expressing it as a separate function is how it stays
        true: there is no code path where an approval state can reach this.
        """
        return tuple(
            gate for gate in self.external_gates if gate.get("state") not in PASSING_GATE_STATES
        )

    @property
    def approval_blocks(self) -> bool:
        return self.approval_required and self.approval_state not in PASSING_APPROVAL_STATES


class PolicyContractError(RuntimeError):
    """The policy authority did not answer the way the port requires.

    Raised rather than absorbed into a ``BLOCKED_POLICY`` decision: a port that
    returns an out-of-enum verdict has not denied anything, and recording a
    denial it never made would put a fabricated governance verdict into the audit
    record. The message names the field and never the value -- a snapshot may
    carry credential identifiers.
    """


class PolicyPort(Protocol):
    """The one thing RK-02 requires of RK-04.

    One method. RK-02 cannot influence the verdict, cannot retry it with
    different context, and cannot substitute its own — the signature does not
    allow any of it.
    """

    def evaluate(self, question: PolicyQuestion) -> PolicyVerdict:
        ...


@dataclass(frozen=True)
class StaticPolicy:
    """A stand-in that returns one prepared verdict. It decides nothing.

    Not an implementation of RK-04 and not a simplified one: there is no rule
    evaluation in it at all. It exists so the router's own behaviour can be
    tested against every verdict shape without `ai-stack#41` existing.
    """

    verdict: PolicyVerdict

    def evaluate(self, question: PolicyQuestion) -> PolicyVerdict:
        return self.verdict

    @classmethod
    def allowing(cls, **overrides: Any) -> StaticPolicy:
        defaults: dict[str, Any] = {
            "policy_id": "rk02-test-policy",
            "policy_version": "0",
            "decision": "ALLOW",
            "snapshot": {"rules": [], "note": "stand-in; RK-04 is not implemented"},
        }
        defaults.update(overrides)
        return cls(verdict=PolicyVerdict(**defaults))
