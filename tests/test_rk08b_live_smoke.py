"""RK-08B/B — the live smoke's gates, exercised without a live anything.

The smoke is the one thing in this repository that is *supposed* to open a
connection, which makes it the one thing whose refusal path has to be tested
hardest. Every test here runs the real gates, the real pin check, the real
binding, the real validator and the real evidence assembly. What none of them
does is reach a network: the socket is injected as a fake, exactly as it is for
the transport tests.

The test that matters most is the boring one —
``test_the_smoke_is_refused_when_the_opt_in_is_absent`` and its neighbours. A
smoke that dispatches by accident against an operator's automation host is not a
test failure, it is an incident, and "it is opt-in" is a claim that has to be
executed rather than asserted in a runbook.
"""

from __future__ import annotations

import json

import pytest

from ginkgo_runtime.smoke import (
    OPT_IN_VARIABLE,
    PING_INPUT,
    PONG_OUTPUT,
    SMOKE_CORRELATION_ID,
    SMOKE_IDEMPOTENCY_KEY,
    SMOKE_WORKFLOW_IDENTITY,
    SmokeRefused,
    check_preconditions,
    run_live_smoke,
    workflow_artifact_sha256,
)
from ginkgo_runtime.smoke.__main__ import EXIT_FAILED, EXIT_OK, EXIT_REFUSED, main
from ginkgo_runtime.smoke.n8n_ping import BASE_URL_VARIABLE, CREDENTIAL_REF_VARIABLE

from .test_rk08b_live_transport import (
    CREDENTIAL_ENV,
    CREDENTIAL_REF,
    CREDENTIAL_VALUE,
    FakeHttpClient,
)

BASE_URL = "http://127.0.0.1:5678"

#: The environment that opts in. Written out here and nowhere else, so that
#: `test_the_opt_in_variable_is_set_nowhere_in_the_repository` can prove the
#: repository itself never sets it.
OPTED_IN = {OPT_IN_VARIABLE: "1", BASE_URL_VARIABLE: BASE_URL}


def smoke_body(identity=SMOKE_WORKFLOW_IDENTITY, value="PONG", status="success"):
    return {
        "ginkgo": {
            "workflow": identity,
            "status": status,
            "correlation_id": SMOKE_CORRELATION_ID,
            "output": {"value": value},
        }
    }


def client(**kwargs):
    kwargs.setdefault("body", smoke_body())
    return FakeHttpClient(**kwargs)


# ===========================================================================
# The gates
# ===========================================================================


@pytest.mark.parametrize(
    "environ",
    [
        {},
        {BASE_URL_VARIABLE: BASE_URL},
        {OPT_IN_VARIABLE: "", BASE_URL_VARIABLE: BASE_URL},
        {OPT_IN_VARIABLE: "0", BASE_URL_VARIABLE: BASE_URL},
        {OPT_IN_VARIABLE: "true", BASE_URL_VARIABLE: BASE_URL},
        {OPT_IN_VARIABLE: "yes", BASE_URL_VARIABLE: BASE_URL},
        {OPT_IN_VARIABLE: "TRUE", BASE_URL_VARIABLE: BASE_URL},
        {OPT_IN_VARIABLE: " 1", BASE_URL_VARIABLE: BASE_URL},
    ],
)
def test_the_smoke_is_refused_when_the_opt_in_is_absent(environ):
    """One value opts in. Everything that merely looks like consent does not."""
    fake = client()
    with pytest.raises(SmokeRefused):
        run_live_smoke(environ, client=fake)
    assert fake.requests == []


def test_the_smoke_is_refused_when_no_endpoint_is_named():
    """It does not fall back to a conventional address. Guessing is the whole risk."""
    fake = client()
    with pytest.raises(SmokeRefused) as refused:
        run_live_smoke({OPT_IN_VARIABLE: "1"}, client=fake)
    assert fake.requests == []
    assert "endpoint" in str(refused.value)
    assert "5678" not in str(refused.value)


@pytest.mark.parametrize(
    "url",
    [
        "http://user:hunter2hunter2@127.0.0.1:5678",
        "http://127.0.0.1:5678?apiKey=AKIAIOSFODNN7EXAMPLE",
        "ftp://127.0.0.1",
        "localhost:5678",
    ],
)
def test_an_endpoint_that_could_carry_a_credential_refuses_the_smoke(url):
    fake = client()
    with pytest.raises(SmokeRefused) as refused:
        run_live_smoke({OPT_IN_VARIABLE: "1", BASE_URL_VARIABLE: url}, client=fake)
    assert fake.requests == []
    assert "AKIAIOSFODNN7EXAMPLE" not in str(refused.value)
    assert "hunter2hunter2" not in str(refused.value)


def test_a_bound_reference_that_does_not_resolve_refuses_the_smoke():
    """Fail closed, before dispatch — never an unauthenticated fallback."""
    environ = dict(OPTED_IN, **{CREDENTIAL_REF_VARIABLE: CREDENTIAL_REF})
    fake = client()
    with pytest.raises(SmokeRefused):
        run_live_smoke(environ, client=fake)
    assert fake.requests == []


def test_every_gate_is_reported_at_once_rather_than_one_at_a_time():
    checks = check_preconditions({})
    assert [check.name for check in checks] == [
        "opt-in",
        "endpoint",
        "workflow-artifact",
        "credential-reference",
        "workflow-imported",
    ]
    blocking = [check for check in checks if check.blocking and not check.satisfied]
    assert {check.name for check in blocking} == {"opt-in", "endpoint"}


def test_the_unverifiable_gate_is_reported_as_unverifiable_and_never_as_a_pass():
    """Whether the workflow is imported is not knowable from here, and it says so."""
    checks = {check.name: check for check in check_preconditions(OPTED_IN)}
    imported = checks["workflow-imported"]
    assert imported.satisfied is False
    assert imported.blocking is False
    assert imported.verdict == "UNVERIFIABLE"
    # And it does not block: an operator who has imported the workflow is not
    # prevented from running the smoke by a check that cannot be satisfied.
    assert all(
        check.satisfied for check in check_preconditions(OPTED_IN) if check.blocking
    )


def test_no_precondition_message_quotes_anything_read_from_the_environment():
    environ = dict(
        OPTED_IN,
        **{CREDENTIAL_REF_VARIABLE: CREDENTIAL_REF, CREDENTIAL_ENV: CREDENTIAL_VALUE},
    )
    printed = " ".join(check.as_line() for check in check_preconditions(environ))
    assert CREDENTIAL_VALUE not in printed
    assert CREDENTIAL_ENV not in printed


# ===========================================================================
# The pin
# ===========================================================================


def test_the_committed_workflow_is_what_the_smoke_pins():
    digest = workflow_artifact_sha256()
    assert len(digest) == 64
    # Stable across calls: it is a digest of bytes on disk, not of a re-serialised
    # document, so it does not drift with whichever JSON writer last touched it.
    assert digest == workflow_artifact_sha256()


def test_the_pinned_workflow_calls_nothing_and_stores_nothing():
    """The smoke's safety claims, asserted against the document rather than the README.

    "No third-party API call, no CRM, no LLM" is a property of this file. A
    runbook can say it; only a test keeps it true when someone adds a node.
    """
    import os

    from ginkgo_runtime.catalog.build import default_root
    from ginkgo_runtime.smoke import WORKFLOW_ARTIFACT

    with open(os.path.join(default_root(), *WORKFLOW_ARTIFACT), encoding="utf-8") as fh:
        document = json.load(fh)

    permitted = {
        "n8n-nodes-base.webhook",
        "n8n-nodes-base.set",
        "n8n-nodes-base.respondToWebhook",
    }
    kinds = {node["type"] for node in document["nodes"]}
    assert kinds <= permitted, kinds
    assert len(document["nodes"]) == 3

    serialised = json.dumps(document)
    # No credential is attached to any node, and no node exists that could make
    # an outbound call, touch a database or reach a model.
    assert "credentials" not in serialised
    for forbidden in (
        "httpRequest",
        "postgres",
        "mysql",
        "googleSheets",
        "openAi",
        "lmChat",
        "executeWorkflow",
        "executeCommand",
        "evolution",
        "callima",
    ):
        assert forbidden.lower() not in serialised.lower(), forbidden

    # And it persists nothing: a smoke run leaves no execution data behind.
    assert document["settings"]["saveDataSuccessExecution"] == "none"
    assert document["settings"]["saveDataErrorExecution"] == "none"

    # The identity the transport verifies is in the document, not in the request.
    assert SMOKE_WORKFLOW_IDENTITY in serialised


def test_the_opt_in_variable_is_set_nowhere_in_the_repository():
    """Not in CI, not in a script, not in a fixture. Only an operator sets it."""
    import os
    import subprocess

    from ginkgo_runtime.catalog.build import default_root

    root = default_root()
    result = subprocess.run(
        ["git", "-C", root, "grep", "-l", "%s=1" % OPT_IN_VARIABLE],
        capture_output=True,
        text=True,
    )
    setters = [
        line
        for line in result.stdout.splitlines()
        if line and os.path.basename(line) not in ("test_rk08b_live_smoke.py",)
    ]
    # Documentation may quote the command an operator types; nothing executable
    # may set it.
    executable = [name for name in setters if name.endswith((".py", ".yml", ".yaml", ".sh"))]
    assert executable == [], executable


# ===========================================================================
# The run itself, over a fake socket
# ===========================================================================


def test_ping_pong_normalises_to_an_independently_validated_success():
    report = run_live_smoke(OPTED_IN, client=client())
    assert report["passed"] is True
    assert report["state"] == "succeeded"
    assert report["output"] == dict(PONG_OUTPUT)
    assert report["independent_validation"] == {
        "required": True,
        "satisfied": True,
        "validator_ref": "rk08b-b-ping-pong-validator",
    }


def test_the_engines_own_word_for_success_is_not_enough():
    """GL-07. A workflow reporting `success` while returning the wrong thing fails."""
    report = run_live_smoke(OPTED_IN, client=client(body=smoke_body(value="PING")))
    assert report["state"] == "succeeded"
    assert report["independent_validation"]["satisfied"] is False
    assert report["passed"] is False


def test_the_dispatched_input_is_the_ping_and_nothing_else():
    fake = client()
    run_live_smoke(OPTED_IN, client=fake)
    body = json.loads(fake.requests[0].body.decode("utf-8"))
    assert body["input"] == dict(PING_INPUT)
    assert body["correlation_id"] == SMOKE_CORRELATION_ID
    assert body["idempotency_key"] == SMOKE_IDEMPOTENCY_KEY


def test_the_report_proves_dispatch_identity_outcome_and_correlation():
    report = run_live_smoke(OPTED_IN, client=client())
    evidence = report["evidence"]
    assert evidence["dispatch_attempted"] is True
    record = evidence["dispatches"][0]
    assert record["workflow_identity"] == SMOKE_WORKFLOW_IDENTITY
    assert record["workflow_artifact_sha256"] == workflow_artifact_sha256()
    assert record["identity_verified"] is True
    assert record["outcome"] == "observed"
    assert record["correlation_id"] == SMOKE_CORRELATION_ID
    assert evidence["normalized"]["state"] == "succeeded"
    assert evidence["normalized"]["executor_ref"] == "rk-n8n-executor"


def test_a_wrong_workflow_at_the_endpoint_fails_the_smoke_rather_than_passing_it():
    report = run_live_smoke(
        OPTED_IN, client=client(body=smoke_body(identity="callima.inbound@3"))
    )
    assert report["passed"] is False
    assert report["evidence"]["normalized"]["error_code"] == "N8N_WORKFLOW_MISMATCH"
    assert "callima" not in json.dumps(report)


def test_a_resolved_credential_never_appears_in_the_smoke_report():
    environ = dict(
        OPTED_IN,
        **{CREDENTIAL_REF_VARIABLE: CREDENTIAL_REF, CREDENTIAL_ENV: CREDENTIAL_VALUE},
    )
    fake = client()
    report = run_live_smoke(environ, client=fake)
    assert report["passed"] is True
    assert CREDENTIAL_VALUE in json.dumps(dict(fake.requests[0].headers))
    assert CREDENTIAL_VALUE not in json.dumps(report)
    assert report["evidence"]["dispatches"][0]["authenticated"] is True


# ===========================================================================
# The CLI
# ===========================================================================


def test_the_cli_reports_preconditions_without_contacting_anything(monkeypatch, capsys):
    monkeypatch.delenv(OPT_IN_VARIABLE, raising=False)
    monkeypatch.delenv(BASE_URL_VARIABLE, raising=False)
    assert main(["preconditions"]) == EXIT_REFUSED
    printed = capsys.readouterr().out
    assert "MISSING opt-in" in printed
    assert "UNVERIFIABLE workflow-imported" in printed


def test_the_cli_refuses_a_ping_without_the_opt_in(monkeypatch, capsys):
    monkeypatch.delenv(OPT_IN_VARIABLE, raising=False)
    assert main(["ping", "--base-url", BASE_URL]) == EXIT_REFUSED
    assert "refused" in capsys.readouterr().err


def test_the_cli_prints_the_pin(capsys):
    assert main(["pin"]) == EXIT_OK
    assert capsys.readouterr().out.strip() == workflow_artifact_sha256()


def test_refused_and_failed_are_different_exit_codes():
    """"Never contacted anything" and "contacted n8n and it was wrong" differ."""
    assert EXIT_OK != EXIT_FAILED != EXIT_REFUSED != EXIT_OK


def test_the_cli_never_imports_the_socket_module_for_a_refused_run(monkeypatch):
    """A refused smoke has not even loaded the module that owns the socket."""
    import subprocess
    import sys

    from ginkgo_runtime.catalog.build import default_root

    probe = (
        "import sys; sys.path.insert(0, %r);"
        "from ginkgo_runtime.smoke.__main__ import main;"
        "code = main(['ping', '--base-url', 'http://127.0.0.1:5678']);"
        "loaded = [m for m in sys.modules if 'n8n_http_client' in m];"
        "print(code, loaded)" % (default_root() + "/src")
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        env={"PATH": "", "SYSTEMROOT": ""},
    )
    assert result.stdout.strip() == "2 []", (result.stdout, result.stderr)
