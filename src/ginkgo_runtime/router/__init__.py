"""RK-02 -- Domain Router (absorbs RK-03 skill/binding resolution).

The only authority permitted to resolve implementation (invariant 4, ADR-001).

It resolves ``capability -> skill/version -> adapter -> provider`` over the
immutable Runtime Catalog and emits one ``RuntimeRouteDecision v1``.

It never assigns an executor, never accepts provider authority from a caller,
never invents a fallback, and never reads a live registry.

Specification: ``Ginkgo-Core/ai-stack#40``; ``capability-runtime-kernel-v1.md``
section 5.
"""

from __future__ import annotations

from .decision import (
    ALGORITHM_VERSION,
    SCHEMA_VERSION,
    VOLATILE_DECISION_FIELDS,
    DecisionValidationError,
    compute_decision_hash,
    compute_decision_inputs_hash,
    compute_input_fingerprint,
    validate_decision,
)
from .policy import (
    PASSING_APPROVAL_STATES,
    PASSING_GATE_STATES,
    PolicyContractError,
    PolicyPort,
    PolicyQuestion,
    PolicyVerdict,
    StaticPolicy,
)
from .router import EXECUTION_MODES, DomainRouter, RoutingContext, to_route_view

__all__ = [
    "ALGORITHM_VERSION",
    "EXECUTION_MODES",
    "PASSING_APPROVAL_STATES",
    "PASSING_GATE_STATES",
    "SCHEMA_VERSION",
    "VOLATILE_DECISION_FIELDS",
    "DecisionValidationError",
    "DomainRouter",
    "PolicyContractError",
    "PolicyPort",
    "PolicyQuestion",
    "PolicyVerdict",
    "RoutingContext",
    "StaticPolicy",
    "compute_decision_hash",
    "compute_decision_inputs_hash",
    "compute_input_fingerprint",
    "to_route_view",
    "validate_decision",
]
