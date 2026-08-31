"""RK-08A -- Executor Contract, RK-09 -- the reference skill, RK-08B-A -- the n8n bridge.

The Executor Router selects; the Executor Contract normalizes. Only the second
half is here, now with two implementations behind it:

* :class:`InProcessExecutor` -- runs one pure tool in this process, so the
  reference slice runs with zero containers, zero network and zero credentials;
* :class:`~ginkgo_runtime.executor.n8n.N8nExecutor` -- runs a bound workflow
  through n8n, offline, over a transport port whose only shipped implementation
  replays recorded observations.

Two implementations, one signature, one result builder. That is what makes
RK-08B's promotion gate -- *"same TaskOps job executed by n8n and a stub executor
with equivalent normalized result"* -- a swap rather than an argument.

Neither executor selects anything, and n8n gains no authority by being reachable
(invariant 11). No live transport, no retries, no cancellation initiation, no
lease: those are RK-08B-B and later.

Specification: ``Ginkgo-Core/ai-stack#32``, ``#42`` and ``#44``;
``architecture/runtime/executor-contract.md`` and ``reference-slice.md``.
"""

from __future__ import annotations

from .contract import (
    DEFAULT_TIMEOUT_MS,
    IN_PROCESS_EXECUTOR_REF,
    NON_TERMINAL_STATES,
    TERMINAL_STATES,
    TOOLS,
    ExecutionEnvelopeError,
    Executor,
    InProcessExecutor,
    Transition,
    build_execution_request,
    build_execution_result,
)
from .echo import TOOL_ID, EchoInputError, echo_transform
from .n8n import (
    N8N_EXECUTOR_REF,
    N8N_INTERNAL_KEYS,
    N8N_STATUS_TO_STATE,
    N8nBinding,
    N8nExecutor,
    N8nObservation,
    N8nTransport,
    N8nTransportError,
    RecordedTransport,
    project_output,
)

__all__ = [
    "DEFAULT_TIMEOUT_MS",
    "IN_PROCESS_EXECUTOR_REF",
    "N8N_EXECUTOR_REF",
    "N8N_INTERNAL_KEYS",
    "N8N_STATUS_TO_STATE",
    "NON_TERMINAL_STATES",
    "TERMINAL_STATES",
    "TOOLS",
    "TOOL_ID",
    "EchoInputError",
    "ExecutionEnvelopeError",
    "Executor",
    "InProcessExecutor",
    "N8nBinding",
    "N8nExecutor",
    "N8nObservation",
    "N8nTransport",
    "N8nTransportError",
    "RecordedTransport",
    "Transition",
    "build_execution_request",
    "build_execution_result",
    "echo_transform",
    "project_output",
]
