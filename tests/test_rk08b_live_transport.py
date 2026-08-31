"""RK-08B/B — the governed live n8n transport, driven by a fake client.

Every test here runs the **real** transport: the real URL construction, the real
header block, the real credential resolution, the real workflow identity check,
the real structural validation and the real failure normalisation. What is faked
is exactly one thing — the socket — and it is faked by satisfying the
``HttpClient`` port with a class that returns a response object.

That is the whole argument for the port. If the tests had to reach a real n8n,
none of the eleven failure classes below would be reachable at all: there is no
way to ask a running engine for a truncated body, a 401, a workflow answering
under the wrong identity, and a connection that is refused, in one suite, in a
second, deterministically. Here every one of them is a two-line fixture.

Nothing in this file opens a socket, names a host that exists, or reads a
credential. ``test_the_live_transport_opens_no_socket`` asserts the first by
execution, and ``tests/test_boundaries.py`` asserts structurally that the socket
lives in exactly one module and that importing the package does not load it.
"""

from __future__ import annotations

import ast
import json
import pathlib
import re
from typing import ClassVar

import pytest

from ginkgo_runtime.clock import SteppingClock
from ginkgo_runtime.executor import (
    N8N_TRANSPORT_FAILURES,
    AuthorizationHeader,
    CredentialResolutionError,
    EnvironmentCredentialResolver,
    HttpConnectionRefused,
    HttpRequest,
    HttpResponse,
    HttpTimeout,
    HttpTransportFailure,
    N8nBinding,
    N8nEndpoint,
    N8nExecutor,
    N8nHttpTransport,
    N8nTransportError,
    WorkflowPin,
    dispatch_evidence,
)

from .test_rk08b_n8n_executor import envelope

BASE_URL = "http://127.0.0.1:5678"
WORKFLOW_REF = "wf-ginkgo-rk08b-ping"
WORKFLOW_PATH = "webhook/ginkgo-rk08b-ping"
WORKFLOW_IDENTITY = "ginkgo.rk08b.ping@1"
TOOL_ID = "rk-n8n-ping"
CORRELATION_ID = "corr-0001"
IDEMPOTENCY_KEY = "idem-0001"

#: The credential reference and the material it resolves to. The material is
#: shaped like a real API key so the credential boundary's value rules would fire
#: on it — an assertion that a fake secret never leaks proves nothing if the fake
#: is a secret no rule recognises.
CREDENTIAL_REF = "cred-ref-n8n-local"
CREDENTIAL_VALUE = "AKIAIOSFODNN7EXAMPLE"
CREDENTIAL_ENV = "GINKGO_N8N_CREDENTIAL_CRED_REF_N8N_LOCAL"

PIN = WorkflowPin(
    workflow_ref=WORKFLOW_REF,
    path=WORKFLOW_PATH,
    declared_identity=WORKFLOW_IDENTITY,
    artifact_sha256="b" * 64,
)

BINDING = N8nBinding(
    binding_id="n8n:ginkgo-rk08b-ping@1",
    workflow_ref=WORKFLOW_REF,
    output_path=("ginkgo", "output"),
)


def pong_body(identity=WORKFLOW_IDENTITY, status="success", correlation=None, output=None):
    """What the pinned workflow returns, engine wrapper and all."""
    envelope_ = {
        "workflow": identity,
        "status": status,
        "output": {"value": "PONG"} if output is None else output,
    }
    if correlation is not None:
        envelope_["correlation_id"] = correlation
    return {"ginkgo": envelope_}


class FakeHttpClient:
    """An ``HttpClient`` that returns what it was told to, and records the request.

    It records the request *whole* — headers included — because that is the only
    surface a resolved credential is ever written onto, and a test asserting that
    a credential is not journalled has to be able to see the one place it legally
    appears in order to prove it appears nowhere else.
    """

    def __init__(self, status=200, body=None, raises=None, headers=None, raw=None):
        self.status = status
        self.raw = raw if raw is not None else json.dumps(
            body if body is not None else pong_body()
        ).encode("utf-8")
        self.raises = raises
        self.headers = headers or {}
        self.requests: list[HttpRequest] = []

    def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        if self.raises is not None:
            raise self.raises
        return HttpResponse(status=self.status, headers=dict(self.headers), body=self.raw)


def transport(client=None, *, pins=None, resolver=None, endpoint=None):
    return N8nHttpTransport(
        endpoint=endpoint or N8nEndpoint(base_url=BASE_URL),
        client=client or FakeHttpClient(),
        pins={WORKFLOW_REF: PIN} if pins is None else pins,
        resolver=resolver,
    )


def dispatch(transport_, payload=None, credential_refs=()):
    return transport_.start(
        workflow_ref=WORKFLOW_REF,
        payload=payload if payload is not None else {"value": "PING"},
        correlation_id=CORRELATION_ID,
        idempotency_key=IDEMPOTENCY_KEY,
        credential_refs=credential_refs,
    )


def bridge(contracts, client=None, *, binding=None, resolver=None, pins=None):
    """The whole chain: Executor Contract over the live transport over a fake socket."""
    live = transport(client, pins=pins, resolver=resolver)
    executor = N8nExecutor(
        contracts=contracts,
        clock=SteppingClock(),
        transport=live,
        bindings={TOOL_ID: binding or BINDING},
    )
    return executor, live


def run(contracts, client=None, *, payload=None, **kwargs):
    executor, live = bridge(contracts, client, **kwargs)
    result, transitions = executor.execute(
        envelope(), tool_id=TOOL_ID, payload=payload or {"value": "PING"}
    )
    return result, transitions, live


# ===========================================================================
# The slice, end to end: PING -> PONG
# ===========================================================================


def test_ping_reaches_pong_through_the_whole_chain(contracts):
    """The reference smoke semantics, with the socket faked and nothing else."""
    result, transitions, live = run(contracts)
    assert result["state"] == "succeeded"
    assert result["output"] == {"value": "PONG"}
    assert result["error"] is None
    assert [t.state for t in transitions] == ["queued", "running", "succeeded"]
    assert len(live.client.requests) == 1


def test_the_request_is_built_from_the_pin_and_not_from_the_caller(contracts):
    live = transport()
    dispatch(live)
    request = live.client.requests[0]
    assert request.method == "POST"
    assert request.url == "%s/%s" % (BASE_URL, WORKFLOW_PATH)
    assert request.connect_timeout_ms > 0
    assert request.request_timeout_ms > 0
    # Two deadlines, not one. `test_the_two_deadlines_are_separately_configurable`
    # proves they are actually independent rather than incidentally equal.
    assert request.connect_timeout_ms != request.request_timeout_ms


def test_the_two_deadlines_are_separately_configurable():
    endpoint = N8nEndpoint(
        base_url=BASE_URL, connect_timeout_ms=250, request_timeout_ms=30000
    )
    live = transport(endpoint=endpoint)
    dispatch(live)
    request = live.client.requests[0]
    assert request.connect_timeout_ms == 250
    assert request.request_timeout_ms == 30000


def test_a_non_positive_timeout_is_refused_at_configuration():
    with pytest.raises(ValueError):
        N8nEndpoint(base_url=BASE_URL, connect_timeout_ms=0)
    with pytest.raises(ValueError):
        N8nEndpoint(base_url=BASE_URL, request_timeout_ms=-1)


# ===========================================================================
# Correlation and idempotency
# ===========================================================================


def test_correlation_and_idempotency_cross_the_boundary(contracts):
    live = transport()
    dispatch(live)
    request = live.client.requests[0]
    assert request.headers["X-Ginkgo-Correlation-Id"] == CORRELATION_ID
    assert request.headers["X-Ginkgo-Idempotency-Key"] == IDEMPOTENCY_KEY
    body = json.loads(request.body.decode("utf-8"))
    assert body["correlation_id"] == CORRELATION_ID
    assert body["idempotency_key"] == IDEMPOTENCY_KEY
    assert body["input"] == {"value": "PING"}


def test_the_correlation_is_carried_onto_the_normalized_result(contracts):
    result, _, _ = run(contracts)
    assert result["correlation_id"] == CORRELATION_ID
    assert result["idempotency_key"] == IDEMPOTENCY_KEY


def test_a_result_for_a_different_correlation_is_refused(contracts):
    """A workflow answering for someone else's request would corrupt evidence."""
    client = FakeHttpClient(body=pong_body(correlation="corr-somebody-else"))
    result, _, live = run(contracts, client)
    assert result["state"] == "failed"
    assert result["error"]["code"] == "N8N_CORRELATION_MISMATCH"
    assert result["error"]["category"] == "INTERNAL"
    assert "corr-somebody-else" not in json.dumps(result)


def test_a_matching_echoed_correlation_passes(contracts):
    client = FakeHttpClient(body=pong_body(correlation=CORRELATION_ID))
    result, _, _ = run(contracts, client)
    assert result["state"] == "succeeded"


# ===========================================================================
# Workflow identity pinning
# ===========================================================================


def test_a_workflow_answering_under_another_identity_fails_closed(contracts):
    client = FakeHttpClient(body=pong_body(identity="callima.whatsapp.inbound@7"))
    result, _, live = run(contracts, client)
    assert result["state"] == "failed"
    assert result["error"]["code"] == "N8N_WORKFLOW_MISMATCH"
    assert result["error"]["category"] == "INTERNAL"
    # The identity that answered is NOT quoted back. It is upstream text.
    assert "callima" not in json.dumps(result)
    assert live.journal[-1].identity_verified is False


def test_the_pinned_identity_is_never_sent_so_the_check_is_a_real_check():
    """A check against a value the transport supplied moments earlier is not a check."""
    live = transport()
    dispatch(live)
    request = live.client.requests[0]
    everything_sent = json.dumps(dict(request.headers)) + request.body.decode("utf-8")
    assert WORKFLOW_IDENTITY not in everything_sent


def test_an_unpinned_workflow_reference_is_never_dispatched():
    live = transport(pins={})
    with pytest.raises(N8nTransportError) as refused:
        dispatch(live)
    assert refused.value.failure == "workflow_not_found"
    assert live.client.requests == []
    assert live.journal[-1].attempted is False


def test_a_pin_without_an_identity_is_refused_at_configuration():
    with pytest.raises(ValueError):
        WorkflowPin(workflow_ref="w", path="p", declared_identity="")


def test_an_artifact_digest_that_is_not_a_sha256_is_refused():
    with pytest.raises(ValueError):
        WorkflowPin(
            workflow_ref="w", path="p", declared_identity="i", artifact_sha256="short"
        )


# ===========================================================================
# Error normalisation — every class RK-08B/B is required to normalise
# ===========================================================================


@pytest.mark.parametrize(
    ("client", "code", "state"),
    [
        (
            FakeHttpClient(raises=HttpConnectionRefused()),
            "N8N_CONNECTION_REFUSED",
            "failed",
        ),
        (FakeHttpClient(raises=HttpTimeout()), "N8N_TRANSPORT_TIMEOUT", "timed_out"),
        (
            FakeHttpClient(raises=HttpTransportFailure()),
            "N8N_TRANSPORT_UNAVAILABLE",
            "failed",
        ),
        (FakeHttpClient(status=401, raw=b"{}"), "N8N_UNAUTHORIZED", "failed"),
        (FakeHttpClient(status=403, raw=b"{}"), "N8N_UNAUTHORIZED", "failed"),
        (FakeHttpClient(status=404, raw=b"{}"), "N8N_WORKFLOW_NOT_FOUND", "failed"),
        (FakeHttpClient(status=429, raw=b"{}"), "N8N_RATE_LIMITED", "failed"),
        (FakeHttpClient(status=400, raw=b"{}"), "N8N_HTTP_CLIENT_ERROR", "failed"),
        (FakeHttpClient(status=500, raw=b"{}"), "N8N_HTTP_SERVER_ERROR", "failed"),
        (FakeHttpClient(status=502, raw=b"{}"), "N8N_HTTP_SERVER_ERROR", "failed"),
        (FakeHttpClient(raw=b"not json at all"), "N8N_MALFORMED_RESPONSE", "failed"),
        (FakeHttpClient(raw=b"\xff\xfe not utf8"), "N8N_MALFORMED_RESPONSE", "failed"),
        (FakeHttpClient(raw=b"[]"), "N8N_INVALID_RESPONSE", "failed"),
        (FakeHttpClient(body={"ginkgo": "not an object"}), "N8N_INVALID_RESPONSE", "failed"),
        (FakeHttpClient(body={"data": {}}), "N8N_INVALID_RESPONSE", "failed"),
        (
            FakeHttpClient(body={"ginkgo": {"workflow": WORKFLOW_IDENTITY}}),
            "N8N_INVALID_RESPONSE",
            "failed",
        ),
        (
            FakeHttpClient(body=pong_body(identity="other@1")),
            "N8N_WORKFLOW_MISMATCH",
            "failed",
        ),
        (
            FakeHttpClient(body=pong_body(correlation="corr-other")),
            "N8N_CORRELATION_MISMATCH",
            "failed",
        ),
    ],
)
def test_every_failure_class_normalises_to_its_declared_code(contracts, client, code, state):
    result, _, _ = run(contracts, client)
    assert result["state"] == state
    assert result["error"]["code"] == code
    assert result["terminal"] is True


def test_an_upstream_execution_failure_is_the_workflows_failure_not_the_transports(contracts):
    """`status: "error"` from a workflow that ran is a PROVIDER failure of the skill.

    Distinct from every class above, all of which mean the bridge never learned
    an outcome. Conflating the two would tell an operator to check the network
    when the workflow is what broke.
    """
    client = FakeHttpClient(body=pong_body(status="error"))
    result, _, _ = run(contracts, client)
    assert result["state"] == "failed"
    assert result["error"]["code"] == "N8N_WORKFLOW_FAILED"
    assert result["error"]["category"] == "PROVIDER"


def test_a_timeout_is_the_only_class_that_reaches_the_timed_out_state(contracts):
    """The frozen contract couples `timed_out` to `category: TIMEOUT`."""
    timed_out = [
        name
        for name, declared in N8N_TRANSPORT_FAILURES.items()
        if declared["state"] == "timed_out"
    ]
    assert timed_out == ["timeout"]
    assert N8N_TRANSPORT_FAILURES["timeout"]["category"] == "TIMEOUT"


def test_every_declared_failure_carries_a_contract_legal_error(contracts):
    """Every row of the table, validated against the frozen schemas, not eyeballed."""
    schema = contracts.error.schema
    categories = set(schema["properties"]["category"]["enum"])
    limit = schema["properties"]["message"]["maxLength"]
    pattern = schema["properties"]["code"]["pattern"]
    for name, declared in N8N_TRANSPORT_FAILURES.items():
        assert declared["state"] in ("failed", "timed_out"), name
        assert declared["category"] in categories, name
        assert isinstance(declared["retryable"], bool), name
        assert re.fullmatch(pattern, str(declared["code"])), name
        assert 0 < len(declared["message"]) <= limit, name


def test_an_unknown_failure_class_fails_closed_to_unavailable():
    """Fail closed: an unmapped class is never reported as something more specific."""
    assert N8nTransportError("no such class").failure == "unavailable"
    assert N8nTransportError().failure == "unavailable"


def test_a_transport_error_cannot_be_handed_upstream_text():
    """There is no message parameter, so a raise site has nowhere to put one."""
    import inspect

    parameters = list(inspect.signature(N8nTransportError.__init__).parameters)
    assert parameters == ["self", "failure"]


def test_a_body_over_the_cap_is_refused_rather_than_parsed(contracts):
    from ginkgo_runtime.executor import MAX_RESPONSE_BYTES

    client = FakeHttpClient(raw=b"x" * (MAX_RESPONSE_BYTES + 1))
    result, _, _ = run(contracts, client)
    assert result["state"] == "failed"
    assert result["error"]["code"] == "N8N_INVALID_RESPONSE"


def test_a_poll_is_never_answered_with_a_fabricated_state():
    """The pinned workflow is synchronous; a poll means a misconfiguration."""
    live = transport()
    with pytest.raises(N8nTransportError) as refused:
        live.poll(workflow_ref=WORKFLOW_REF, handle=None)
    assert refused.value.failure == "invalid_result"


# ===========================================================================
# Endpoint configuration
# ===========================================================================


@pytest.mark.parametrize(
    "url",
    [
        "http://user:hunter2@127.0.0.1:5678",
        "https://admin:AKIAIOSFODNN7EXAMPLE@n8n.internal",
        "http://127.0.0.1:5678?apiKey=AKIAIOSFODNN7EXAMPLE",
        "http://127.0.0.1:5678#token=abc",
        "ftp://127.0.0.1",
        "not-a-url",
        "http://",
    ],
)
def test_an_endpoint_that_could_carry_a_credential_is_refused(url):
    with pytest.raises(ValueError):
        N8nEndpoint(base_url=url)


def test_a_pinned_path_carrying_a_query_string_is_refused():
    endpoint = N8nEndpoint(base_url=BASE_URL)
    with pytest.raises(ValueError):
        endpoint.url_for("webhook/x?token=abc")


def test_the_endpoint_is_recorded_as_a_digest_and_never_as_a_url(contracts):
    result, _, live = run(contracts)
    evidence = dispatch_evidence(live.journal, result)
    serialised = json.dumps(evidence)
    assert BASE_URL not in serialised
    assert "127.0.0.1" not in serialised
    assert N8nEndpoint(base_url=BASE_URL).fingerprint in serialised


# ===========================================================================
# The credential-reference resolution boundary
# ===========================================================================


def test_a_governed_reference_resolves_into_exactly_one_header():
    resolver = EnvironmentCredentialResolver(environ={CREDENTIAL_ENV: CREDENTIAL_VALUE})
    live = transport(resolver=resolver)
    dispatch(live, credential_refs=(CREDENTIAL_REF,))
    request = live.client.requests[0]
    assert request.headers["X-N8N-API-KEY"] == CREDENTIAL_VALUE
    # One header, and the value appears nowhere else in the request.
    assert request.url.count(CREDENTIAL_VALUE) == 0
    assert CREDENTIAL_VALUE not in request.body.decode("utf-8")
    assert [name for name, value in request.headers.items() if value == CREDENTIAL_VALUE] == [
        "X-N8N-API-KEY"
    ]


def test_no_reference_means_no_header_and_that_is_a_declared_configuration():
    live = transport()
    dispatch(live)
    assert "X-N8N-API-KEY" not in live.client.requests[0].headers
    assert live.journal[-1].authenticated is False


def test_a_declared_reference_that_does_not_resolve_fails_closed(contracts):
    """Never a silent downgrade to an unauthenticated dispatch."""
    resolver = EnvironmentCredentialResolver(environ={})
    bound = N8nBinding(
        binding_id=BINDING.binding_id,
        workflow_ref=WORKFLOW_REF,
        output_path=BINDING.output_path,
        credential_refs=(CREDENTIAL_REF,),
    )
    result, _, live = run(contracts, binding=bound, resolver=resolver)
    assert result["state"] == "failed"
    assert result["error"]["code"] == "N8N_CREDENTIAL_UNRESOLVED"
    assert result["error"]["category"] == "INTERNAL"
    assert live.client.requests == []


def test_a_reference_with_no_resolver_configured_fails_closed():
    live = transport(resolver=None)
    with pytest.raises(N8nTransportError) as refused:
        dispatch(live, credential_refs=(CREDENTIAL_REF,))
    assert refused.value.failure == "credential_unresolved"
    assert live.client.requests == []


def test_more_than_one_reference_is_refused_rather_than_silently_narrowed():
    resolver = EnvironmentCredentialResolver(environ={CREDENTIAL_ENV: CREDENTIAL_VALUE})
    live = transport(resolver=resolver)
    with pytest.raises(N8nTransportError) as refused:
        dispatch(live, credential_refs=(CREDENTIAL_REF, "cred-ref-second"))
    assert refused.value.failure == "credential_unresolved"
    assert live.client.requests == []


def test_the_variable_a_reference_reads_from_is_derived_and_not_free():
    """A reference cannot be pointed at an arbitrary variable in the environment."""
    resolver = EnvironmentCredentialResolver()
    assert resolver.variable_for(CREDENTIAL_REF) == CREDENTIAL_ENV
    assert resolver.variable_for("../../PATH").startswith("GINKGO_N8N_CREDENTIAL_")
    assert "/" not in resolver.variable_for("a/b/c")


def test_the_resolver_names_the_reference_and_never_the_material():
    resolver = EnvironmentCredentialResolver(environ={})
    with pytest.raises(CredentialResolutionError) as refused:
        resolver.resolve(CREDENTIAL_REF)
    assert CREDENTIAL_REF in str(refused.value)
    assert CREDENTIAL_ENV not in str(refused.value)
    assert CREDENTIAL_VALUE not in str(refused.value)


def test_an_empty_resolution_is_refused_rather_than_sent_as_an_empty_header():
    with pytest.raises(CredentialResolutionError):
        AuthorizationHeader("X-N8N-API-KEY", "")


# ===========================================================================
# Dispatch evidence
# ===========================================================================


def test_the_evidence_proves_dispatch_workflow_outcome_and_correlation(contracts):
    result, _, live = run(contracts)
    evidence = dispatch_evidence(live.journal, result)

    assert evidence["dispatch_attempted"] is True
    record = evidence["dispatches"][0]
    assert record["attempted"] is True
    assert record["workflow_identity"] == WORKFLOW_IDENTITY
    assert record["workflow_artifact_sha256"] == "b" * 64
    assert record["identity_verified"] is True
    assert record["outcome"] == "observed"
    assert record["http_status"] == 200
    assert record["correlation_id"] == CORRELATION_ID
    assert record["idempotency_key"] == IDEMPOTENCY_KEY

    assert evidence["normalized"]["state"] == "succeeded"
    assert evidence["normalized"]["error_code"] is None
    assert evidence["normalized"]["correlation_id"] == CORRELATION_ID
    assert evidence["normalized"]["executor_ref"] == "rk-n8n-executor"


def test_the_evidence_records_a_refusal_as_an_attempt_that_failed(contracts):
    client = FakeHttpClient(status=401, raw=b"{}")
    result, _, live = run(contracts, client)
    evidence = dispatch_evidence(live.journal, result)
    record = evidence["dispatches"][0]
    assert record["attempted"] is True
    assert record["identity_verified"] is False
    assert record["outcome"] == "unauthorized"
    assert record["http_status"] == 401
    assert evidence["normalized"]["error_code"] == "N8N_UNAUTHORIZED"


def test_the_evidence_distinguishes_never_dispatched_from_dispatched_and_refused():
    live = transport(pins={})
    with pytest.raises(N8nTransportError):
        dispatch(live)
    assert live.journal[-1].attempted is False


def test_a_dispatch_outcome_outside_the_vocabulary_is_refused():
    live = transport()
    with pytest.raises(ValueError):
        live._record(
            attempted=True,
            pin=PIN,
            workflow_ref=WORKFLOW_REF,
            correlation_id=CORRELATION_ID,
            idempotency_key=IDEMPOTENCY_KEY,
            authenticated=False,
            outcome="something-invented",
        )


# ===========================================================================
# Offline by construction
# ===========================================================================


def test_the_live_transport_opens_no_socket(contracts, monkeypatch):
    """Asserted by execution. The whole chain runs with sockets blocked."""
    import socket

    def blocked(*args, **kwargs):
        raise AssertionError("the live transport attempted real network I/O")

    monkeypatch.setattr(socket, "socket", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    result, _, _ = run(contracts)
    assert result["state"] == "succeeded"


def test_the_transport_module_imports_only_the_string_half_of_urllib():
    """``urllib.parse`` parses; ``urllib.request`` opens. Only the first is here.

    Asserted over the AST rather than over a substring, because ``urllib.parse``
    and ``urllib.request`` differ by six characters and a substring scan that got
    it wrong would fail open.
    """
    import ginkgo_runtime.executor.n8n_transport as module

    tree = ast.parse(pathlib.Path(module.__file__).read_text(encoding="utf-8"))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module)

    assert "urllib.request" not in modules
    assert {m for m in modules if m.startswith("urllib")} == {"urllib.parse"}
    assert not {m.split(".")[0] for m in modules} & {
        "socket",
        "ssl",
        "http",
        "requests",
        "httpx",
        "asyncio",
        "subprocess",
        "os",
    }


def test_the_concrete_client_is_the_module_that_has_the_socket():
    """The negative control for the test above: something has to import it."""
    import ginkgo_runtime.executor.n8n_http_client as module

    tree = ast.parse(pathlib.Path(module.__file__).read_text(encoding="utf-8"))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    assert "http.client" in modules
    assert "socket" in modules


# ===========================================================================
# The concrete client, with the connection class faked
# ===========================================================================
#
# `StdlibHttpClient` is the one module that owns a socket, and it is the one
# module whose behaviour cannot be proven by giving it a fake port -- it *is* the
# implementation of the port. So the seam moves down one level: the
# `http.client` connection class is replaced, and everything above it -- the two
# deadlines, the bounded read, the discarded exception text -- is exercised for
# real.
#
# No test in this file opens a socket, including these.


class FakeConnection:
    """Stands in for ``http.client.HTTPConnection``. Opens nothing."""

    instances: ClassVar[list[FakeConnection]] = []

    def __init__(self, host, port=None, timeout=None, **kwargs):
        self.host = host
        self.port = port
        self.connect_timeout = timeout
        self.sock = _FakeSocket()
        self.requested: dict[str, object] = {}
        self.closed = False
        self.raise_on_connect: BaseException | None = None
        self.raise_on_request: BaseException | None = None
        self.status = 200
        self.body = b'{"ok": true}'
        self.headers = [("Content-Type", "application/json")]
        self.response: _FakeResponse | None = None
        FakeConnection.instances.append(self)

    def connect(self):
        if self.raise_on_connect is not None:
            raise self.raise_on_connect

    def request(self, method, path, body=None, headers=None):
        if self.raise_on_request is not None:
            raise self.raise_on_request
        self.requested = {"method": method, "path": path, "body": body, "headers": headers}

    def getresponse(self):
        self.response = _FakeResponse(self.status, self.body, self.headers)
        return self.response

    def close(self):
        self.closed = True


class _FakeSocket:
    def __init__(self):
        self.timeout = None

    def settimeout(self, value):
        self.timeout = value


class _FakeResponse:
    def __init__(self, status, body, headers):
        self.status = status
        self._body = body
        self._headers = headers
        self.read_bound = None

    def read(self, amt=None):
        self.read_bound = amt
        return self._body[:amt] if amt is not None else self._body

    def getheaders(self):
        return list(self._headers)


@pytest.fixture
def fake_connection(monkeypatch):
    import http.client

    FakeConnection.instances = []
    monkeypatch.setattr(http.client, "HTTPConnection", FakeConnection)
    monkeypatch.setattr(http.client, "HTTPSConnection", FakeConnection)
    return FakeConnection


def _client_request(**overrides):
    fields = {
        "method": "POST",
        "url": "http://127.0.0.1:5678/webhook/ginkgo-rk08b-ping",
        "headers": {"Accept": "application/json"},
        "body": b"{}",
        "connect_timeout_ms": 1500,
        "request_timeout_ms": 9000,
    }
    fields.update(overrides)
    return HttpRequest(**fields)


def test_the_client_arms_the_connect_deadline_then_the_request_deadline(fake_connection):
    from ginkgo_runtime.executor.n8n_http_client import StdlibHttpClient

    response = StdlibHttpClient().send(_client_request())
    connection = fake_connection.instances[0]
    assert connection.connect_timeout == 1.5
    assert connection.sock.timeout == 9.0
    assert connection.closed is True
    assert response.status == 200


def test_the_client_bounds_the_read(fake_connection):
    """``read()`` with no bound would let a misbehaving engine exhaust memory."""
    from ginkgo_runtime.executor import MAX_RESPONSE_BYTES
    from ginkgo_runtime.executor.n8n_http_client import StdlibHttpClient

    StdlibHttpClient().send(_client_request())
    read_bound = fake_connection.instances[0].response.read_bound
    assert read_bound is not None
    # One byte over the cap, so the transport above can tell "exactly at the cap"
    # from "truncated" and refuse the second.
    assert read_bound == MAX_RESPONSE_BYTES + 1


@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        (ConnectionRefusedError("refused by 127.0.0.1:5678"), HttpConnectionRefused),
        (TimeoutError("timed out"), HttpTimeout),
        (OSError("network unreachable"), HttpTransportFailure),
    ],
)
def test_a_connect_failure_maps_to_a_declared_class_and_drops_its_text(
    fake_connection, monkeypatch, raised, expected
):
    import http.client

    from ginkgo_runtime.executor.n8n_http_client import StdlibHttpClient

    def connecting(*args, **kwargs):
        connection = FakeConnection(*args, **kwargs)
        connection.raise_on_connect = raised
        return connection

    monkeypatch.setattr(http.client, "HTTPConnection", connecting)
    with pytest.raises(expected) as refused:
        StdlibHttpClient().send(_client_request())
    assert str(raised) not in str(refused.value)
    assert refused.value.__cause__ is None
    assert refused.value.__context__ is None
    assert fake_connection.instances[0].closed is True


def test_a_request_failure_maps_the_same_way_and_still_closes_the_connection(
    fake_connection, monkeypatch
):
    import http.client

    from ginkgo_runtime.executor.n8n_http_client import StdlibHttpClient

    def connecting(*args, **kwargs):
        connection = FakeConnection(*args, **kwargs)
        connection.raise_on_request = http.client.HTTPException(
            "remote said: token=AKIAIOSFODNN7EXAMPLE"
        )
        return connection

    monkeypatch.setattr(http.client, "HTTPConnection", connecting)
    with pytest.raises(HttpTransportFailure) as refused:
        StdlibHttpClient().send(_client_request())
    assert "AKIAIOSFODNN7EXAMPLE" not in str(refused.value)
    assert refused.value.__context__ is None
    assert fake_connection.instances[0].closed is True


def test_the_client_refuses_a_scheme_it_does_not_speak(fake_connection):
    from ginkgo_runtime.executor.n8n_http_client import StdlibHttpClient

    with pytest.raises(HttpTransportFailure):
        StdlibHttpClient().send(_client_request(url="ftp://127.0.0.1/x"))
    assert fake_connection.instances == []
