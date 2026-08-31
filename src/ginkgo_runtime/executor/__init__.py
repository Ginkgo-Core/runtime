"""RK-08A -- Executor Contract, RK-09 -- the reference skill, RK-08B -- the n8n bridge.

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

Both are behind one credential boundary. :mod:`ginkgo_runtime.executor.credentials`
holds the rule; the inbound half refuses caller-controlled input carrying a
credential before any transport is touched, and the outbound half refuses to build
a result carrying one at all.

Neither executor selects anything, and n8n gains no authority by being reachable
(invariant 11). RK-08B-B makes it reachable and changes nothing about that: the
live transport is a second implementation of the same port, and it selects
nothing, decides nothing and normalises nothing -- the failure vocabulary it
reports into is declared in :mod:`ginkgo_runtime.executor.n8n`, in the Contract.

**Importing this package still opens no socket.**
:mod:`ginkgo_runtime.executor.n8n_http_client` is the one module in the kernel
that may import a network library, and it is deliberately *not* re-exported here.
Reaching a network takes an explicit import of that module by name, which is a
line in a diff rather than a transitive consequence of importing the package that
every other component already imports. Everything else the live transport needs
-- endpoint, pins, credential resolution, dispatch evidence -- has no socket and
is exported normally.

No retries, no cancellation initiation and no lease: those are later slices.

Specification: ``Ginkgo-Core/ai-stack#32``, ``#42`` and ``#44``;
``architecture/runtime/executor-contract.md`` and ``reference-slice.md``.
"""

from __future__ import annotations

from .contract import (
    DEFAULT_TIMEOUT_MS,
    IN_PROCESS_EXECUTOR_REF,
    NON_TERMINAL_STATES,
    RESULT_REFERENCE_FIELDS,
    TERMINAL_STATES,
    TOOLS,
    ExecutionEnvelopeError,
    Executor,
    InProcessExecutor,
    Transition,
    build_execution_request,
    build_execution_result,
)
from .credentials import (
    CREDENTIAL_NAME_TOKENS,
    MAX_SCAN_DEPTH,
    RAW_SECRET_RULES,
    CredentialBoundaryError,
    CredentialFinding,
    assert_no_credentials,
    credential_findings,
    is_credential_name,
    raw_secret_rule,
)
from .echo import TOOL_ID, EchoInputError, echo_transform
from .n8n import (
    ENVELOPE_REFERENCE_FIELDS,
    N8N_EXECUTOR_REF,
    N8N_INTERNAL_KEYS,
    N8N_STATUS_TO_STATE,
    N8N_TRANSPORT_FAILURES,
    N8nBinding,
    N8nExecutor,
    N8nObservation,
    N8nTransport,
    N8nTransportError,
    RecordedTransport,
    project_output,
)
from .n8n_transport import (
    DEFAULT_CONNECT_TIMEOUT_MS,
    DEFAULT_REQUEST_TIMEOUT_MS,
    DISPATCH_EVIDENCE_VERSION,
    MAX_RESPONSE_BYTES,
    RESULT_ENVELOPE_KEY,
    AuthorizationHeader,
    CredentialResolutionError,
    CredentialResolver,
    EnvironmentCredentialResolver,
    HttpClient,
    HttpConnectionRefused,
    HttpRequest,
    HttpResponse,
    HttpTimeout,
    HttpTransportFailure,
    N8nDispatchRecord,
    N8nEndpoint,
    N8nHttpTransport,
    WorkflowPin,
    dispatch_evidence,
    endpoint_fingerprint,
)

__all__ = [
    "CREDENTIAL_NAME_TOKENS",
    "DEFAULT_CONNECT_TIMEOUT_MS",
    "DEFAULT_REQUEST_TIMEOUT_MS",
    "DEFAULT_TIMEOUT_MS",
    "DISPATCH_EVIDENCE_VERSION",
    "ENVELOPE_REFERENCE_FIELDS",
    "IN_PROCESS_EXECUTOR_REF",
    "MAX_RESPONSE_BYTES",
    "MAX_SCAN_DEPTH",
    "N8N_EXECUTOR_REF",
    "N8N_INTERNAL_KEYS",
    "N8N_STATUS_TO_STATE",
    "N8N_TRANSPORT_FAILURES",
    "NON_TERMINAL_STATES",
    "RAW_SECRET_RULES",
    "RESULT_ENVELOPE_KEY",
    "RESULT_REFERENCE_FIELDS",
    "TERMINAL_STATES",
    "TOOLS",
    "TOOL_ID",
    "AuthorizationHeader",
    "CredentialBoundaryError",
    "CredentialFinding",
    "CredentialResolutionError",
    "CredentialResolver",
    "EchoInputError",
    "EnvironmentCredentialResolver",
    "ExecutionEnvelopeError",
    "Executor",
    "HttpClient",
    "HttpConnectionRefused",
    "HttpRequest",
    "HttpResponse",
    "HttpTimeout",
    "HttpTransportFailure",
    "InProcessExecutor",
    "N8nBinding",
    "N8nDispatchRecord",
    "N8nEndpoint",
    "N8nExecutor",
    "N8nHttpTransport",
    "N8nObservation",
    "N8nTransport",
    "N8nTransportError",
    "RecordedTransport",
    "Transition",
    "WorkflowPin",
    "assert_no_credentials",
    "build_execution_request",
    "build_execution_result",
    "credential_findings",
    "dispatch_evidence",
    "echo_transform",
    "endpoint_fingerprint",
    "is_credential_name",
    "project_output",
    "raw_secret_rule",
]
