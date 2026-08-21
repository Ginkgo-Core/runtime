"""Building, hashing and validating a ``RuntimeRouteDecision``.

Three hashes, answering three different questions. Keeping them distinct is the
point of the record:

``input_fingerprint``
    SHA-256 over the normalized business input. *"Never the input itself"* — the
    schema says so, and it is why a decision can be persisted and audited without
    persisting caller data. Uses the type-tagged fingerprint rather than the
    catalog's canonical encoder, because business input is caller data and may
    legally contain floats.

All three use the **type-tagged** fingerprint rather than the catalog's canonical
encoder. The catalog refuses floats, correctly, because a float in a projection is
a defect. A routing decision is different: ``constraints_applied.max_cost.amount``
is declared ``number`` in the frozen contract, so ``19.99`` is an ordinary
constraint, and a policy snapshot carries thresholds like ``minimum_multiplier:
5.0``. Using the stricter encoder here meant a perfectly legal request crashed the
router.

``decision_inputs_hash``
    Over everything that *determined* the outcome: the capability, the input
    fingerprint, the execution mode, the applied constraints, the catalog version
    and its pinned commits, the policy identity and snapshot hash, the fallback
    policy, and the algorithm version. Two decisions with the same
    ``decision_inputs_hash`` were asked the same question.

``decision_hash``
    Over the whole decision **minus its volatile identity**. Two decisions with
    the same ``decision_hash`` are the same answer.

That last exclusion is the one that matters, and it is deliberate.
``decision_id``, ``resolved_at``, ``request_id`` and ``correlation_id`` differ on
every call by construction. Hashing them would make ``decision_hash`` a unique id
— it would still validate, still look reproducible, and never once be equal for
two identical routings. The requirement is *identical inputs produce an identical
hash*, so what varies per call is outside it.

The hashing model is generalized from ``FrozenDecision`` in
``ginkgo-connect/packages/core``, as `ai-stack#40` directs — the freeze/hash/pin
triple, not the package. Nothing is imported from it, and its
``rankedFallbacks`` and ``chosenExecutorRef`` are absent here by design rather
than by omission.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..canonical import fingerprint
from ..contracts import FrozenContracts

__all__ = [
    "ALGORITHM_VERSION",
    "SCHEMA_VERSION",
    "VOLATILE_DECISION_FIELDS",
    "DecisionValidationError",
    "compute_decision_hash",
    "compute_decision_inputs_hash",
    "compute_input_fingerprint",
    "validate_decision",
]

SCHEMA_VERSION = "ginkgo.runtime-route-decision.v1"

#: Bump when the resolution algorithm's *meaning* changes. It is part of
#: ``decision_inputs_hash``, so a silent change in how routing resolves cannot
#: reuse an old hash and pretend the answer is unchanged.
ALGORITHM_VERSION = "ginkgo.domain-router.v1"

#: Excluded from ``decision_hash``. Every one of these differs per call by
#: construction; see the module docstring for why including them would make the
#: hash useless while looking correct.
VOLATILE_DECISION_FIELDS = (
    "correlation_id",
    "decision_hash",
    "decision_id",
    "request_id",
    "resolved_at",
    "task_id",
)


class DecisionValidationError(RuntimeError):
    """The router built a decision that is not a ``RuntimeRouteDecision v1``.

    Raised rather than returned: a router that emits a malformed decision has a
    defect, and handing that decision downstream would spread it.
    """


def compute_input_fingerprint(normalized_input: Any) -> str | None:
    """SHA-256 over the normalized business input, or ``None`` when there is none."""
    if normalized_input is None:
        return None
    return fingerprint(normalized_input)


def compute_decision_inputs_hash(
    *,
    capability: Mapping[str, Any],
    input_fingerprint: str | None,
    execution_mode: str,
    constraints_applied: Mapping[str, Any],
    catalog: Mapping[str, Any],
    policy: Mapping[str, Any],
    fallback_policy: str,
) -> str:
    """Hash the question, not the answer."""
    return fingerprint(
        {
            "algorithm_version": ALGORITHM_VERSION,
            "capability": dict(capability),
            "catalog": {
                "catalog_version": catalog["catalog_version"],
                "pinned_refs": [dict(ref) for ref in catalog["pinned_refs"]],
            },
            "constraints_applied": dict(constraints_applied),
            "execution_mode": execution_mode,
            "fallback_policy": fallback_policy,
            "input_fingerprint": input_fingerprint,
            # Policy IDENTITY and the snapshot hash -- never the snapshot itself.
            # The snapshot may name internal rules; the decision proves which
            # snapshot was seen without carrying it.
            "policy": {
                "decision": policy["decision"],
                "policy_id": policy["policy_id"],
                "policy_snapshot_hash": policy["policy_snapshot_hash"],
                "policy_version": policy["policy_version"],
            },
        }
    )


def compute_decision_hash(decision: Mapping[str, Any]) -> str:
    """Hash the answer, with per-call identity excluded."""
    stable = {
        key: value for key, value in decision.items() if key not in VOLATILE_DECISION_FIELDS
    }
    return fingerprint(stable)


def validate_decision(decision: Mapping[str, Any], contracts: FrozenContracts) -> None:
    """Assert the decision satisfies the frozen contract, and carries nothing forbidden.

    Two checks, because they catch different things. The schema is
    ``additionalProperties: false``, so a forbidden field is *already* structurally
    impossible — but ``x-ginkgo-forbidden-fields`` names ``ranked_fallbacks`` and
    ``executor_ref`` specifically, and `ai-stack#40`'s Done-when asks for
    confirmation that they are rejected. Asserting it directly means the guarantee
    survives even if the schema ever stops being closed.
    """
    violations = contracts.runtime_route_decision.violations(decision)
    if violations:
        raise DecisionValidationError(
            "the router built a decision that is not RuntimeRouteDecision v1: %s"
            % "; ".join(str(v) for v in violations[:8])
        )

    present = _forbidden_present(decision, contracts.route_decision_forbidden_fields)
    if present:
        raise DecisionValidationError(
            "the decision carries fields the contract forbids: %s" % ", ".join(present)
        )


def _forbidden_present(node: Any, forbidden: tuple[str, ...], found: list[str] | None = None):
    out = [] if found is None else found
    if isinstance(node, Mapping):
        for key, value in node.items():
            if str(key) in forbidden:
                out.append(str(key))
            _forbidden_present(value, forbidden, out)
    elif isinstance(node, list | tuple):
        for item in node:
            _forbidden_present(item, forbidden, out)
    return sorted(set(out))
