"""RK-04 -- Policy + Lifecycle Gate.

A pure function over the RK-07 catalog record plus runtime state. The only place
invariant 16 (``FEATURE`` never executes in production) and invariant 5 (approval
and external gate are independent) are enforced.

It holds no state, mutates no approval, clears no gate, evaluates no business
policy, re-derives no knowledge-corpus eligibility, and accepts no caller
override.

Specification: ``Ginkgo-Core/ai-stack#41``; the normative matrix is
``architecture/runtime/lifecycle-and-promotion.md`` section 3.
"""

from __future__ import annotations

from .gate import (
    ALGORITHM_VERSION,
    ALLOW,
    BLOCK,
    PASSING_APPROVAL_STATES,
    PASSING_GATE_STATE,
    ApprovalState,
    ConstraintContext,
    ExternalGateState,
    GateDecision,
    GateRequest,
    PolicyContext,
    evaluate,
)
from .matrix import (
    CALLER_TRAFFIC_MODES,
    CEILINGS,
    DENIED,
    MATRIX,
    PERMITTED,
    PERMITTED_GOVERNED,
    cell,
)
from .reasons import REASONS, Reason, order, outcome_for

__all__ = [
    "ALGORITHM_VERSION",
    "ALLOW",
    "BLOCK",
    "CALLER_TRAFFIC_MODES",
    "CEILINGS",
    "DENIED",
    "MATRIX",
    "PASSING_APPROVAL_STATES",
    "PASSING_GATE_STATE",
    "PERMITTED",
    "PERMITTED_GOVERNED",
    "REASONS",
    "ApprovalState",
    "ConstraintContext",
    "ExternalGateState",
    "GateDecision",
    "GateRequest",
    "PolicyContext",
    "Reason",
    "cell",
    "evaluate",
    "order",
    "outcome_for",
]
