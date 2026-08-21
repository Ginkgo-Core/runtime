"""RK-08A -- Executor Contract, and RK-09 -- the reference deterministic skill.

The Executor Router selects; the Executor Contract normalizes. Only the second
half is here, with exactly one implementation behind it: an in-process executor,
so the reference slice runs with zero containers, zero network and zero
credentials.

No n8n bridge (RK-08B is Wave 2), no retries, no cancellation, no lease.

Specification: ``Ginkgo-Core/ai-stack#32`` and ``#42``;
``architecture/runtime/executor-contract.md`` and ``reference-slice.md``.
"""

from __future__ import annotations

from .contract import (
    DEFAULT_TIMEOUT_MS,
    TERMINAL_STATES,
    TOOLS,
    ExecutionEnvelopeError,
    Executor,
    InProcessExecutor,
    Transition,
    build_execution_request,
)
from .echo import TOOL_ID, EchoInputError, echo_transform

__all__ = [
    "DEFAULT_TIMEOUT_MS",
    "TERMINAL_STATES",
    "TOOLS",
    "TOOL_ID",
    "EchoInputError",
    "ExecutionEnvelopeError",
    "Executor",
    "InProcessExecutor",
    "Transition",
    "build_execution_request",
    "echo_transform",
]
