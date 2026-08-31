"""RK-08B-A.1 — the credential boundary, inbound and outbound.

**The adversarial corpus in this file is independent of the implementation.**
Every secret below was written by hand from the vendors' own published token
formats, and every credential-shaped field name was written by hand from the
names this material is stored under in the wild. Nothing here is generated from
``CREDENTIAL_NAME_TOKENS`` or ``RAW_SECRET_RULES``, and
``test_the_adversarial_corpus_is_not_copied_from_the_implementation`` is the
assertion that keeps it that way: a suite that parametrised over the module's own
constants would prove the module agrees with itself and nothing else. That is the
exact defect the RK-08B/A review found in the credential test it replaced.

Every value below is fabricated. They are shaped like real credentials — that is
the point — and none of them is one.
"""

from __future__ import annotations

import json
import re

import pytest

from ginkgo_runtime.clock import SteppingClock
from ginkgo_runtime.executor import (
    CredentialBoundaryError,
    InProcessExecutor,
    N8nBinding,
    N8nExecutor,
    N8nObservation,
    RecordedTransport,
    build_execution_request,
    build_execution_result,
    credential_findings,
    is_credential_name,
)

from .conftest import capability_request

WORKFLOW_REF = "wf-credential-boundary-0001"
TOOL_ID = "rk-echo-transform"


# ===========================================================================
# The adversarial corpus — hand-written, not derived
# ===========================================================================

#: Fabricated material in the published shapes of real credentials. Written from
#: the vendors' documented token formats, not from this kernel's rule table.
RAW_SECRETS: dict[str, str] = {
    "aws-key-id": "AKIAQYLPMN5HGXTZ7WVB",
    "google-key": "AIzaSyD9tSrke72PouQMnMXa7eZSW0jkFMBWyQx",
    "github-pat": "ghp_9Kq2XrT4mW7bN1cV6sD8fH3jL5pZ0aQeR2yU",
    "slack-bot": "xoxb-2947153860-4820193756-9fKdmZqRtWvXbNcYhJuP",
    "stripe-live": "sk_live_51H8xQ2LmNpRsTuVwXyZaBcDe",
    "openai-project": "sk-proj-tN4vQ8mRz2LkX7bW9pYc3JdFhG6sA1eU",
    "jwt": (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        ".eyJzdWIiOiI5OTgyMSIsInRlbmFudCI6ImV2b2x1dGlvbiJ9"
        ".Kx7bQ2mVzR4tLpW9nYcJdF6hG1sA3eU0"
    ),
    "pem-block": "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA3q2\n",
    "bearer-header": "Bearer 7f3c9a1b4e6d8f2a0c5b9e1d3a7f4c6b",
    "database-url": "postgresql://svc_ops:h9Tq2Lm4Pw@db.internal:5432/ledger",
    "inline-env": "export SHOPEE_API_KEY=Zx91LmQd7Rv2Bt6YuI3o",
}

#: Field names credential material is stored under. Aliases, casings and
#: compounds — none of them an exact match for anything in the frozen contract's
#: ``x-ginkgo-forbidden-fields``, which is what makes them adversarial.
CREDENTIAL_ALIASES: tuple[str, ...] = (
    "httpHeaderAuth",
    "X-Api-Key",
    "clientSecret",
    "refresh_token_v2",
    "vault_passphrase",
    "sessionToken",
    "PRIVATE_KEY",
    "n8nCredentials",
    "authToken",
    "pwd",
    "oauth2_bearer",
    "signingKey",
    "Set-Cookie",
    "api_key_prod",
)

#: Ordinary business field names that must keep working. Several are deliberately
#: one edit away from a credential name — a boundary that rejected these would be
#: switched off within a sprint, and a switched-off boundary is not a boundary.
BENIGN_NAMES: tuple[str, ...] = (
    "author",
    "authored_at",
    "secretary",
    "session_id",
    "cookbook",
    "credit_note",
    "passenger_count",
    "message",
    "length",
    "tool_id",
    "key_findings",
    "signatory_name",
    "sasquatch_sightings",
)

#: Ordinary business values that must keep working, including the two shapes this
#: kernel's own documents are full of: SHA-256 digests and UUIDs.
BENIGN_VALUES: tuple[str, ...] = (
    "hello from the reference slice",
    "a" * 64,
    "3f786850e387550fdab836ed7e6dc881de23001b" + "c" * 24,
    "11111111-1111-4111-8111-111111111111",
    "2026-08-21T12:00:00.000Z",
    "https://docs.example.com/runbooks/onboarding",
    "basic authentication is required for this endpoint",
    "the bearer of this record is the account owner",
    "R$ 1.249,90",
)

PLACEHOLDER = "<supplied-by-the-adapter-layer>"


def nest(value, *, depth: int = 5, leaf: str = "leaf"):
    """Bury ``value`` under ``depth`` levels of ordinary business structure."""
    node = {leaf: value}
    for level in reversed(range(depth)):
        node = {"level_%d" % level: [{"items": node}]}
    return node


# ===========================================================================
# Fixtures — the bridge, wired to a transport that records everything
# ===========================================================================


def engine_document(output):
    """An n8n execution document carrying **no** engine-internal key.

    Deliberately clean of ``workflowId``, ``runData`` and ``credentials``: the
    existing leak check keys on exactly those, so a document containing one would
    let that check fire and leave the credential rule untested. Anything these
    tests catch, they catch on the credential rule alone.
    """
    return {"status": "success", "data": {"ginkgo": {"output": output}}}


def binding(output_path=("data", "ginkgo", "output"), credential_refs=("cred-ref-01",)):
    return N8nBinding(
        binding_id="n8n:rk-credential-boundary@1",
        workflow_ref=WORKFLOW_REF,
        output_path=output_path,
        credential_refs=credential_refs,
    )


def envelope(**overrides):
    built = build_execution_request(
        execution_id="exec-cred-0001",
        request=capability_request(),
        decision={"decision_id": "dec-0001", "decision_hash": "a" * 64},
        input_fingerprint=None,
    )
    built.update(overrides)
    return built


def bridge(contracts, output=None, *, bound=None):
    clock = SteppingClock()
    transport = RecordedTransport(
        script={
            WORKFLOW_REF: [
                N8nObservation("success", engine_document(output or {"echoed": "hello"}))
            ]
        }
    )
    executor = N8nExecutor(
        contracts=contracts,
        clock=clock,
        transport=transport,
        bindings={TOOL_ID: bound or binding()},
    )
    return executor, transport, clock


# ===========================================================================
# A — inbound: nested raw credential material
# ===========================================================================


@pytest.mark.parametrize("name", sorted(RAW_SECRETS))
def test_a_nested_raw_credential_never_reaches_the_transport(contracts, name):
    """Raw material, five levels down, under an entirely innocent field name.

    No field-name rule can see this one: the leaf is called ``delivery_note``.
    Only a value scan that walks arbitrary structure catches it, which is the
    whole of blocker 2 — the smuggling surface is the shape of the payload, not a
    list of known top-level fields.
    """
    executor, transport, clock = bridge(contracts)
    payload = {"order_id": "SO-4471", **nest(RAW_SECRETS[name], leaf="delivery_note")}

    with pytest.raises(CredentialBoundaryError) as excinfo:
        executor.execute(envelope(), tool_id=TOOL_ID, payload=payload)

    assert transport.calls == []
    assert transport.dispatched == []
    assert RAW_SECRETS[name] not in str(excinfo.value)


def test_a_credential_smuggled_through_an_artifact_block_is_refused(contracts):
    executor, transport, _ = bridge(contracts)
    payload = {
        "artifacts": [
            {"kind": "report", "uri": "s3://bucket/report.pdf"},
            {"kind": "callback", "config": {"headers": {"note": RAW_SECRETS["bearer-header"]}}},
        ]
    }
    with pytest.raises(CredentialBoundaryError):
        executor.execute(envelope(), tool_id=TOOL_ID, payload=payload)
    assert transport.dispatched == []


def test_a_credential_smuggled_through_the_envelope_is_refused(contracts):
    """The envelope is schema-valid and still carries material, in a string field."""
    executor, transport, _ = bridge(contracts)
    smuggled = envelope(idempotency_key="idem-" + RAW_SECRETS["github-pat"])

    with pytest.raises(CredentialBoundaryError):
        executor.execute(smuggled, tool_id=TOOL_ID, payload={"message": "hello"})
    assert transport.dispatched == []


def test_a_value_in_credential_refs_is_refused_though_the_field_name_is_not(contracts):
    """``credential_refs`` is exempt by NAME and never by CONTENT.

    The frozen field is declared to hold identifiers. A value there is the
    contract violation its own description warns about, and nothing else in the
    bridge would notice it.
    """
    executor, transport, _ = bridge(contracts)
    clean = envelope(credential_refs=["cred-ref-evolution-01"])
    executor.execute(clean, tool_id=TOOL_ID, payload={"message": "hello"})
    assert transport.dispatched

    executor, transport, _ = bridge(contracts)
    with pytest.raises(CredentialBoundaryError):
        executor.execute(
            envelope(credential_refs=[RAW_SECRETS["aws-key-id"]]),
            tool_id=TOOL_ID,
            payload={"message": "hello"},
        )
    assert transport.dispatched == []


def test_a_binding_holding_a_value_instead_of_a_reference_is_refused(contracts):
    """A misconfigured binding is caught before it can hand a value across."""
    executor, transport, _ = bridge(
        contracts, bound=binding(credential_refs=(RAW_SECRETS["stripe-live"],))
    )
    with pytest.raises(CredentialBoundaryError):
        executor.execute(envelope(), tool_id=TOOL_ID, payload={"message": "hello"})
    assert transport.dispatched == []


# ===========================================================================
# B — inbound: credential aliases, with no material at all
# ===========================================================================


@pytest.mark.parametrize("alias", CREDENTIAL_ALIASES)
def test_a_credential_alias_is_refused_even_holding_a_placeholder(contracts, alias):
    """The name alone is disqualifying, and it has to be.

    The value here is the literal string ``<supplied-by-the-adapter-layer>`` — no
    rule in the value scan can fire on it. If this passes, the field is a live
    credential surface waiting for its first real value, and the boundary would
    only notice on the day it stopped being a drill.
    """
    executor, transport, _ = bridge(contracts)
    payload = {"customer": {"profile": nest({alias: PLACEHOLDER}, depth=3)}}

    with pytest.raises(CredentialBoundaryError) as excinfo:
        executor.execute(envelope(), tool_id=TOOL_ID, payload=payload)

    assert transport.dispatched == []
    assert any(f.rule == "credential-field-name" for f in excinfo.value.findings)


def test_a_credential_alias_is_refused_as_a_list_element(contracts):
    executor, transport, _ = bridge(contracts)
    payload = {"connections": [{"label": "primary"}, {"apiKey": PLACEHOLDER}]}
    with pytest.raises(CredentialBoundaryError):
        executor.execute(envelope(), tool_id=TOOL_ID, payload=payload)
    assert transport.dispatched == []


# ===========================================================================
# C — outbound: nested credential in the projected output
# ===========================================================================


@pytest.mark.parametrize("name", sorted(RAW_SECRETS))
def test_a_nested_credential_in_the_projected_output_is_refused(contracts, name):
    """Blocker 1. The engine document carries no n8n-internal key at all.

    So the existing engine-internal leak check cannot be what fires here, and a
    projected output that is clean by that check is still refused when it carries
    credential material under a business name three levels down.
    """
    executor, transport, _ = bridge(
        contracts, output={"echoed": "hello", **nest(RAW_SECRETS[name], leaf="handover")}
    )
    with pytest.raises(CredentialBoundaryError) as excinfo:
        executor.execute(envelope(), tool_id=TOOL_ID, payload={"message": "hello"})
    assert RAW_SECRETS[name] not in str(excinfo.value)


@pytest.mark.parametrize("alias", CREDENTIAL_ALIASES)
def test_a_credential_alias_in_the_projected_output_is_refused(contracts, alias):
    executor, _, _ = bridge(contracts, output={"echoed": "hello", alias: PLACEHOLDER})
    with pytest.raises(CredentialBoundaryError):
        executor.execute(envelope(), tool_id=TOOL_ID, payload={"message": "hello"})


def test_the_in_process_executor_is_behind_the_same_outbound_boundary(contracts):
    """One result builder, one boundary. Both executors, not just the bridge."""
    executor = InProcessExecutor(
        contracts=contracts,
        clock=SteppingClock(),
        tools={TOOL_ID: lambda payload: {"receipt": nest(RAW_SECRETS["jwt"])}},
    )
    with pytest.raises(CredentialBoundaryError):
        executor.execute(envelope(), tool_id=TOOL_ID, payload={"message": "hello"})


def test_a_result_builder_refuses_credential_material_in_an_error(contracts):
    """Diagnostics are an outbound surface too, and the noisiest one."""
    with pytest.raises(CredentialBoundaryError):
        build_execution_result(
            contracts,
            envelope(),
            state="failed",
            started_at="2026-08-21T12:00:00.000Z",
            ended_at="2026-08-21T12:00:00.400Z",
            executor_ref="rk-n8n-executor",
            error={
                "code": "PROVIDER_REJECTED",
                "message": "upstream said: %s" % RAW_SECRETS["inline-env"],
                "category": "PROVIDER",
                "retryable": False,
            },
        )


# ===========================================================================
# D — the rejected value is never echoed
# ===========================================================================


@pytest.mark.parametrize("name", sorted(RAW_SECRETS))
def test_the_refused_material_is_never_written_down(contracts, name):
    """Not in the message, not in the findings, not in a repr, not in a log.

    ``error.schema.json``: *"Secrets, credentials, raw endpoints and provider
    payloads MUST NOT appear in message or details."* A boundary that reported
    what it caught by quoting it would move the leak from the result into the log
    and call the job done.
    """
    secret = RAW_SECRETS[name]
    executor, _, _ = bridge(contracts)

    with pytest.raises(CredentialBoundaryError) as excinfo:
        executor.execute(
            envelope(), tool_id=TOOL_ID, payload=nest(secret, leaf="delivery_note")
        )

    error = excinfo.value
    written = "\n".join(
        (
            str(error),
            repr(error),
            "".join(error.args and str(arg) for arg in error.args),
            json.dumps([[f.pointer, f.rule] for f in error.findings]),
            error.surface,
        )
    )
    assert secret not in written
    # Not even a fragment long enough to be useful. Sixteen characters of an AWS
    # key id or a JWT signature is a working head start, and a truncated echo is
    # still an echo.
    for start in range(0, max(1, len(secret) - 16)):
        assert secret[start : start + 16] not in written


def test_the_bridge_no_longer_quotes_an_upstream_status(contracts):
    """The previous code echoed 64 characters of the engine's own status string.

    Upstream text is upstream text however short the slice is, and a status field
    is as good a place to hide material as any other. Nothing is quoted now, and
    the state is still refused rather than read as success.
    """
    marker = "unmapped-status-" + RAW_SECRETS["aws-key-id"]
    executor, _, _ = bridge(contracts)
    executor.transport.script = {
        WORKFLOW_REF: [N8nObservation(marker, engine_document({"echoed": "hello"}))]
    }
    with pytest.raises(CredentialBoundaryError) as excinfo:
        executor.execute(envelope(), tool_id=TOOL_ID, payload={"message": "hello"})
    assert RAW_SECRETS["aws-key-id"] not in str(excinfo.value)


def test_an_unmapped_status_carrying_no_material_still_names_no_upstream_text(contracts):
    executor, _, _ = bridge(contracts)
    executor.transport.script = {
        WORKFLOW_REF: [N8nObservation("finished-ok", engine_document({"echoed": "hello"}))]
    }
    result, _ = executor.execute(envelope(), tool_id=TOOL_ID, payload={"message": "hello"})
    assert result["state"] == "failed"
    assert result["error"]["code"] == "N8N_STATUS_UNKNOWN"
    assert "finished-ok" not in json.dumps(result)


# ===========================================================================
# E — the transport is never invoked after a pre-dispatch rejection
# ===========================================================================


def test_nothing_at_all_happens_after_an_inbound_rejection(contracts):
    """Zero transport calls, and zero clock readings.

    The clock is read once per transition, so an unread clock is proof that no
    transition was recorded either — the rejection precedes the state machine,
    not merely the socket.
    """
    executor, transport, clock = bridge(contracts)
    with pytest.raises(CredentialBoundaryError):
        executor.execute(
            envelope(), tool_id=TOOL_ID, payload={"vault": {"apiKey": PLACEHOLDER}}
        )
    assert transport.calls == []
    assert transport.dispatched == []
    assert clock.readings == 0


def test_the_transport_is_never_reached_for_any_case_in_the_corpus(contracts):
    """Every adversarial case, counted once, over one shared transport."""
    for secret in RAW_SECRETS.values():
        executor, transport, _ = bridge(contracts)
        with pytest.raises(CredentialBoundaryError):
            executor.execute(envelope(), tool_id=TOOL_ID, payload=nest(secret))
        assert len(transport.dispatched) == 0
    for alias in CREDENTIAL_ALIASES:
        executor, transport, _ = bridge(contracts)
        with pytest.raises(CredentialBoundaryError):
            executor.execute(envelope(), tool_id=TOOL_ID, payload={alias: PLACEHOLDER})
        assert len(transport.dispatched) == 0


# ===========================================================================
# F — no result is emitted on an outbound rejection
# ===========================================================================


def test_no_result_is_emitted_when_the_output_carries_a_credential(contracts):
    """Not a success, not a scrubbed success, and not a `failed` result either.

    A ``failed`` result would be a document built from tainted material, and it
    would blame the skill for a binding defect. The call raises instead, and RK-01
    turns a raising downstream into ``DOWNSTREAM_CONTRACT_VIOLATION`` carrying
    only the exception's type.
    """
    executor, _, _ = bridge(contracts, output={"receipt": {"authToken": PLACEHOLDER}})
    with pytest.raises(CredentialBoundaryError) as excinfo:
        executor.execute(envelope(), tool_id=TOOL_ID, payload={"message": "hello"})
    assert not hasattr(excinfo.value, "result")
    assert "succeeded" not in str(excinfo.value)


def test_a_transition_is_never_emitted_carrying_credential_material(contracts):
    executor, _, _ = bridge(contracts, output={"vault": {"secret_key": PLACEHOLDER}})
    with pytest.raises(CredentialBoundaryError):
        executor.execute(envelope(), tool_id=TOOL_ID, payload={"message": "hello"})


# ===========================================================================
# G — benign payloads still pass
# ===========================================================================


@pytest.mark.parametrize("name", BENIGN_NAMES)
def test_a_benign_field_name_still_passes_both_boundaries(contracts, name):
    executor, transport, _ = bridge(contracts, output={name: "ok", "echoed": "hello"})
    result, _ = executor.execute(
        envelope(), tool_id=TOOL_ID, payload={name: "ordinary business data"}
    )
    assert result["state"] == "succeeded"
    assert result["output"][name] == "ok"
    assert transport.dispatched[0]["payload"][name] == "ordinary business data"


@pytest.mark.parametrize("value", BENIGN_VALUES)
def test_a_benign_value_still_passes_both_boundaries(contracts, value):
    executor, _, _ = bridge(contracts, output={"echoed": value})
    result, _ = executor.execute(envelope(), tool_id=TOOL_ID, payload=nest(value))
    assert result["state"] == "succeeded"
    assert result["output"]["echoed"] == value


def test_a_realistic_business_payload_passes_unchanged(contracts):
    """One document with everything an ordinary order carries, and nothing else."""
    payload = {
        "order_id": "SO-4471",
        "session_id": "11111111-1111-4111-8111-111111111111",
        "author": "ana",
        "lines": [
            {"sku": "GK-100", "qty": 2, "unit_price": 129.9, "signatory_name": "bo"},
            {"sku": "GK-200", "qty": 1, "unit_price": 89.5, "notes": None},
        ],
        "totals": {"gross": 349.3, "currency": "BRL", "key_findings": ["on time"]},
    }
    executor, transport, _ = bridge(contracts, output={"echoed": "hello", "accepted": True})
    result, _ = executor.execute(envelope(), tool_id=TOOL_ID, payload=payload)
    assert result["state"] == "succeeded"
    assert transport.dispatched[0]["payload"] == payload
    assert credential_findings(payload) == []


# ===========================================================================
# Fail-closed: an unfinished scan is never a clean scan
# ===========================================================================


def test_a_document_deeper_than_the_limit_is_refused_not_truncated(contracts):
    deep: dict = {"leaf": "value"}
    for _ in range(80):
        deep = {"nested": deep}
    executor, transport, _ = bridge(contracts)
    with pytest.raises(CredentialBoundaryError) as excinfo:
        executor.execute(envelope(), tool_id=TOOL_ID, payload=deep)
    assert any(f.rule == "document-too-deep" for f in excinfo.value.findings)
    assert transport.dispatched == []


def test_a_value_the_scan_cannot_read_is_refused(contracts):
    """An opaque object cannot be shown to be free of credential material."""

    class Opaque:
        def __repr__(self):
            return RAW_SECRETS["aws-key-id"]

    executor, transport, _ = bridge(contracts)
    with pytest.raises(CredentialBoundaryError) as excinfo:
        executor.execute(envelope(), tool_id=TOOL_ID, payload={"blob": Opaque()})
    assert any(f.rule == "unscannable-type" for f in excinfo.value.findings)
    assert RAW_SECRETS["aws-key-id"] not in str(excinfo.value)
    assert transport.dispatched == []


def test_a_key_the_scan_cannot_read_is_refused(contracts):
    executor, transport, _ = bridge(contracts)
    with pytest.raises(CredentialBoundaryError) as excinfo:
        executor.execute(envelope(), tool_id=TOOL_ID, payload={("tuple", "key"): "value"})
    assert any(f.rule == "unscannable-key" for f in excinfo.value.findings)
    assert transport.dispatched == []


def test_a_credential_used_as_a_mapping_key_is_refused(contracts):
    """``{"<an api key>": "quota"}`` indexes something by the credential itself.

    A value-only scan walks straight past it, and the field name rule has nothing
    to match — the key is material, not a name.
    """
    executor, transport, _ = bridge(contracts)
    payload = {"quotas": {RAW_SECRETS["aws-key-id"]: {"calls": 400}}}

    with pytest.raises(CredentialBoundaryError) as excinfo:
        executor.execute(envelope(), tool_id=TOOL_ID, payload=payload)

    assert transport.dispatched == []
    # And the pointer that reports it does not spell it out.
    assert RAW_SECRETS["aws-key-id"] not in str(excinfo.value)
    assert all(RAW_SECRETS["aws-key-id"] not in f.pointer for f in excinfo.value.findings)


def test_a_field_name_that_is_both_a_name_and_material_is_reported_without_the_material(
    contracts,
):
    """``api_key_AKIA…`` is worth naming and must not be quoted. It gets both."""
    executor, _, _ = bridge(contracts)
    key = "api_key_" + RAW_SECRETS["aws-key-id"]

    with pytest.raises(CredentialBoundaryError) as excinfo:
        executor.execute(envelope(), tool_id=TOOL_ID, payload={key: "used"})

    pointers = " ".join(f.pointer for f in excinfo.value.findings)
    assert "api_key" in pointers
    assert RAW_SECRETS["aws-key-id"] not in pointers
    assert RAW_SECRETS["aws-key-id"] not in str(excinfo.value)


#: Credential field names spelled with letters that are not the letters they look
#: like. Built from code points rather than pasted in: putting a confusable into a
#: file people read reproduces, in the source, the very ambiguity these cases are
#: about — and the linter says so.
LOOKALIKE_NAMES: tuple[tuple[str, str], ...] = (
    ("cyrillic-a", chr(0x0430) + "pi_key"),
    ("greek-omicron", "passw" + chr(0x03BF) + "rd"),
    ("cyrillic-es", chr(0x0441) + "redentials"),
    ("fullwidth", "".join(chr(0xFF00 + ord(c) - 0x20) for c in "api_key")),
)


@pytest.mark.parametrize(("label", "name"), LOOKALIKE_NAMES)
def test_a_credential_name_spelled_with_lookalike_letters_is_refused(
    contracts, label, name
):
    """It reads as a credential field to every reviewer. It must to the rule too."""
    executor, transport, _ = bridge(contracts)
    with pytest.raises(CredentialBoundaryError):
        executor.execute(envelope(), tool_id=TOOL_ID, payload={name: PLACEHOLDER})
    assert transport.dispatched == []


@pytest.mark.parametrize(
    "name", ["descrição", "endereço", "observações", "número"]
)
def test_an_accented_business_field_name_still_passes(contracts, name):
    """The fold is not a ban on non-ASCII names.

    This kernel's payloads are Portuguese. A boundary that refused ``descrição``
    would be switched off within a sprint, and the homoglyph fold above is worth
    nothing if it takes ordinary field names down with it.
    """
    executor, _, _ = bridge(contracts, output={"echoed": "hello"})
    result, _ = executor.execute(envelope(), tool_id=TOOL_ID, payload={name: "texto"})
    assert result["state"] == "succeeded"
    assert not is_credential_name(name)


def test_bytes_are_not_waved_past_as_a_scalar(contracts):
    executor, transport, _ = bridge(contracts)
    with pytest.raises(CredentialBoundaryError):
        executor.execute(
            envelope(), tool_id=TOOL_ID, payload={"blob": RAW_SECRETS["pem-block"].encode()}
        )
    assert transport.dispatched == []


# ===========================================================================
# The policy is the contract's, and the corpus is not the implementation's
# ===========================================================================


def test_every_credential_field_the_frozen_contract_names_is_caught(contracts):
    """Read off ``x-ginkgo-forbidden-fields``, not off this kernel's constants.

    The frozen envelope declares fifteen forbidden fields. Five of them name
    credential material; those five are the floor the boundary must clear, and
    this reads them from the pinned schema so the floor cannot be lowered by
    editing a Python tuple.
    """
    declared = contracts.runtime_execution_request.schema["x-ginkgo-forbidden-fields"]
    credential_bearing = [
        name
        for name in declared
        if re.search(r"credential|secret|api_key|access_token|token", str(name))
    ]
    assert len(credential_bearing) >= 5, declared
    uncaught = [name for name in credential_bearing if not is_credential_name(str(name))]
    assert uncaught == []


def test_the_adversarial_corpus_is_written_down_and_not_derived():
    """H, asserted structurally rather than promised in a docstring.

    Every corpus in this file is parsed back and required to be built from
    **literals only** — no name, no attribute, no call. A suite that read its
    cases out of ``CREDENTIAL_NAME_TOKENS`` could only ever prove that the module
    agrees with itself, and it would keep passing on the day someone deleted half
    the list. This is the check that makes that impossible rather than unlikely.
    """
    import ast
    import pathlib

    corpora = {"RAW_SECRETS", "CREDENTIAL_ALIASES", "BENIGN_NAMES", "BENIGN_VALUES"}
    tree = ast.parse(pathlib.Path(__file__).read_text(encoding="utf-8"))
    checked: set[str] = set()

    for node in tree.body:
        if isinstance(node, ast.AnnAssign):
            targets = [node.target]
        elif isinstance(node, ast.Assign):
            targets = node.targets
        else:
            continue
        named = {t.id for t in targets if isinstance(t, ast.Name)} & corpora
        if not named or node.value is None:
            continue
        checked |= named
        # The ANNOTATION may name types; the VALUE may not name anything.
        for sub in ast.walk(node.value):
            assert not isinstance(sub, ast.Name | ast.Attribute | ast.Call), (
                "%s is computed, not written down: %s" % (named, ast.dump(sub)[:80])
            )

    assert checked == corpora, "a corpus was renamed and this check lost track of it"


def test_no_adversarial_alias_is_a_name_the_frozen_contract_already_lists(contracts):
    """The corpus defeats the exact-name check, which is why it is worth running.

    `ai-stack#39`, on the sibling gap: *"an exact-name check passes
    ``provider_hint``, ``endpoint_url`` and ``commands``."* Not one alias here is
    a field the frozen envelope names, so a boundary built by copying
    ``x-ginkgo-forbidden-fields`` into a set and comparing would catch none of
    them — and the review's finding was that the previous boundary was that
    boundary.
    """
    declared = {
        str(name).lower()
        for name in contracts.runtime_execution_request.schema["x-ginkgo-forbidden-fields"]
    }
    assert declared  # the contract does name fields; the corpus simply avoids them
    assert [alias for alias in CREDENTIAL_ALIASES if alias.lower() in declared] == []


def test_the_corpus_is_adversarial_in_both_directions():
    """Every hostile case is refused and every benign case is admitted."""
    for alias in CREDENTIAL_ALIASES:
        assert is_credential_name(alias), alias
    for name, secret in RAW_SECRETS.items():
        assert credential_findings({"note": secret}), name
    for name in BENIGN_NAMES:
        assert not is_credential_name(name), name
    for value in BENIGN_VALUES:
        assert credential_findings({"note": value}) == [], value
