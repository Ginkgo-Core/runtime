"""RK-08B/B — the leak paths a live transport opens that an offline one did not.

RK-08B/A closed the credential boundary in both directions over *documents*: the
envelope, the payload, the projected output, the transitions and the assembled
result. A live transport adds six surfaces that no document boundary can see,
because none of them is a document the kernel builds:

1. an **HTTP error body**, written by the engine, quoting whatever it failed on;
2. **response headers**, which routinely carry ``Set-Cookie`` and auth echoes;
3. **exception text**, from ``http.client``, from the OS error table, and from
   ``json`` — which quotes the document around a parse failure;
4. a **URL** with an embedded credential or a token in a query string;
5. the **n8n response payload**, which carries the engine's own credential blocks;
6. **diagnostics and evidence** — the journal, the dispatch record, anything an
   operator reads.

Each has a test here, and each test uses a secret shaped like real credential
material, so the credential boundary's own value rules would fire on it if it
ever appeared. A leak test whose secret is a string no rule recognises proves
nothing.

The corpus is written down rather than derived. A secret generated from the thing
it is compared against would make every assertion below true by construction.
"""

from __future__ import annotations

import json

import pytest

from ginkgo_runtime.executor import (
    CredentialBoundaryError,
    CredentialResolutionError,
    EnvironmentCredentialResolver,
    HttpConnectionRefused,
    HttpTimeout,
    HttpTransportFailure,
    N8nBinding,
    N8nEndpoint,
    N8nTransportError,
    dispatch_evidence,
)

from .test_rk08b_live_transport import (
    BASE_URL,
    BINDING,
    CREDENTIAL_ENV,
    CREDENTIAL_REF,
    CREDENTIAL_VALUE,
    TOOL_ID,
    WORKFLOW_IDENTITY,
    WORKFLOW_REF,
    FakeHttpClient,
    bridge,
    dispatch,
    pong_body,
    run,
    transport,
)
from .test_rk08b_n8n_executor import envelope

#: Every one of these is a published credential shape. The credential boundary's
#: `RAW_SECRET_RULES` recognise all of them, which is what makes "it never
#: appears" a check rather than a coincidence.
SECRETS = (
    "AKIAIOSFODNN7EXAMPLE",
    "AIzaSyA" + "0" * 32,
    "ghp_0123456789abcdefghijklmnopqrstuvwxyz",
    "xoxb-000000000000-000000000000-abcdefghijklmnopqrstuvwx",
    "sk_live_0123456789abcdefghij",
    "sk-proj-0123456789abcdefghijklmnop",
    "-----BEGIN RSA PRIVATE KEY-----",
    "Bearer 0123456789abcdefghijklmnopqrstuvwxyz",
    "password: hunter2hunter2",
)


def everything_written(result, transitions=(), journal=()):
    """Every string this run produced, in one document, for one substring check.

    Assertions in this file are made over *this*, not over a hand-picked field.
    A leak test that checks ``result["error"]["message"]`` proves that one field
    is clean and says nothing about the transition reasons, the dispatch journal
    or the exception a caller catches.
    """
    return json.dumps(
        {
            "result": result,
            "transitions": [[t.state, t.occurred_at, t.reason] for t in transitions],
            "journal": [record.as_evidence() for record in journal],
        },
        default=repr,
    )


# ===========================================================================
# 0. The corpus itself
# ===========================================================================


def test_every_secret_in_the_corpus_is_one_a_rule_actually_recognises():
    """Otherwise every assertion below is a check against an unremarkable string.

    "The secret did not leak" is only evidence if the secret is the kind of thing
    the credential boundary would have caught had it leaked into a document. A
    corpus of arbitrary strings would pass every test in this file while proving
    that arbitrary strings do not appear.
    """
    from ginkgo_runtime.executor import raw_secret_rule

    unrecognised = [secret for secret in SECRETS if raw_secret_rule(secret) is None]
    assert unrecognised == []
    # And they are distinct rules, not nine spellings of one.
    assert len({raw_secret_rule(secret) for secret in SECRETS}) == len(SECRETS)


# ===========================================================================
# 1. HTTP error bodies
# ===========================================================================


@pytest.mark.parametrize("secret", SECRETS)
@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500, 503])
def test_an_http_error_body_never_reaches_anything(contracts, status, secret):
    """The richest leak path in a live bridge, and it is not read at all.

    n8n's error bodies quote the request that failed. A bridge that put one in an
    error message would publish the header block it was sent with.
    """
    body = json.dumps(
        {
            "message": "Workflow failed: request had header X-N8N-API-KEY=%s" % secret,
            "stack": "Error at Webhook (credential=%s)" % secret,
        }
    ).encode("utf-8")
    client = FakeHttpClient(status=status, raw=body)
    result, transitions, live = run(contracts, client)

    assert result["state"] in ("failed", "timed_out")
    written = everything_written(result, transitions, live.journal)
    assert secret not in written
    # Nor any of the engine's own words.
    assert "Workflow failed" not in written
    assert "stack" not in written


def test_a_2xx_body_is_read_and_a_non_2xx_body_is_not(contracts):
    """The negative control. If no body were ever read, the tests above are vacuous."""
    ok = FakeHttpClient(status=200, body=pong_body())
    result, _, _ = run(contracts, ok)
    assert result["output"] == {"value": "PONG"}

    refused = FakeHttpClient(status=500, body=pong_body())
    result, _, _ = run(contracts, refused)
    # Same body, different status: nothing was projected out of it.
    assert "output" not in result
    assert result["error"]["code"] == "N8N_HTTP_SERVER_ERROR"


# ===========================================================================
# 2. Response headers
# ===========================================================================


@pytest.mark.parametrize("secret", SECRETS)
def test_response_headers_are_never_read_recorded_or_reported(contracts, secret):
    """They are carried across the port precisely so that this can be asserted."""
    headers = {
        "Set-Cookie": "n8n-auth=%s; HttpOnly" % secret,
        "Authorization": "Bearer %s" % secret,
        "X-Api-Key": secret,
        "X-Powered-By": "Express",
    }
    client = FakeHttpClient(status=200, body=pong_body(), headers=headers)
    result, transitions, live = run(contracts, client)
    assert result["state"] == "succeeded"

    written = everything_written(result, transitions, live.journal)
    assert secret not in written
    assert "Set-Cookie" not in written
    assert "X-Powered-By" not in written


def test_a_header_carrying_a_credential_does_not_even_reach_a_failure_path(contracts):
    """Same headers, but on a status the transport refuses. Still nothing."""
    client = FakeHttpClient(
        status=500,
        raw=b"{}",
        headers={"WWW-Authenticate": "Basic realm=%s" % SECRETS[0]},
    )
    result, transitions, live = run(contracts, client)
    assert SECRETS[0] not in everything_written(result, transitions, live.journal)


# ===========================================================================
# 3. Exception text
# ===========================================================================


@pytest.mark.parametrize("secret", SECRETS)
@pytest.mark.parametrize(
    "raised",
    [HttpConnectionRefused, HttpTimeout, HttpTransportFailure],
)
def test_an_exception_from_the_client_is_discarded_and_never_wrapped(
    contracts, raised, secret
):
    """The client's exception is not the cause of the transport's, deliberately.

    A ``raise ... from exc`` would attach the original to ``__cause__``, where a
    traceback printer would find it and print an OS error string that may carry a
    URL with a userinfo component.
    """
    client = FakeHttpClient(raises=raised("connecting to https://admin:%s@host" % secret))
    result, transitions, live = run(contracts, client)

    written = everything_written(result, transitions, live.journal)
    assert secret not in written
    assert "admin" not in written


def test_the_transport_error_itself_carries_no_upstream_text(contracts):
    live = transport(FakeHttpClient(raises=HttpTimeout("host=%s" % SECRETS[0])))
    with pytest.raises(N8nTransportError) as refused:
        dispatch(live)
    assert SECRETS[0] not in str(refused.value)
    assert refused.value.__cause__ is None
    assert refused.value.__context__ is None


@pytest.mark.parametrize("secret", SECRETS)
def test_a_json_parse_failure_never_quotes_the_document_it_failed_on(contracts, secret):
    """``json``'s own message quotes the text around the error. It is discarded."""
    client = FakeHttpClient(raw=('{"apiKey": "%s", ' % secret).encode("utf-8"))
    result, transitions, live = run(contracts, client)
    assert result["error"]["code"] == "N8N_MALFORMED_RESPONSE"
    written = everything_written(result, transitions, live.journal)
    assert secret not in written
    assert "apiKey" not in written


# ===========================================================================
# 4. URLs
# ===========================================================================


@pytest.mark.parametrize("secret", SECRETS[:6])
def test_an_endpoint_carrying_a_credential_cannot_be_configured(secret):
    for url in (
        "http://user:%s@127.0.0.1:5678" % secret,
        "http://127.0.0.1:5678?token=%s" % secret,
        "http://127.0.0.1:5678#%s" % secret,
    ):
        with pytest.raises(ValueError) as refused:
            N8nEndpoint(base_url=url)
        assert secret not in str(refused.value)


def test_a_configured_url_is_never_written_into_a_result_or_evidence(contracts):
    result, transitions, live = run(contracts)
    written = everything_written(result, transitions, live.journal)
    assert BASE_URL not in written
    assert "127.0.0.1" not in written
    assert "5678" not in written


def test_the_pinned_path_never_reaches_the_caller(contracts):
    result, transitions, live = run(contracts)
    written = everything_written(result, transitions, live.journal)
    assert "webhook/" not in written


# ===========================================================================
# 5. The n8n response payload
# ===========================================================================


@pytest.mark.parametrize("secret", SECRETS)
def test_the_engines_own_credential_blocks_never_survive_the_projection(contracts, secret):
    """A real execution document carries them; only one declared path is read."""
    document = pong_body()
    document["workflowData"] = {
        "nodes": [{"name": "Webhook", "credentials": {"httpHeaderAuth": secret}}]
    }
    document["data"] = {"resultData": {"runData": {"Set": [{"json": {"token": secret}}]}}}
    client = FakeHttpClient(body=document)
    result, transitions, live = run(contracts, client)

    assert result["state"] == "succeeded"
    assert result["output"] == {"value": "PONG"}
    written = everything_written(result, transitions, live.journal)
    for leaked in (secret, "workflowData", "runData", "httpHeaderAuth"):
        assert leaked not in written


@pytest.mark.parametrize("secret", SECRETS)
def test_a_credential_inside_the_declared_output_path_emits_no_result_at_all(
    contracts, secret
):
    """The RK-08B/A outbound boundary, over a live response rather than a recording.

    Not a scrubbed result and not a `failed` result: no result. A document built
    from material that should never have been there is the thing being prevented.
    """
    # A BENIGN key carrying real material. `api_key` would be caught one layer
    # earlier by the engine-internal key check, which would prove the wrong
    # thing: the point here is the material rule, which is the only one that can
    # see a credential nested under an innocent business name.
    client = FakeHttpClient(body=pong_body(output={"value": "PONG", "note": secret}))
    executor, live = bridge(contracts, client)
    with pytest.raises(CredentialBoundaryError) as refused:
        executor.execute(envelope(), tool_id=TOOL_ID, payload={"value": "PING"})
    assert secret not in str(refused.value)


def test_a_workflow_that_answers_with_an_n8n_internal_key_fails_closed(contracts):
    client = FakeHttpClient(body=pong_body(output={"value": "PONG", "workflow_id": "Xp7"}))
    result, _, _ = run(contracts, client)
    assert result["state"] == "failed"
    assert result["error"]["code"] == "N8N_OUTPUT_LEAK"


# ===========================================================================
# 6. Diagnostics and evidence
# ===========================================================================


def test_a_resolved_credential_is_written_into_one_header_and_nowhere_else(contracts):
    resolver = EnvironmentCredentialResolver(environ={CREDENTIAL_ENV: CREDENTIAL_VALUE})
    bound = N8nBinding(
        binding_id=BINDING.binding_id,
        workflow_ref=WORKFLOW_REF,
        output_path=BINDING.output_path,
        credential_refs=(CREDENTIAL_REF,),
    )
    result, transitions, live = run(contracts, binding=bound, resolver=resolver)
    assert result["state"] == "succeeded"

    # It IS in the outgoing request — that is the whole point of resolving it.
    assert CREDENTIAL_VALUE in json.dumps(dict(live.client.requests[0].headers))
    # And in nothing else the run produced.
    assert CREDENTIAL_VALUE not in everything_written(result, transitions, live.journal)
    assert CREDENTIAL_VALUE not in json.dumps(dispatch_evidence(live.journal, result))
    assert CREDENTIAL_VALUE not in repr(live)
    assert CREDENTIAL_VALUE not in repr(resolver)
    assert live.journal[-1].authenticated is True


def test_a_resolved_header_cannot_be_printed_or_pickled():
    resolver = EnvironmentCredentialResolver(environ={CREDENTIAL_ENV: CREDENTIAL_VALUE})
    header = resolver.resolve(CREDENTIAL_REF)
    assert CREDENTIAL_VALUE not in repr(header)
    assert CREDENTIAL_VALUE not in str(header)
    assert CREDENTIAL_VALUE not in "%s" % header
    assert "<redacted>" in repr(header)
    import pickle

    # `__getstate__` refuses, so a header cannot be written into a cache, a
    # queue, a session file or a crash dump by anything that pickles objects
    # generically.
    with pytest.raises(CredentialResolutionError):
        pickle.dumps(header)


def test_the_dispatch_evidence_is_scanned_by_the_credential_boundary(contracts):
    """The joined document goes through the same scan as a result. Prove it fires."""
    result, _, live = run(contracts)
    tainted = dict(result)
    tainted["correlation_id"] = "corr-%s" % SECRETS[0]
    with pytest.raises(CredentialBoundaryError):
        dispatch_evidence(live.journal, tainted)


def test_the_dispatch_record_has_no_field_that_could_hold_a_credential(contracts):
    """Absent, not redacted. A redaction is a decision someone can later widen."""
    result, _, live = run(contracts)
    record = live.journal[0].as_evidence()
    assert set(record) == {
        "attempted",
        "authenticated",
        "correlation_id",
        "endpoint_fingerprint",
        "http_status",
        "idempotency_key",
        "identity_verified",
        "outcome",
        "workflow_artifact_sha256",
        "workflow_identity",
        "workflow_ref",
    }
    # No URL, no header block, no body, no payload, no error text.
    for absent in ("url", "endpoint", "headers", "body", "payload", "request", "response"):
        assert absent not in record


@pytest.mark.parametrize("secret", SECRETS)
def test_a_payload_carrying_a_credential_never_reaches_the_client(secret):
    """Defence in depth: the transport rescans even though the executor already did."""
    live = transport()
    with pytest.raises(CredentialBoundaryError) as refused:
        dispatch(live, payload={"value": "PING", "config": {"nested": {"token": secret}}})
    assert live.client.requests == []
    assert live.journal == []
    assert secret not in str(refused.value)


def test_the_smoke_report_carries_no_endpoint_and_no_material(contracts):
    """What an operator is handed after a live run, checked as one document."""
    resolver = EnvironmentCredentialResolver(environ={CREDENTIAL_ENV: CREDENTIAL_VALUE})
    bound = N8nBinding(
        binding_id=BINDING.binding_id,
        workflow_ref=WORKFLOW_REF,
        output_path=BINDING.output_path,
        credential_refs=(CREDENTIAL_REF,),
    )
    result, _, live = run(contracts, binding=bound, resolver=resolver)
    report = json.dumps(dispatch_evidence(live.journal, result), sort_keys=True)
    assert CREDENTIAL_VALUE not in report
    assert BASE_URL not in report
    assert WORKFLOW_IDENTITY in report

