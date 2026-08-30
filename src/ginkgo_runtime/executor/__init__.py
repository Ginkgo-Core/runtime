"""Governed executor boundaries and deterministic implementations.

The Executor Router selects; the Executor Contract normalizes. RK-08A provides
the canonical boundary, RK-09 the in-process reference executor, and RK-08B/A
the offline n8n adapter plus in-memory transport.

No HTTP transport, live n8n, retries, credential values, or TaskOps state lives
in this package.

Specification: ``Ginkgo-Core/ai-stack#32`` and ``#42``;
``architecture/runtime/executor-contract.md`` and ``reference-slice.md``.
"""

from __future__ import annotations

from .artifact import WorkflowArtifactDescriptor, WorkflowArtifactError, WorkflowArtifactPin
from .contract import (
    DEFAULT_TIMEOUT_MS,
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
    EXECUTION_DISPOSITIONS,
    N8N_STATE_MAP,
    InMemoryN8nTransport,
    N8nExecutorAdapter,
    N8nTransport,
    N8nTransportCancelled,
    N8nTransportError,
    N8nTransportRequest,
    N8nTransportTimeout,
    normalize_n8n_state,
)

__all__ = [
    "DEFAULT_TIMEOUT_MS",
    "EXECUTION_DISPOSITIONS",
    "N8N_STATE_MAP",
    "TERMINAL_STATES",
    "TOOLS",
    "TOOL_ID",
    "EchoInputError",
    "ExecutionEnvelopeError",
    "Executor",
    "InMemoryN8nTransport",
    "InProcessExecutor",
    "N8nExecutorAdapter",
    "N8nTransport",
    "N8nTransportCancelled",
    "N8nTransportError",
    "N8nTransportRequest",
    "N8nTransportTimeout",
    "Transition",
    "WorkflowArtifactDescriptor",
    "WorkflowArtifactError",
    "WorkflowArtifactPin",
    "build_execution_request",
    "build_execution_result",
    "echo_transform",
    "normalize_n8n_state",
]
