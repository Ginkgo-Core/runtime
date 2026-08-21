"""The internal routing interface, and a deterministic fake to test the Bus against.

RK-01 hands an admitted request across one seam and gets one answer back. What
lives behind the seam is RK-04 (Policy + Lifecycle Gate) and RK-02 (Domain
Router), and **neither is implemented here** -- `ai-stack#39` depends on `#40` and
`#41`, and this PR implements neither.

So the seam is a Protocol, and the only implementation in this repository is
:class:`DeterministicDownstream`: a scripted fake with no routing logic in it at
all. It answers from a table the test wrote. That is the point -- a fake that
resolved anything would be a second routing authority, which is the specific
thing invariant 4 forbids.

**The Bus consumes a decision; it never produces one.** ``CapabilityResult v1``
requires ``skill_id``, ``skill_version`` and the ``routing`` block, and those are
outputs of resolution. :class:`RouteView` is the read-only projection of
``RuntimeRouteDecision`` carrying exactly those fields and nothing else -- in
particular no ``decision_hash`` to recompute and no candidate list to rank.

A contract tension, surfaced rather than papered over
-----------------------------------------------------

``capability-result.schema.json`` requires ``skill_id`` and ``skill_version`` as
**non-empty strings**, not nullable. Reference slice N12 requires an unknown
capability name to produce a ``BLOCKED`` result. Those two cannot both hold: a
capability that resolved to nothing has no skill to name, and emitting one would
be an invented identity.

RK-01 does not resolve that here. It fails closed: a blocked outcome that carries
no skill identity yields a **rejection**, not a fabricated result. N9-N12 belong
to `#40` and `#41`, and the contract question belongs with them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

__all__ = [
    "BLOCKED_OUTCOMES",
    "DeterministicDownstream",
    "DownstreamOutcome",
    "RouteView",
    "RuntimeDownstream",
]

#: ``runtime-route-decision.schema.json`` ``resolved.outcome``, minus ``ROUTED``.
#: Every one of them fails closed and produces no substitute route.
BLOCKED_OUTCOMES = (
    "BLOCKED_LIFECYCLE",
    "BLOCKED_POLICY",
    "BLOCKED_APPROVAL",
    "BLOCKED_EXTERNAL_GATE",
    "NO_ELIGIBLE_ROUTE",
    "UNKNOWN_CAPABILITY",
    "UNKNOWN_CONTRACT_VERSION",
)


@dataclass(frozen=True)
class RouteView:
    """The read-only slice of ``RuntimeRouteDecision`` the Bus needs to build a result.

    Deliberately narrow. ``RuntimeRouteDecision`` also carries ``decision_hash``,
    ``policy_snapshot``, ``execution_mode`` and more; none of it is the Bus's
    business, and a wider view would invite the Bus to start reasoning about
    routing.
    """

    outcome: str
    domain: str | None = None
    skill_id: str | None = None
    skill_version: str | None = None
    adapter_id: str | None = None
    provider: str | None = None
    fallback_used: bool = False
    routing_reason: str = ""
    approval: Mapping[str, Any] = field(
        default_factory=lambda: {"required": False, "status": "NOT_REQUIRED"}
    )
    external_gates: tuple[Mapping[str, Any], ...] = ()
    evidence_refs: tuple[str, ...] = ()
    cost_events: tuple[Mapping[str, Any], ...] = ()

    @property
    def routed(self) -> bool:
        return self.outcome == "ROUTED"


@dataclass(frozen=True)
class DownstreamOutcome:
    """One answer from behind the seam.

    ``execution`` is a ``RuntimeExecutionResult v1`` document, or ``None`` when the
    decision blocked before anything ran.
    """

    decision: RouteView
    execution: Mapping[str, Any] | None = None


class RuntimeDownstream(Protocol):
    """What RK-01 requires of everything downstream of it.

    One method. The Bus passes the admitted request and receives one outcome; it
    does not choose a skill, a provider, an adapter or an executor, and it cannot,
    because nothing in this signature lets it express such a choice.
    """

    def dispatch(self, request: Mapping[str, Any]) -> DownstreamOutcome:
        ...


@dataclass
class DeterministicDownstream:
    """A scripted stand-in. No routing logic, by construction.

    Answers come from ``by_capability``, keyed on ``capability.name``; anything
    unscripted returns ``UNKNOWN_CAPABILITY``. It records every request it was
    handed, which is how the replay tests prove the Bus did **not** dispatch a
    second time (reference slice P7).
    """

    by_capability: dict[str, DownstreamOutcome] = field(default_factory=dict)
    calls: list[Mapping[str, Any]] = field(default_factory=list)

    def dispatch(self, request: Mapping[str, Any]) -> DownstreamOutcome:
        self.calls.append(request)
        name = request["capability"]["name"]
        scripted = self.by_capability.get(name)
        if scripted is not None:
            return scripted
        return DownstreamOutcome(
            decision=RouteView(
                outcome="UNKNOWN_CAPABILITY",
                routing_reason="no scripted route for %s" % name,
            )
        )

    @property
    def call_count(self) -> int:
        return len(self.calls)
