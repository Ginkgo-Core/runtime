"""RK-08B-B -- the governed live n8n transport, minus the socket.

.. code-block:: text

    Runtime -> Executor Contract -> N8nExecutor -> N8nHttpTransport
            -> [HttpClient port] -> local n8n -> pinned workflow
            -> RuntimeExecutionResult v1 -> Evidence

Everything in that chain except the two bracketed words lives here. The socket
itself lives in :mod:`ginkgo_runtime.executor.n8n_http_client`, which is the only
module in this package allowed to import a network library, and which this module
does **not** import: it is handed an :class:`HttpClient`.

That split is the point of the module. The interesting parts of a live transport
-- how a URL is built, which headers carry correlation, what makes a response
structurally valid, how a workflow proves it is the pinned one, which failure
class an outcome belongs to, and what may be written down about any of it -- are
all decidable without a network, and so they are all tested without one. What is
left behind the port is byte transfer and the operating system's error table.

The five rules this module exists to hold
-----------------------------------------

**A credential is resolved here and nowhere earlier.** ``credential_refs`` are
identifiers all the way from the binding to this boundary; the value is fetched
at the moment of dispatch by an injected :class:`CredentialResolver`, lives in an
:class:`AuthorizationHeader` that cannot be printed, and is written into exactly
one place -- the outgoing request's header block, which is never returned, never
journalled and never logged. There is no attribute anywhere on this module's
objects from which a resolved value can be read back by accident, because
``AuthorizationHeader`` exposes only ``apply``.

**The endpoint cannot carry a credential.** :class:`N8nEndpoint` refuses a URL
with a userinfo component and refuses a query string outright at construction --
not because a query string is always a secret, but because it is the one part of
a URL that is routinely used as one, and a boundary that allowed it would have to
inspect it. Refusing the shape is cheaper and does not have to be right about the
contents.

**The workflow proves its own identity.** The pinned identity is *not* sent to
the engine and then compared to itself; it is baked into the workflow document
and the transport checks that what came back matches what the pin says. A check
against a value the caller supplied moments earlier is not a check.

**Nothing upstream is ever quoted.** Not a body, not a header, not a status
message, not an exception's text. What crosses back into
:mod:`ginkgo_runtime.executor.n8n` is a member of a closed failure vocabulary --
:data:`~ginkgo_runtime.executor.n8n.N8N_TRANSPORT_FAILURES` -- or a projected
observation. Both are enumerable and both are written in this repository.

**Every dispatch is recorded, and the record is safe.** A
:class:`N8nDispatchRecord` proves the dispatch was attempted, against which
workflow identity, under which correlation, with what outcome -- and carries no
URL, no header, no body and no credential. It is scanned by the same credential
boundary as everything else before it becomes evidence.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlsplit

from .credentials import assert_no_credentials
from .n8n import N8N_TRANSPORT_FAILURES, N8nObservation, N8nTransportError

__all__ = [
    "DEFAULT_CONNECT_TIMEOUT_MS",
    "DEFAULT_REQUEST_TIMEOUT_MS",
    "DISPATCH_EVIDENCE_VERSION",
    "MAX_RESPONSE_BYTES",
    "RESULT_ENVELOPE_KEY",
    "AuthorizationHeader",
    "CredentialResolutionError",
    "CredentialResolver",
    "EnvironmentCredentialResolver",
    "HttpClient",
    "HttpConnectionRefused",
    "HttpRequest",
    "HttpResponse",
    "HttpTimeout",
    "HttpTransportFailure",
    "N8nDispatchRecord",
    "N8nEndpoint",
    "N8nHttpTransport",
    "WorkflowPin",
    "dispatch_evidence",
    "endpoint_fingerprint",
]

#: Connect deadline. Small on purpose: a local n8n either accepts a TCP
#: connection immediately or is not running, and a long connect timeout turns
#: "not running" into "hangs for a while and then says not running".
DEFAULT_CONNECT_TIMEOUT_MS = 2000

#: Request deadline, measured from the moment the connection is established. This
#: is the wall-clock bound the offline half could not enforce, and it is
#: deliberately separate from ``bounds.timeout_ms`` on the envelope: the envelope
#: bounds the *execution*, this bounds the *socket*.
DEFAULT_REQUEST_TIMEOUT_MS = 10000

#: The most a response body may be. A transport that read an unbounded body from
#: an engine that is misbehaving would be a memory exhaustion away from taking
#: the kernel with it, and there is no legitimate ``PING`` answer of any size.
MAX_RESPONSE_BYTES = 262144

#: The single key the kernel's own result envelope sits under in a workflow's
#: response. Everything outside it belongs to n8n and is never read.
RESULT_ENVELOPE_KEY = "ginkgo"

DISPATCH_EVIDENCE_VERSION = "ginkgo.runtime.n8n-dispatch-evidence.v1"

#: HTTP status classes that are not a 2xx, mapped to a failure class. Explicit,
#: like every other vocabulary in this bridge; anything unlisted falls through to
#: the 4xx/5xx split below and, failing that, to ``invalid_result``.
_STATUS_FAILURES: Mapping[int, str] = {
    401: "unauthorized",
    403: "unauthorized",
    404: "workflow_not_found",
    429: "rate_limited",
}


def endpoint_fingerprint(base_url: str) -> str:
    """A stable, non-reversing identity for an endpoint.

    Evidence has to be able to say *which* endpoint was dispatched to, and
    ``error.schema.json`` says a raw endpoint must never appear in a record. A
    digest satisfies both: two runs against the same host produce the same value,
    and the value names no host.
    """
    return hashlib.sha256(base_url.encode("utf-8")).hexdigest()


# ===========================================================================
# The credential boundary at the point of dispatch
# ===========================================================================


class CredentialResolutionError(RuntimeError):
    """A bound credential reference did not resolve.

    Names the *reference* -- which is an identifier the binding already holds in
    plain sight -- and never the value, the variable it was looked for in, or
    anything read out of the environment.
    """


class AuthorizationHeader:
    """One resolved authorization header. Applied, never read back.

    Not a dataclass, and not a ``NamedTuple``: both generate a ``__repr__`` that
    prints every field, and a resolved credential that can be printed will
    eventually be printed -- into a traceback, a debug log, a test failure or a
    journal. This class exposes exactly one operation, ``apply``, and its
    ``__repr__`` and ``__str__`` both name the header and redact the value.

    The value is also not reachable as an attribute: ``__slots__`` names it
    ``_value``, ``__getstate__`` refuses, and there is no accessor. That does not
    make it impossible to obtain -- nothing in Python does -- but it does mean
    obtaining it requires writing code that is obviously reaching for a secret.
    """

    __slots__ = ("_name", "_value")

    def __init__(self, name: str, value: str) -> None:
        if not name or not isinstance(name, str):
            raise CredentialResolutionError("an authorization header needs a name")
        if not value or not isinstance(value, str):
            raise CredentialResolutionError(
                "the credential reference resolved to no material"
            )
        self._name = name
        self._value = value

    @property
    def name(self) -> str:
        """The header's NAME. Not material, and needed to build the request."""
        return self._name

    def apply(self, headers: dict[str, str]) -> dict[str, str]:
        """Write the header into ``headers`` and return it.

        The one place a resolved value is ever written. The dict it is written
        into belongs to a single outgoing :class:`HttpRequest`, which is handed
        straight to the client and never journalled, returned or serialised.
        """
        headers[self._name] = self._value
        return headers

    def __repr__(self) -> str:
        return "<AuthorizationHeader %s=<redacted>>" % self._name

    __str__ = __repr__

    def __getstate__(self) -> Any:
        raise CredentialResolutionError(
            "a resolved authorization header is not serialisable"
        )


class CredentialResolver(Protocol):
    """Turns a governed credential *reference* into material, at the boundary.

    A port, so the resolution strategy is a deployment concern and the transport
    holds no opinion about where a secret lives. Every test in this repository
    injects one that resolves from a dict it constructed, so no test needs an
    environment variable and none reads a real credential.
    """

    def resolve(self, reference: str) -> AuthorizationHeader:
        """Return the header for ``reference``, or raise :class:`CredentialResolutionError`."""
        ...


@dataclass(frozen=True)
class EnvironmentCredentialResolver:
    """Resolves a reference from the process environment. Fails closed.

    ``os.environ`` is read through an injected mapping rather than imported: the
    boundaries test forbids ``os`` inside the bridge's own modules, and a resolver
    that took its source as an argument is testable without setting a variable in
    the test process anyway. The host passes ``os.environ`` at the edge -- the
    smoke runner is the only caller that does.

    The variable name is derived from the reference and is never inferred from
    anything a caller supplied, so a reference cannot be pointed at an arbitrary
    variable in the kernel's environment by whoever wrote the request.
    """

    #: The header the resolved material is sent under. n8n's own header auth
    #: default; overridable, because an instance behind a proxy may want another.
    header_name: str = "X-N8N-API-KEY"
    prefix: str = "GINKGO_N8N_CREDENTIAL_"
    #: ``repr=False``, and that is load-bearing rather than tidy. A dataclass
    #: prints every field, this field is an environment, and an environment holds
    #: every secret the process was given -- so the generated ``__repr__`` would
    #: publish the whole of it into any traceback that happened to include a
    #: transport, which holds a resolver, which holds this.
    #: ``test_a_resolved_credential_is_written_into_one_header_and_nowhere_else``
    #: found exactly that and is what this line answers.
    environ: Mapping[str, str] = field(default_factory=dict, repr=False)

    def variable_for(self, reference: str) -> str:
        """The environment variable a reference is read from. Derived, not free."""
        folded = "".join(
            character if character.isalnum() else "_" for character in reference
        )
        return "%s%s" % (self.prefix, folded.upper())

    def resolve(self, reference: str) -> AuthorizationHeader:
        value = self.environ.get(self.variable_for(reference))
        if not value:
            # The reference is named; the variable is not, and neither is
            # anything that was in it. An absent credential is a hard failure
            # rather than an unauthenticated dispatch -- a transport that quietly
            # dropped the header would turn a misconfiguration into a request
            # that succeeds against an unauthenticated instance and fails against
            # a real one.
            raise CredentialResolutionError(
                "the credential reference %r did not resolve at the transport "
                "boundary" % reference
            )
        return AuthorizationHeader(self.header_name, value)


# ===========================================================================
# The HTTP port
# ===========================================================================


class HttpTransportFailure(RuntimeError):
    """The client could not complete an exchange.

    Carries no text from the operating system, the socket layer or the remote
    end. The concrete client raises one of these three and discards the original
    exception's message rather than wrapping it, which is why this hierarchy has
    no ``cause`` or ``detail`` field to put one in.
    """


class HttpConnectionRefused(HttpTransportFailure):
    """Nothing was listening, or the connection was actively refused."""


class HttpTimeout(HttpTransportFailure):
    """A connect or request deadline elapsed."""


@dataclass(frozen=True)
class HttpRequest:
    """One outgoing exchange, fully built. Built here, sent behind the port."""

    method: str
    url: str
    headers: Mapping[str, str]
    body: bytes
    connect_timeout_ms: int
    request_timeout_ms: int
    max_response_bytes: int = MAX_RESPONSE_BYTES


@dataclass(frozen=True)
class HttpResponse:
    """One response, as bytes and a status. Headers are carried, never read.

    They are carried so a test can prove the transport ignores them: a response
    header block is a documented leak path -- ``Set-Cookie``, ``Authorization``
    echoes, ``X-Powered-By`` -- and "the transport does not read headers" is only
    a claim worth making if the headers were available for it to read.
    """

    status: int
    headers: Mapping[str, str]
    body: bytes


class HttpClient(Protocol):
    """The only way this package reaches a network.

    One method, no session, no retry, no redirect following, no cookie jar. Each
    of those is a decision, and a transport that made them would be making
    routing and policy decisions inside an executor.
    """

    def send(self, request: HttpRequest) -> HttpResponse:
        """Perform ``request``, or raise a :class:`HttpTransportFailure`."""
        ...


# ===========================================================================
# Endpoint and workflow pinning
# ===========================================================================


@dataclass(frozen=True)
class N8nEndpoint:
    """Where the bridge speaks to, and under what deadlines.

    Explicitly configured and never discovered. A transport that probed for a
    reachable n8n would be selecting an executor, which is the Executor Router's
    job and not this module's.

    Nothing here is a credential and nothing here may become one: the constructor
    refuses a URL carrying userinfo or a query string, so the two places a secret
    is habitually hidden in a URL are structurally unavailable.
    """

    base_url: str
    connect_timeout_ms: int = DEFAULT_CONNECT_TIMEOUT_MS
    request_timeout_ms: int = DEFAULT_REQUEST_TIMEOUT_MS

    def __post_init__(self) -> None:
        parts = urlsplit(self.base_url)
        if parts.scheme not in ("http", "https"):
            raise ValueError("the n8n endpoint must be an http or https URL")
        if not parts.hostname:
            raise ValueError("the n8n endpoint must name a host")
        if parts.username or parts.password or "@" in parts.netloc:
            # `url-embedded-credential` in the credential boundary would catch
            # this too. It is refused here as well, at construction, so a
            # misconfigured endpoint fails when it is configured rather than on
            # the first dispatch.
            raise ValueError(
                "the n8n endpoint must not carry a userinfo component; "
                "credentials are resolved from governed references"
            )
        if parts.query or parts.fragment:
            raise ValueError(
                "the n8n endpoint must not carry a query string or fragment; "
                "a query string is the usual hiding place for a token"
            )
        if self.connect_timeout_ms <= 0 or self.request_timeout_ms <= 0:
            raise ValueError("both endpoint timeouts must be positive")

    @property
    def fingerprint(self) -> str:
        """The digest recorded in evidence in place of the URL."""
        return endpoint_fingerprint(self.base_url)

    def url_for(self, path: str) -> str:
        """Join a pinned path onto the base. No query string, ever."""
        if "?" in path or "#" in path:
            raise ValueError("a pinned workflow path carries no query or fragment")
        return "%s/%s" % (self.base_url.rstrip("/"), path.lstrip("/"))


@dataclass(frozen=True)
class WorkflowPin:
    """The pinned identity of one workflow, and where it answers.

    Three separate things, deliberately:

    ``path``
        where the engine answers. Operational, changes with a deployment.

    ``declared_identity``
        the identity the *workflow document itself* returns. It is baked into the
        committed workflow JSON and is not sent by the transport, so checking it
        is a real check rather than a comparison of a value with itself.

    ``artifact_sha256``
        the digest of the committed workflow document. The transport does not use
        it -- there is no way to ask a running n8n for the bytes it imported --
        but the smoke procedure does, so that "the pinned workflow" names a
        specific file and not a workflow someone edited in the UI afterwards.
    """

    workflow_ref: str
    path: str
    declared_identity: str
    artifact_sha256: str | None = None

    def __post_init__(self) -> None:
        if not self.declared_identity:
            raise ValueError("a workflow pin must declare an identity to verify")
        if self.artifact_sha256 is not None and len(self.artifact_sha256) != 64:
            raise ValueError("a workflow artifact digest must be a sha256 hex digest")


# ===========================================================================
# The dispatch record
# ===========================================================================


@dataclass(frozen=True)
class N8nDispatchRecord:
    """What one dispatch attempt is entitled to say about itself.

    Every field is either an identifier the kernel already holds, a boolean, an
    integer, or a digest. There is no URL, no header block, no request body, no
    response body and no credential -- not redacted, *absent*, which is the only
    version of that claim that survives someone later widening a redaction.

    ``authenticated`` is a boolean and is spelled that way on purpose: the
    credential boundary's field-name rule refuses a key tokenising to ``auth``,
    ``authorization`` or ``credential``, so a record that named this field
    honestly would be refused by the boundary that is meant to protect it. The
    boolean is the whole of what may be recorded either way.
    """

    attempted: bool
    workflow_ref: str
    workflow_identity: str
    endpoint_fingerprint: str
    correlation_id: str
    idempotency_key: str
    authenticated: bool
    outcome: str
    http_status: int | None = None
    identity_verified: bool = False
    workflow_artifact_sha256: str | None = None

    def as_evidence(self) -> dict[str, Any]:
        """The record as a plain document, ready to be scanned and written."""
        return {
            "attempted": self.attempted,
            "authenticated": self.authenticated,
            "correlation_id": self.correlation_id,
            "endpoint_fingerprint": self.endpoint_fingerprint,
            "http_status": self.http_status,
            "idempotency_key": self.idempotency_key,
            "identity_verified": self.identity_verified,
            "outcome": self.outcome,
            "workflow_artifact_sha256": self.workflow_artifact_sha256,
            "workflow_identity": self.workflow_identity,
            "workflow_ref": self.workflow_ref,
        }


def dispatch_evidence(
    records: Sequence[N8nDispatchRecord], result: Mapping[str, Any]
) -> dict[str, Any]:
    """Join what the transport attempted to what the Contract decided.

    Neither half is sufficient alone. The dispatch records prove a dispatch was
    *attempted*, against which workflow identity and under which correlation --
    facts only the transport has. The normalized disposition proves what the
    kernel *concluded*, which only the Executor Contract has. An evidence record
    carrying one and not the other cannot answer "did the runtime reach n8n, and
    what did it decide about what came back".

    The joined document is scanned by the credential boundary before it is
    returned, so a record that somehow acquired material is never written at all
    -- the same rule, and the same failure mode, as
    :func:`~ginkgo_runtime.executor.contract.build_execution_result`.
    """
    error = result.get("error") or {}
    document = {
        "schema_version": DISPATCH_EVIDENCE_VERSION,
        "dispatches": [record.as_evidence() for record in records],
        "dispatch_attempted": any(record.attempted for record in records),
        "normalized": {
            "correlation_id": result.get("correlation_id"),
            "idempotency_key": result.get("idempotency_key"),
            "execution_id": result.get("execution_id"),
            "executor_ref": result.get("executor_ref"),
            "state": result.get("state"),
            "terminal": result.get("terminal"),
            "error_code": error.get("code"),
            "error_category": error.get("category"),
            "retryable": error.get("retryable"),
        },
    }
    assert_no_credentials(document, surface="the n8n dispatch evidence record")
    return document


# ===========================================================================
# The transport
# ===========================================================================


@dataclass
class N8nHttpTransport:
    """The live transport, satisfying :class:`~ginkgo_runtime.executor.n8n.N8nTransport`.

    Satisfying it *without editing it*, which was ADR-RT-003's option B argument
    for a port over a mock: RK-08B-A predicted this class would drop in behind
    the same Protocol, and it does -- :class:`~ginkgo_runtime.executor.n8n.N8nExecutor`
    is unchanged except for the failure table it now reads.

    One request per execution. The pinned workflow answers synchronously through
    a *Respond to Webhook* node, so the first observation is already terminal and
    ``poll`` is never called on the reference path. That is a property of this
    slice, not a claim about n8n in general: an asynchronous workflow would need
    a second endpoint to poll and is deliberately out of RK-08B-B's scope.
    """

    endpoint: N8nEndpoint
    client: HttpClient
    pins: Mapping[str, WorkflowPin]
    resolver: CredentialResolver | None = None
    #: One :class:`N8nDispatchRecord` per attempt, in order. Read by the smoke
    #: runner to build evidence; never read by the executor, which must not learn
    #: anything about the transport beyond the observation and the failure class.
    journal: list[N8nDispatchRecord] = field(default_factory=list)

    # -- the port ----------------------------------------------------------

    def start(
        self,
        *,
        workflow_ref: str,
        payload: Mapping[str, Any],
        correlation_id: str,
        idempotency_key: str,
        credential_refs: Sequence[str],
    ) -> N8nObservation:
        pin = self.pins.get(workflow_ref)
        if pin is None:
            # Not a network failure and not the engine's fault: the binding names
            # a workflow this transport was never configured for.
            self._record(
                attempted=False,
                pin=None,
                workflow_ref=workflow_ref,
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
                authenticated=False,
                outcome="workflow_not_found",
            )
            raise N8nTransportError("workflow_not_found")

        # Defence in depth. `N8nExecutor` already scanned both the envelope and
        # the payload before it reached any transport, and this transport is a
        # public class that can be driven directly -- by the smoke runner, by a
        # future adapter, by a test. A boundary that is only enforced by its
        # caller is enforced by whoever remembers to call it.
        assert_no_credentials(payload, surface="the payload dispatched to n8n")

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            # Correlation and idempotency cross the boundary as headers *and* in
            # the body. The headers are what an operator greps n8n's own logs
            # for; the body is what the workflow can echo, and echoing is what
            # makes the correlation check below possible at all.
            "X-Ginkgo-Correlation-Id": correlation_id,
            "X-Ginkgo-Idempotency-Key": idempotency_key,
        }
        authenticated = self._authorize(
            headers, credential_refs, pin, correlation_id, idempotency_key
        )

        body = json.dumps(
            {
                "correlation_id": correlation_id,
                "idempotency_key": idempotency_key,
                "input": dict(payload),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

        request = HttpRequest(
            method="POST",
            url=self.endpoint.url_for(pin.path),
            headers=headers,
            body=body,
            connect_timeout_ms=self.endpoint.connect_timeout_ms,
            request_timeout_ms=self.endpoint.request_timeout_ms,
        )
        return self._exchange(request, pin, correlation_id, idempotency_key, authenticated)

    def poll(self, *, workflow_ref: str, handle: str | None) -> N8nObservation:
        """Never reached on this slice, and it fails closed rather than pretending.

        The pinned workflow is synchronous, so a poll means the engine returned a
        non-terminal status it has no way to advance from. Returning the previous
        observation, or a fabricated ``running``, would turn that into a poll
        budget exhaustion and report a misconfiguration as a timeout.
        """
        raise N8nTransportError("invalid_result")

    # -- credential resolution --------------------------------------------

    def _authorize(
        self,
        headers: dict[str, str],
        credential_refs: Sequence[str],
        pin: WorkflowPin,
        correlation_id: str,
        idempotency_key: str,
    ) -> bool:
        """Resolve at most one reference into the header block. Fail closed."""
        references = [reference for reference in credential_refs if reference]
        if not references:
            # An unauthenticated local instance is the reference deployment for
            # this slice, and it is a legitimate configuration -- not a silent
            # downgrade, because the binding declaring no reference is what says
            # so. A binding that DOES declare one and cannot resolve it fails.
            return False
        if self.resolver is None or len(references) != 1:
            self._record(
                attempted=False,
                pin=pin,
                workflow_ref=pin.workflow_ref,
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
                authenticated=False,
                outcome="credential_unresolved",
            )
            raise N8nTransportError("credential_unresolved")
        # Same discipline as `_exchange`: the raise is outside the handler, so the
        # resolver's exception is not left hanging off `__context__`. Its message
        # names only the reference, and it is still not propagated -- what leaves
        # here is a failure class.
        resolved: AuthorizationHeader | None = None
        try:
            resolved = self.resolver.resolve(references[0])
        except CredentialResolutionError:
            resolved = None
        if resolved is None:
            self._record(
                attempted=False,
                pin=pin,
                workflow_ref=pin.workflow_ref,
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
                authenticated=False,
                outcome="credential_unresolved",
            )
            raise N8nTransportError("credential_unresolved")
        resolved.apply(headers)
        return True

    # -- the exchange ------------------------------------------------------

    def _exchange(
        self,
        request: HttpRequest,
        pin: WorkflowPin,
        correlation_id: str,
        idempotency_key: str,
        authenticated: bool,
    ) -> N8nObservation:
        def fail(
            outcome: str, status: int | None = None, verified: bool = False
        ) -> N8nTransportError:
            self._record(
                attempted=True,
                pin=pin,
                workflow_ref=pin.workflow_ref,
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
                authenticated=authenticated,
                outcome=outcome,
                http_status=status,
                identity_verified=verified,
            )
            return N8nTransportError(outcome)

        # The failure class is captured and the raise happens OUTSIDE the handler.
        #
        # `raise ... from None` clears `__cause__` and sets `__suppress_context__`,
        # which stops the default traceback printer -- but `__context__` still
        # holds the original exception, and the original exception's text is
        # written by the socket layer and may name a host or a URL with a userinfo
        # component. Anything that walks the chain reflectively (a structured
        # logger, an error reporter, a test) finds it there. Raising outside the
        # handler leaves `__context__` genuinely empty, which is the difference
        # between suppressed and discarded.
        failure: str | None = None
        try:
            response = self.client.send(request)
        except HttpConnectionRefused:
            failure = "connection_refused"
        except HttpTimeout:
            failure = "timeout"
        except HttpTransportFailure:
            failure = "unavailable"
        if failure is not None:
            raise fail(failure)

        if not 200 <= response.status < 300:
            outcome = _STATUS_FAILURES.get(response.status)
            if outcome is None:
                outcome = (
                    "http_client_error" if response.status < 500 else "http_server_error"
                )
            # The body is not read, not decoded and not looked at. A non-2xx body
            # from n8n is the single richest leak path in this module -- it is
            # written by the engine, may quote the request it failed on, and
            # routinely carries a stack trace naming a credential.
            raise fail(outcome, response.status)

        if len(response.body) > MAX_RESPONSE_BYTES:
            raise fail("invalid_result", response.status)

        # `json`'s own error message quotes the document around the failure --
        # `Expecting ',' delimiter: line 1 column 34` is harmless, but the
        # `doc` attribute on `JSONDecodeError` is the whole body. So the
        # exception is caught, dropped, and the raise happens outside the
        # handler, leaving nothing on `__context__` for a reporter to walk.
        document: Any = None
        parsed = False
        try:
            document = json.loads(response.body.decode("utf-8"))
            parsed = True
        except (UnicodeDecodeError, ValueError):
            parsed = False
        if not parsed:
            raise fail("malformed_json", response.status)

        return self._observe(
            document, pin, correlation_id, idempotency_key, authenticated, response.status
        )

    # -- structural validation and identity ------------------------------

    def _observe(
        self,
        document: Any,
        pin: WorkflowPin,
        correlation_id: str,
        idempotency_key: str,
        authenticated: bool,
        status: int,
    ) -> N8nObservation:
        """Turn a well-formed JSON document into an observation, or fail closed.

        Everything outside :data:`RESULT_ENVELOPE_KEY` is n8n's and is carried
        into the observation's ``payload`` unread -- the projection in
        :mod:`ginkgo_runtime.executor.n8n` is what decides which single path of it
        a caller may see, and duplicating that decision here would give the kernel
        two places to get it wrong.
        """

        def fail(outcome: str, verified: bool = False) -> N8nTransportError:
            self._record(
                attempted=True,
                pin=pin,
                workflow_ref=pin.workflow_ref,
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
                authenticated=authenticated,
                outcome=outcome,
                http_status=status,
                identity_verified=verified,
            )
            return N8nTransportError(outcome)

        if not isinstance(document, Mapping):
            raise fail("invalid_result")
        envelope = document.get(RESULT_ENVELOPE_KEY)
        if not isinstance(envelope, Mapping):
            raise fail("invalid_result")

        identity = envelope.get("workflow")
        if not isinstance(identity, str) or not identity:
            raise fail("invalid_result")
        if identity != pin.declared_identity:
            # Fail closed, and note that the record says `identity_verified:
            # False` -- an operator reading the journal after a deployment
            # mix-up learns that the runtime reached *an* engine and refused what
            # it found, which is a different fact from "n8n was unreachable".
            raise fail("workflow_mismatch")

        echoed = envelope.get("correlation_id")
        if echoed is not None and echoed != correlation_id:
            # Only checked when the workflow echoes it. A workflow that returns
            # nothing is not lying about the correlation; one that returns the
            # wrong correlation is answering for a different request, and a
            # transport that accepted that would attribute one execution's output
            # to another execution's evidence.
            raise fail("correlation_mismatch", verified=True)

        reported = envelope.get("status")
        if not isinstance(reported, str) or not reported.strip():
            raise fail("invalid_result", verified=True)

        handle = envelope.get("execution_id")
        self._record(
            attempted=True,
            pin=pin,
            workflow_ref=pin.workflow_ref,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            authenticated=authenticated,
            outcome="observed",
            http_status=status,
            identity_verified=True,
        )
        # The status string is handed on unmapped. Mapping it is
        # `N8N_STATUS_TO_STATE`'s job, in the Contract, where an unknown status
        # already fails closed -- so a transport that pre-mapped it would be
        # adding a second, quieter vocabulary.
        return N8nObservation(
            status=reported,
            payload=document,
            handle=str(handle) if isinstance(handle, str | int) else None,
        )

    # -- the journal -------------------------------------------------------

    def _record(
        self,
        *,
        attempted: bool,
        pin: WorkflowPin | None,
        workflow_ref: str,
        correlation_id: str,
        idempotency_key: str,
        authenticated: bool,
        outcome: str,
        http_status: int | None = None,
        identity_verified: bool = False,
    ) -> None:
        if outcome != "observed" and outcome not in N8N_TRANSPORT_FAILURES:
            # A `raise`, not an `assert`: `python -O` strips asserts, and a
            # journal whose vocabulary is only checked in development is a
            # journal whose vocabulary is not checked.
            raise ValueError(
                "a dispatch outcome must be `observed` or a declared failure class"
            )
        self.journal.append(
            N8nDispatchRecord(
                attempted=attempted,
                workflow_ref=workflow_ref,
                workflow_identity=pin.declared_identity if pin else "<unpinned>",
                workflow_artifact_sha256=pin.artifact_sha256 if pin else None,
                endpoint_fingerprint=self.endpoint.fingerprint,
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
                authenticated=authenticated,
                outcome=outcome,
                http_status=http_status,
                identity_verified=identity_verified,
            )
        )
