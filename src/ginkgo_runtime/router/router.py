"""RK-02 -- the Domain Router.

Named exactly ``Domain Router``. The only authority permitted to resolve
implementation (invariant 4, ADR-001), and the kernel ships no other component
whose name ends in ``Router``.

.. code-block:: text

    capability + normalized input + internal policy context + immutable catalog
      -> RuntimeRouteDecision

**Absorbs RK-03.** On ``ginkgo-agents@main`` a skill and a binding are one record
discriminated by ``kind``, so resolution is a lookup over one record set, not a
component. Splitting it out creates a second routing authority for zero
capability.

Every non-``ROUTED`` outcome fails closed. **No substitute route is ever
attempted** -- and the shape of this module is what makes that true rather than
merely intended: there is no code path that selects a second candidate, because
there is no candidate ranking anywhere to select from.

Two absences are load-bearing
-----------------------------

**No executor reference.** ``RuntimeRouteDecision`` carries none, and this router
has nothing to put in one. Fusing skill selection and executor selection into a
single decision is the defect the contract exists to prevent (invariant 4). The
Executor Router lives outside the kernel, reached from TaskOps.

**No candidate list.** ``fallback_policy`` authorises a *mode*, never a named
alternative. ``same_semantics_only`` means "a same-semantics substitution would be
permitted if one were proven", and proving one requires a
``same_semantics_proof_ref``. This router never produces one, so it never sets
``fallback.used``. A router that ranked candidates and picked the best surviving
one would be manufacturing exactly the chain P0 froze out.

Ambiguity fails closed
----------------------

When more than one admitted descriptor serves the same capability at the same
contract version, this router emits ``NO_ELIGIBLE_ROUTE`` rather than choosing.

That is deliberate, and it is the same rule as the fallback freeze wearing a
different hat: picking "the first", "the preferred" or "the most mature" is a
ranked candidate chain by another name. ``ginkgo-library``'s registry does declare
a discriminator for this -- ``routing_key`` with ``bindings[].when`` -- and RK-07
does not project it, so no governed basis for choosing exists yet. Until one does,
refusing is the honest answer. Today this affects ``commerce.orders.read``, which
declares two bindings.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..catalog.snapshot import RuntimeCatalog
from ..contracts import FrozenContracts
from .decision import (
    ALGORITHM_VERSION,
    SCHEMA_VERSION,
    compute_decision_hash,
    compute_decision_inputs_hash,
    compute_input_fingerprint,
    validate_decision,
)
from .policy import (
    APPROVAL_STATES,
    GATE_STATES,
    PolicyContractError,
    PolicyPort,
    PolicyQuestion,
)

__all__ = ["EXECUTION_MODES", "DomainRouter", "RoutingContext", "to_route_view"]

#: ``runtime-route-decision.schema.json`` ``execution_mode``.
EXECUTION_MODES = ("certification", "non_production", "production")

#: Modes that serve caller traffic and therefore consult ``routing_eligible``.
#: ``certification`` is excluded on purpose: it is the governed, evidence-producing
#: mode a record uses *before* it is routable, and gating it on the flag would
#: recreate the circularity where a record needs the certificate to run and the
#: run to earn the certificate.
CALLER_TRAFFIC_MODES = ("non_production", "production")

#: ``lifecycle-and-promotion.md`` section 3: ``REJECTED`` is marked "no" in every
#: mode, and "no" is defined as "at any stage of governance". Forcing both
#: eligibility flags false is not enough, because certification consults neither.
NEVER_ROUTABLE_STATUSES = ("REJECTED",)

#: ``DEPRECATED`` may still be certified -- a retired record can be re-run to
#: reproduce historical evidence -- but may not serve caller traffic.
NO_CALLER_TRAFFIC_STATUSES = ("DEPRECATED",)


@dataclass(frozen=True)
class RoutingContext:
    """Everything the router needs that is not in the request.

    ``decision_id`` and ``resolved_at`` are **injected**, not generated. A router
    that called a clock or a UUID factory would not be a pure function of its
    inputs, and reproducibility is the property this component exists to have.
    Where they come from is the host's problem; that they are inputs is the
    router's guarantee.
    """

    execution_mode: str
    decision_id: str
    resolved_at: str
    certification_authorization_ref: str | None = None

    def __post_init__(self) -> None:
        if self.execution_mode not in EXECUTION_MODES:
            raise ValueError(
                "execution_mode %r is not one of %s"
                % (self.execution_mode, list(EXECUTION_MODES))
            )


@dataclass(frozen=True)
class DomainRouter:
    """Resolves ``capability -> skill/version -> adapter -> provider``. Nothing else."""

    catalog: RuntimeCatalog
    policy: PolicyPort
    contracts: FrozenContracts

    # -- the resolution --------------------------------------------------

    def route(self, request: Mapping[str, Any], context: RoutingContext) -> dict[str, Any]:
        """Resolve one admitted request into a ``RuntimeRouteDecision v1``.

        ``request`` has already passed RK-01: it validates against the frozen
        ``CapabilityRequest v1`` and carries no implementation field, so this
        router never has to ask whether the caller tried to choose something.
        """
        capability = dict(request["capability"])
        name = capability["name"]
        version = capability["contract_version"]

        constraints_applied = self._constraints(request.get("constraints") or {})
        fallback_policy = (request.get("constraints") or {}).get("fallback_policy", "none")
        input_fingerprint = compute_input_fingerprint(request.get("input"))

        descriptor, outcome, reason = self._resolve_descriptor(name, version)

        lifecycle = self._lifecycle(descriptor)
        if descriptor is not None and outcome is None:
            outcome, reason = self._lifecycle_guard(lifecycle, context)

        verdict = self._ask_policy(
            PolicyQuestion(
                capability_name=name,
                capability_contract_version=version,
                tenant_id=request["caller"]["tenant_id"],
                workspace_id=request["caller"]["workspace_id"],
                actor_type=request["caller"]["actor_type"],
                execution_mode=context.execution_mode,
                data_classification=constraints_applied.get("data_classification"),
                side_effect_class=self._side_effect_class(descriptor),
                skill_id=None if descriptor is None else self._skill_id(descriptor),
            )
        )
        policy_block = self._policy_block(verdict)

        if outcome is None:
            outcome, reason = self._policy_guard(verdict)

        if outcome is None:
            outcome, reason = self._pin_guard(descriptor)

        if outcome is None:
            outcome, reason = "ROUTED", "resolved to a single eligible descriptor"

        decision = self._build(
            request=request,
            context=context,
            capability=capability,
            outcome=outcome,
            routing_reason=reason,
            descriptor=descriptor,
            lifecycle=lifecycle,
            policy_block=policy_block,
            constraints_applied=constraints_applied,
            fallback_policy=fallback_policy,
            input_fingerprint=input_fingerprint,
        )
        validate_decision(decision, self.contracts)
        return decision

    # -- step 1: which descriptor ----------------------------------------

    def _resolve_descriptor(self, name: str, version: str):
        """Exactly one admitted descriptor, or a fail-closed outcome and why."""
        by_name = self.catalog.skills_for_capability(name)

        if not by_name:
            if self.catalog.capability(name) is None:
                return None, "UNKNOWN_CAPABILITY", (
                    "capability %r is not in the Runtime Catalog at %s"
                    % (name, self.catalog.catalog_version)
                )
            return None, "NO_ELIGIBLE_ROUTE", (
                "capability %r is declared in the catalog but no admitted skill or "
                "binding serves it" % name
            )

        by_version = [
            record
            for record in by_name
            if record["capability"]["contract_version"] == version
        ]
        if not by_version:
            offered = sorted({r["capability"]["contract_version"] for r in by_name})
            return None, "UNKNOWN_CONTRACT_VERSION", (
                "capability %r is served at %s, not at %r"
                % (name, ", ".join(offered), version)
            )

        if len(by_version) > 1:
            claimants = sorted(str(r["record_id"]) for r in by_version)
            return None, "NO_ELIGIBLE_ROUTE", (
                "%d admitted descriptors serve %s@%s (%s); the Domain Router "
                "declines to rank them, because choosing 'the preferred' one is a "
                "ranked candidate chain under another name"
                % (len(claimants), name, version, ", ".join(claimants))
            )

        return by_version[0], None, ""

    # -- step 2: lifecycle ------------------------------------------------

    def _lifecycle(self, descriptor: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if descriptor is None:
            return None
        routing = descriptor["routing"]
        return {
            "production_eligible": bool(routing["production_eligible"]),
            "routing_eligible": bool(routing["routing_eligible"]),
            "status": str(descriptor["lifecycle_status"]),
        }

    def _lifecycle_guard(self, lifecycle: Mapping[str, Any] | None, context: RoutingContext):
        """The section 3 matrix, applied per mode. Nothing here grants anything."""
        assert lifecycle is not None
        status = lifecycle["status"]
        mode = context.execution_mode

        if status in NEVER_ROUTABLE_STATUSES:
            return "BLOCKED_LIFECYCLE", (
                "lifecycle_status %s is terminal and is not routable in any mode, "
                "certification included" % status
            )

        if mode in CALLER_TRAFFIC_MODES and status in NO_CALLER_TRAFFIC_STATUSES:
            return "BLOCKED_LIFECYCLE", (
                "lifecycle_status %s may be certified but may not serve caller "
                "traffic in %s" % (status, mode)
            )

        if mode == "certification":
            # Neither eligibility flag is consulted; the governance is the
            # recorded authorization instead.
            if not context.certification_authorization_ref:
                return "BLOCKED_LIFECYCLE", (
                    "certification mode requires a recorded certification "
                    "authorization naming the skill, the mode and the gate; "
                    "without one it would be an unaudited bypass of the ladder"
                )
            return None, ""

        if not lifecycle["routing_eligible"]:
            return "BLOCKED_LIFECYCLE", (
                "lifecycle_status %s is not routing_eligible, so it cannot serve "
                "%s traffic" % (status, mode)
            )

        if mode == "production" and not lifecycle["production_eligible"]:
            return "BLOCKED_LIFECYCLE", (
                "lifecycle_status %s is not production_eligible" % status
            )

        return None, ""

    # -- step 3: policy ---------------------------------------------------

    def _ask_policy(self, question: PolicyQuestion):
        """Call the port, and treat everything it can do wrong as a contract breach.

        The port is foreign code -- RK-04 is not implemented here and whatever
        eventually implements it lives behind this seam. Two failures are
        possible and neither may become a routing decision:

        * it **raises**. The exception message is downstream text that routinely
          quotes a connection string, so only its *type* is reported;
        * it returns a verdict outside the frozen enums. That is not a denial,
          and recording ``BLOCKED_POLICY`` for it would put a governance verdict
          into the audit record that no policy authority ever gave.
        """
        try:
            verdict = self.policy.evaluate(question)
        except Exception as exc:
            raise PolicyContractError(
                "the policy authority raised %s instead of returning a verdict"
                % type(exc).__name__
            ) from None

        if verdict.decision not in ("ALLOW", "DENY"):
            raise PolicyContractError(
                "the policy authority returned a decision outside the frozen enum "
                "ALLOW|DENY"
            )
        if verdict.approval_state not in APPROVAL_STATES:
            raise PolicyContractError(
                "the policy authority returned an approval state outside the "
                "frozen enum %s" % list(APPROVAL_STATES)
            )
        for gate in verdict.external_gates:
            if gate.get("state") not in GATE_STATES:
                raise PolicyContractError(
                    "the policy authority returned an external gate state outside "
                    "the frozen enum %s" % list(GATE_STATES)
                )
        return verdict

    def _policy_block(self, verdict) -> dict[str, Any]:
        block: dict[str, Any] = {
            "decision": verdict.decision,
            "policy_id": verdict.policy_id,
            "policy_snapshot_hash": verdict.snapshot_hash(),
            "policy_version": verdict.policy_version,
            "approval": {
                "required": bool(verdict.approval_required),
                "state": verdict.approval_state,
            },
            "external_gates": [dict(gate) for gate in verdict.external_gates],
        }
        if verdict.approval_refs:
            block["approval"]["approval_refs"] = list(verdict.approval_refs)
        return block

    def _policy_guard(self, verdict):
        """Report RK-04's verdict. This router re-decides nothing.

        The precedence -- denial, then gates, then approval -- is fixed so the
        outcome code is deterministic when more than one applies. Gates sit above
        approval because invariant 5 makes a gate un-clearable *by* an approval:
        ``APPROVED + BLOCKED => BLOCKED`` and ``APPROVED + UNKNOWN => BLOCKED``.
        Reporting ``BLOCKED_APPROVAL`` there would name the wrong cause and
        suggest an approval would fix it.
        """
        if not verdict.allowed:
            return "BLOCKED_POLICY", "policy %s v%s returned DENY" % (
                verdict.policy_id,
                verdict.policy_version,
            )

        blocking = verdict.blocking_gates()
        if blocking:
            named = ", ".join(
                "%s=%s" % (gate.get("gate_id"), gate.get("state")) for gate in blocking
            )
            return "BLOCKED_EXTERNAL_GATE", (
                "external gate(s) not CLEAR: %s. An approval never clears a gate "
                "(invariant 5)" % named
            )

        if verdict.approval_blocks:
            return "BLOCKED_APPROVAL", (
                "approval is required and its state is %s" % verdict.approval_state
            )

        return None, ""

    # -- step 4: the pin --------------------------------------------------

    def _skill_id(self, descriptor: Mapping[str, Any]) -> str | None:
        return descriptor["implementation_binding"].get("skill_id")

    def _side_effect_class(self, descriptor: Mapping[str, Any] | None) -> str | None:
        if descriptor is None:
            return None
        value = descriptor.get("side_effect_class")
        return str(value) if isinstance(value, str) else None

    def _pin_guard(self, descriptor: Mapping[str, Any] | None):
        """A ROUTED decision must pin a skill, a version and a side-effect class.

        ``RuntimeRouteDecision`` requires ``skill_id``, ``skill_version``, ``kind``
        and ``side_effect_class`` on a ``ROUTED`` outcome. RK-07 admits legacy
        records that predate the full descriptor contract and reports the gap
        rather than filling it, so a descriptor can be admissible and still not
        pinnable. The router refuses rather than inventing the missing value.
        """
        assert descriptor is not None
        binding = descriptor["implementation_binding"]

        missing = [
            field
            for field, value in (
                ("skill_id", binding.get("skill_id")),
                ("skill_version", binding.get("skill_version")),
                ("kind", descriptor.get("kind")),
                ("side_effect_class", self._side_effect_class(descriptor)),
            )
            if not value
        ]
        if missing:
            return "NO_ELIGIBLE_ROUTE", (
                "descriptor %s is admitted but does not declare %s, and the Domain "
                "Router does not invent what a record leaves undeclared"
                % (descriptor["record_id"], ", ".join(missing))
            )

        for identity, reason_field in (
            ("adapter_id", "adapter_not_applicable_reason"),
            ("provider", "provider_not_applicable_reason"),
        ):
            if binding.get(identity) is None and not binding.get(reason_field):
                return "NO_ELIGIBLE_ROUTE", (
                    "descriptor %s declares %s: null with no %s; an honest null "
                    "needs its reason and the router will not write one"
                    % (descriptor["record_id"], identity, reason_field)
                )

        return None, ""

    # -- assembling the record -------------------------------------------

    def _constraints(self, constraints: Mapping[str, Any]) -> dict[str, Any]:
        """Only the provider-agnostic constraints the frozen contract permits."""
        applied: dict[str, Any] = {}
        for field in ("deadline", "max_cost", "data_classification"):
            if field in constraints:
                applied[field] = constraints[field]
        return applied

    def _resolved(self, outcome: str, descriptor: Mapping[str, Any] | None, capability_name: str):
        resolved: dict[str, Any] = {"outcome": outcome}
        if descriptor is None or outcome != "ROUTED":
            # A non-ROUTED decision names no implementation. Reporting the
            # descriptor it *would* have chosen would leak a route the policy or
            # the ladder refused, and would read as a suggestion.
            return resolved

        binding = descriptor["implementation_binding"]
        resolved.update(
            {
                # ADR-002's grammar is `domain.object.action`, so the first
                # segment is the domain. The descriptor declares no domain field.
                "domain": capability_name.split(".", 1)[0],
                "skill_id": binding["skill_id"],
                "skill_version": binding["skill_version"],
                "kind": descriptor["kind"],
                "binding_id": binding.get("binding_id"),
                "adapter_id": binding.get("adapter_id"),
                "provider": binding.get("provider"),
                "tool_id": binding.get("tool_id"),
            }
        )
        for identity, reason_field in (
            ("adapter_id", "adapter_not_applicable_reason"),
            ("provider", "provider_not_applicable_reason"),
        ):
            if resolved[identity] is None:
                resolved[reason_field] = binding.get(reason_field)
        return resolved

    def _build(self, **parts: Any) -> dict[str, Any]:
        request = parts["request"]
        context: RoutingContext = parts["context"]
        outcome = parts["outcome"]
        descriptor = parts["descriptor"]

        catalog_block = {
            "catalog_version": self.catalog.catalog_version,
            "pinned_refs": self._pinned_refs(),
        }

        decision: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "decision_id": context.decision_id,
            "request_id": request["request_id"],
            "correlation_id": request["correlation_id"],
            "capability": parts["capability"],
            "resolved": self._resolved(outcome, descriptor, parts["capability"]["name"]),
            "execution_mode": context.execution_mode,
            # `used` is always false: this router never performs a substitution,
            # so it never has a same-semantics proof to reference.
            "fallback": {"policy": parts["fallback_policy"], "used": False},
            "catalog": catalog_block,
            "policy": parts["policy_block"],
            "routing_reason": self._clamp_reason(parts["routing_reason"]),
            "algorithm_version": ALGORITHM_VERSION,
            "resolved_at": context.resolved_at,
            "input_fingerprint": parts["input_fingerprint"],
            "decision_inputs_hash": "",
            "decision_hash": "",
        }

        if request.get("task_id"):
            decision["task_id"] = request["task_id"]
        if parts["constraints_applied"]:
            decision["constraints_applied"] = parts["constraints_applied"]
        if parts["lifecycle"] is not None:
            decision["lifecycle"] = parts["lifecycle"]
        side_effect_class = self._side_effect_class(descriptor)
        if outcome == "ROUTED" and side_effect_class:
            decision["side_effect_class"] = side_effect_class
        if context.execution_mode == "certification":
            decision["certification_authorization_ref"] = (
                context.certification_authorization_ref
            )

        decision["decision_inputs_hash"] = compute_decision_inputs_hash(
            capability=parts["capability"],
            input_fingerprint=parts["input_fingerprint"],
            execution_mode=context.execution_mode,
            constraints_applied=parts["constraints_applied"],
            catalog=catalog_block,
            policy=parts["policy_block"],
            fallback_policy=parts["fallback_policy"],
        )
        decision["decision_hash"] = compute_decision_hash(decision)
        return decision

    def _clamp_reason(self, reason: str) -> str:
        """Keep ``routing_reason`` inside the frozen ``maxLength``.

        An ambiguity reason names every claimant, so a capability served by a few
        hundred descriptors produces a reason longer than the contract permits --
        and the router then failed its own artifact rather than explaining why it
        refused. The limit comes from the schema.
        """
        text = reason or "no reason recorded"
        limit = self.contracts.routing_reason_max_length
        if len(text) <= limit:
            return text
        marker = " ... (truncated)"
        return text[: limit - len(marker)] + marker

    def _pinned_refs(self) -> list[dict[str, Any]]:
        """The exact commits the catalog was projected from.

        *"This is what makes a routing decision reproducible after a restart."*
        One entry per repository, deduplicated -- the schema requires uniqueness
        and the per-file paths belong to the catalog, not to this decision.
        """
        seen: dict[str, str] = {}
        for source in self.catalog.pinned_sources:
            seen.setdefault(str(source["repo"]), str(source["commit_sha"]))
        return [
            {"repository": repo, "commit": commit, "path": None}
            for repo, commit in sorted(seen.items())
        ]


def to_route_view(decision: Mapping[str, Any]):
    """Project a decision into the read-only slice RK-01 consumes.

    The seam between the two components, and it is narrow on purpose: RK-01 needs
    identity and routing provenance to build a ``CapabilityResult``, and nothing
    else in the decision is its business.
    """
    from ..bus import RouteView

    resolved = decision["resolved"]
    policy = decision.get("policy") or {}
    approval = policy.get("approval") or {"required": False, "state": "NOT_REQUIRED"}
    return RouteView(
        outcome=resolved["outcome"],
        domain=resolved.get("domain"),
        skill_id=resolved.get("skill_id"),
        skill_version=resolved.get("skill_version"),
        adapter_id=resolved.get("adapter_id"),
        provider=resolved.get("provider"),
        fallback_used=bool(decision["fallback"]["used"]),
        routing_reason=decision["routing_reason"],
        # `CapabilityResult v1` names this field `status`; `approval.schema.json`
        # and this decision name it `state`. RK-01 owns that translation, so the
        # view hands over the decision's spelling unchanged.
        approval={"required": approval["required"], "status": approval["state"]},
        external_gates=tuple(dict(gate) for gate in policy.get("external_gates") or ()),
    )
