"""RK-08B-A.1 -- the credential boundary, inbound and outbound.

Two boundaries, one policy, and both of them fail closed.

**Inbound.** Nothing carrying a credential reaches an executor's transport. The
Bus already refuses a *caller-supplied* implementation surface at admission
(:mod:`ginkgo_runtime.bus.admission`), but the executor is handed two things the
Bus never validated as one document: the ``RuntimeExecutionRequest v1`` envelope
*and* a separate ``payload`` argument. ``payload`` is business data, arbitrarily
nested, and no schema constrains its interior -- so a credential can be smuggled
into it through any nesting depth under any name. The bridge scans both before it
dispatches, and a rejection happens **before** the transport is touched.

**Outbound.** Nothing carrying a credential leaves an executor. A provider's
execution document routinely carries credential material, and the projection that
reads one declared path out of it is only as safe as the path the binding
declares. So the assembled ``RuntimeExecutionResult v1`` is scanned at the one
place every executor builds one, and a result carrying credential material is
never emitted at all -- not as a success, not as a failure, not as a diagnostic.

Why raise rather than return a failed result
--------------------------------------------

A credential on either surface is a defect in the *kernel* -- the Bus should have
refused it at admission, or the binding should not have pointed at it. Reporting
it as a ``failed`` execution would blame the skill for that, exactly as
:class:`~ginkgo_runtime.executor.contract.ExecutionEnvelopeError` refuses to
blame the skill for a malformed envelope. And a ``RuntimeExecutionResult`` built
from tainted material is itself the thing being prevented: the only safe number
of such documents to emit is zero. RK-01 already normalises a raising downstream
into ``DOWNSTREAM_CONTRACT_VIOLATION`` reporting the exception's *type* and
nothing else, so the raise crosses the Bus boundary without carrying anything.

Two rules, and why both are needed
----------------------------------

*Field names*, because a field called ``api_key`` announces what it holds
regardless of what is in it -- and the value may be a placeholder today and a
live key tomorrow. The name rule is **token-based**: a key is split on
separators, case boundaries and digits, and each token (and each adjacent pair,
joined) is compared against :data:`CREDENTIAL_NAME_TOKENS`. Token equality rather
than substring matching is what makes ``author`` and ``secretary`` ordinary words
while ``httpHeaderAuth`` and ``x-api-key`` are not.

*Raw material*, because a credential does not stop being one when it is stored
under an innocent name. ``{"note": "Bearer eyJ..."}`` is a leak and no field-name
rule will ever see it. :data:`RAW_SECRET_RULES` matches the published, documented
shapes of well-known credential material. It is deliberately **not** an entropy
heuristic: this kernel's own documents are full of SHA-256 fingerprints and
decision hashes, and a rule that called those secrets would be turned off within
a week, which is the failure mode that matters.

Both rules apply to mapping **keys** as well as to values. A key is a string, and
``{"<an api key>": "quota"}`` indexes something by the credential it belongs to --
a shape that a value-only scan walks straight past. It is also why a pointer is
built from a key with any recognised material cut out of it: a pointer is written
down, and a field can be worth naming and carry a credential at the same time.

Field names are folded before they are tokenised -- compatibility decomposition,
combining marks dropped, then the letters that merely look Latin mapped onto the
ones they impersonate. Without that, a field name spelled with a Cyrillic first
letter reads as ``api_key`` to every human who reviews it and as nothing at all to
the rule. The fold is deliberately not a rejection of non-ASCII names: this
kernel's business payloads are Portuguese, and a boundary that refused an accented
field name would be removed rather than tuned.

Neither rule is complete, and completeness is not the claim. The claim is that
the boundary is closed by default in three specific ways: an unrecognised value
*type* is a finding rather than a pass, a document deeper than
:data:`MAX_SCAN_DEPTH` is a finding rather than a truncated scan, and a
non-string mapping key is a finding rather than a skipped one. A scan that could
not finish is never reported as a scan that found nothing.

Nothing is ever echoed
----------------------

A :class:`CredentialFinding` carries a **pointer and a rule name**. It never
carries the value it matched and never carries the matched substring, because the
offending value is precisely the thing that must not be written down -- the same
discipline ``error.schema.json`` states in its own description (*"Secrets,
credentials, raw endpoints and provider payloads MUST NOT appear in message or
details"*) and the same one the Bus applies to schema violations. The pointer
names *where*, never *what*.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

__all__ = [
    "CREDENTIAL_NAME_TOKENS",
    "MAX_SCAN_DEPTH",
    "RAW_SECRET_RULES",
    "CredentialBoundaryError",
    "CredentialFinding",
    "assert_no_credentials",
    "credential_findings",
    "is_credential_name",
    "raw_secret_rule",
    "redacted",
]

#: Deepest nesting the credential scan will walk.
#:
#: The same value and the same reasoning as
#: :data:`ginkgo_runtime.bus.admission.MAX_DOCUMENT_DEPTH`: ``payload`` is
#: unbounded business data, and a boundary that recursed into it without a limit
#: could be taken down with a deeply nested document. Fail-closed: a deeper
#: document is a **finding**, never a scan silently cut short, because a
#: truncated scan is a credential check that did not finish.
MAX_SCAN_DEPTH = 64

#: Field-name tokens that carry, or name, credential material.
#:
#: Transcribed from the credential-bearing entries of the frozen envelope's
#: ``x-ginkgo-forbidden-fields`` (``credential``, ``credential_value``,
#: ``secret``, ``api_key``, ``access_token``) and widened to the aliases the same
#: material is routinely stored under. `ai-stack#39` makes the general argument
#: for widening in the sibling case: *"an exact-name check passes
#: ``provider_hint``, ``endpoint_url`` and ``commands``"*. An exact-name check
#: here passes ``apiKey``, ``x-api-key``, ``api_key_prod`` and ``httpHeaderAuth``
#: for the same reason, so this list is matched per **token**, not per name.
#:
#: ``tests/test_rk08b_credential_boundary.py`` asserts that every
#: credential-bearing field the *frozen contract* names is caught by this rule.
#: That test reads the contract, not this constant -- a test that iterated this
#: tuple would prove only that the tuple equals itself.
CREDENTIAL_NAME_TOKENS: frozenset[str] = frozenset(
    {
        "accesskey",
        "accesstoken",
        "apikey",
        "apisecret",
        "apitoken",
        "auth",
        "authorization",
        "authtoken",
        "bearer",
        "clientsecret",
        "cookie",
        "cred",
        "credential",
        "credentials",
        "htpasswd",
        "idtoken",
        "jwt",
        "keypair",
        "keystore",
        "oauth",
        "otp",
        "passphrase",
        "passwd",
        "password",
        "pem",
        "pfx",
        "pkcs12",
        "privatekey",
        "pwd",
        "refreshtoken",
        "sas",
        "secret",
        "secretkey",
        "secrets",
        "sessionkey",
        "sessiontoken",
        "signature",
        "signingkey",
        "ssh",
        "token",
        "tokens",
        "truststore",
    }
)

#: Word edges, written as lookarounds rather than ``\b``.
#:
#: ``\b`` treats ``_`` as a word character, so ``SHOPEE_API_KEY=...`` has no
#: boundary before ``API`` and ``api_key_AKIA...`` has none before ``AKIA``.
#: Underscore-joined names are exactly where credentials are written down --
#: environment variables, form fields, column names -- so the edges here break on
#: an underscore too.
_LEFT_EDGE = r"(?<![0-9A-Za-z])"
_RIGHT_EDGE = r"(?![0-9A-Za-z])"

#: Published shapes of well-known credential material, each with the name it is
#: reported under. The name is what a finding carries; the match never is.
#:
#: Every rule is anchored on a documented, vendor-assigned prefix or an explicit
#: keyword, so a match is evidence rather than a guess. There is no entropy rule
#: and no bare hex or base64 rule: this kernel's own documents carry SHA-256
#: fingerprints, decision hashes and idempotency keys, and a boundary that
#: rejected those would be disabled rather than fixed.
RAW_SECRET_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "aws-access-key-id",
        re.compile(_LEFT_EDGE + r"(?:A3T[A-Z0-9]|ABIA|ACCA|AKIA|ASIA)[0-9A-Z]{16}" + _RIGHT_EDGE),
    ),
    ("google-api-key", re.compile(_LEFT_EDGE + r"AIza[0-9A-Za-z_-]{35}" + _RIGHT_EDGE)),
    ("github-token", re.compile(_LEFT_EDGE + r"gh[pousr]_[0-9A-Za-z]{20,}" + _RIGHT_EDGE)),
    ("slack-token", re.compile(_LEFT_EDGE + r"xox[abprs]-[0-9A-Za-z-]{10,}" + _RIGHT_EDGE)),
    (
        "stripe-key",
        re.compile(_LEFT_EDGE + r"[sprk]k_(?:live|test)_[0-9A-Za-z]{10,}" + _RIGHT_EDGE),
    ),
    ("openai-style-key", re.compile(_LEFT_EDGE + r"sk-(?:proj-)?[0-9A-Za-z_-]{16,}")),
    (
        "json-web-token",
        re.compile(
            _LEFT_EDGE + r"eyJ[0-9A-Za-z_-]{8,}\.eyJ[0-9A-Za-z_-]{8,}\.[0-9A-Za-z_-]{8,}"
        ),
    ),
    ("pem-private-key", re.compile(r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----")),
    # A long, unbroken token behind an auth scheme. The length floor is what
    # keeps ordinary prose -- "basic authentication", "bearer of the record" --
    # out of it: twenty unbroken credential-alphabet characters is material.
    (
        "authorization-scheme",
        re.compile(r"(?i)" + _LEFT_EDGE + r"(?:bearer|basic)\s+[0-9A-Za-z._~+/=-]{20,}"),
    ),
    # An assignment whose left-hand side names a credential. Catches material
    # embedded inside a larger string, where no field-name rule can see it.
    (
        "inline-credential-assignment",
        re.compile(
            r"(?i)" + _LEFT_EDGE + r"(?:password|passwd|pwd|secret|api[_-]?key"
            r"|access[_-]?token|refresh[_-]?token|client[_-]?secret|authorization"
            r"|account[_-]?key|shared[_-]?access[_-]?(?:key|signature))"
            r"\s*[:=]\s*\S{8,}"
        ),
    ),
    # Credentials in a URL's userinfo component.
    (
        "url-embedded-credential",
        re.compile(_LEFT_EDGE + r"[a-z][0-9a-z+.-]*://[^\s/@:]+:[^\s/@]+@"),
    ),
)

#: What a scanned document may be made of. Anything else cannot be shown to be
#: free of credential material, so it is a finding rather than a pass.
_SCALARS = (str, bool, int, float, type(None))

_SEPARATORS = re.compile(r"[^0-9A-Za-z]+")
_CASE_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_DIGIT_BOUNDARY = re.compile(r"(?<=[A-Za-z])(?=[0-9])")

#: Non-Latin letters that render identically to a Latin one, folded before a
#: field name is tokenised.
#:
#: Without this, a field name whose first character is CYRILLIC SMALL LETTER A
#: instead of the Latin one splits on the character the tokeniser does not
#: recognise as a letter and reduces to ``('pi', 'key')``, which matches nothing.
#: The field looks exactly like ``api_key`` to every human who reviews it and
#: like nothing at all to the rule.
#:
#: A fold, not a rejection: this kernel's business payloads are Portuguese, and a
#: boundary that refused an accented field name would be removed rather than
#: tuned. Compatibility decomposition strips the diacritics that make an accented
#: Latin letter accented; this table handles the letters that were never Latin.
#:
#: Written as code points rather than as the characters themselves. Pasting the
#: confusables into a file every reviewer reads would reproduce, in the source,
#: exactly the ambiguity the table exists to remove -- and the linter says so.
_CONFUSABLE_LETTERS = str.maketrans(
    {
        # Cyrillic
        0x0430: "a", 0x0432: "b", 0x0441: "c", 0x0501: "d", 0x0435: "e",
        0x0455: "s", 0x04BB: "h", 0x0456: "i", 0x0458: "j", 0x043A: "k",
        0x04CF: "l", 0x043C: "m", 0x043D: "h", 0x043E: "o", 0x0440: "p",
        0x051B: "q", 0x0442: "t", 0x0443: "y", 0x0445: "x", 0x051D: "w",
        # Greek
        0x03B1: "a", 0x03B2: "b", 0x03B5: "e", 0x03B9: "i", 0x03BA: "k",
        0x03BC: "m", 0x03BD: "v", 0x03BF: "o", 0x03C1: "p", 0x03C4: "t",
        0x03C5: "u", 0x03C7: "x", 0x03F2: "c",
    }
)


class CredentialBoundaryError(RuntimeError):
    """Credential material was found on an executor boundary.

    Carries pointers and rule names. It never carries the offending value, so it
    is safe to log, safe to re-raise and safe for RK-01 to observe -- though RK-01
    reports only the exception's type in any case.
    """

    def __init__(self, surface: str, findings: tuple[CredentialFinding, ...]) -> None:
        self.surface = surface
        self.findings = findings
        super().__init__(
            "the credential boundary refused %s: %d finding(s) -- %s"
            % (
                surface,
                len(findings),
                ", ".join("%s [%s]" % (f.pointer, f.rule) for f in findings[:6]),
            )
        )


@dataclass(frozen=True)
class CredentialFinding:
    """Where a credential was found, and which rule found it. Never what it was."""

    #: A JSON-pointer-ish path built from key names and list indices.
    pointer: str
    #: The rule that fired: a name from :data:`RAW_SECRET_RULES`,
    #: ``credential-field-name``, ``unscannable-type``, ``unscannable-key`` or
    #: ``document-too-deep``.
    rule: str


def _folded(key: str) -> str:
    """A field name reduced to plain lowercase Latin before it is tokenised.

    Compatibility decomposition first, so a fullwidth spelling of ``API_KEY`` and
    an accented Portuguese field name both become ordinary ASCII words; combining
    marks are then dropped, and the letters that merely *look* Latin are folded by
    :data:`_CONFUSABLE_LETTERS`.
    """
    decomposed = unicodedata.normalize("NFKD", key)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return stripped.casefold().translate(_CONFUSABLE_LETTERS)


def _tokens(key: str) -> tuple[str, ...]:
    """Split a field name into lowercase tokens.

    ``x-api-key`` -> ``('x', 'api', 'key')``; ``httpHeaderAuth`` ->
    ``('http', 'header', 'auth')``; ``api_key_2`` -> ``('api', 'key', '2')``.
    """
    parts: list[str] = []
    # Case boundaries come off the RAW key: folding lowercases it, and a
    # lowercased `apiKey` has no boundary left to split on.
    for piece in _CASE_BOUNDARY.split(key):
        for chunk in _SEPARATORS.split(_folded(piece)):
            if not chunk:
                continue
            for atom in _DIGIT_BOUNDARY.split(chunk):
                if atom:
                    parts.append(atom)
    return tuple(parts)


def is_credential_name(key: str) -> bool:
    """Whether a field name names credential material.

    Per token, and per adjacent pair joined -- so ``api_key`` and ``apiKey`` both
    reduce to ``apikey`` while ``author`` stays a word and ``secretary`` stays a
    job.
    """
    tokens = _tokens(key)
    if any(token in CREDENTIAL_NAME_TOKENS for token in tokens):
        return True
    return any(
        first + second in CREDENTIAL_NAME_TOKENS
        for first, second in pairwise(tokens)
    )


def redacted(text: str) -> str:
    """``text`` with every stretch of recognised credential material cut out.

    Used to build pointers. A field name can be worth reporting and carry a
    credential at the same time -- ``api_key_AKIA...`` is both -- and a boundary
    that named the field by quoting it would publish the very thing it refused.
    """
    for _, pattern in RAW_SECRET_RULES:
        text = pattern.sub("<redacted>", text)
    return text


def raw_secret_rule(value: str) -> str | None:
    """The name of the first rule that matches ``value``, or ``None``.

    The match itself is discarded on purpose. The caller gets the rule's name and
    a pointer; it never gets the material.
    """
    for name, pattern in RAW_SECRET_RULES:
        if pattern.search(value):
            return name
    return None


def credential_findings(
    document: Any,
    *,
    reference_fields: tuple[str, ...] = (),
) -> list[CredentialFinding]:
    """Every credential finding in ``document``, walked iteratively.

    ``reference_fields`` exempts contract-declared *reference* fields from the
    **name** rule only -- ``credential_refs`` is required by the frozen envelope
    to exist and is required to hold identifiers. Its contents are still scanned
    for raw material, because a value there is exactly the contract violation the
    field's own description warns about.

    Iterative rather than recursive: a boundary that overflowed the stack while
    checking a hostile document would fail *open*, which is the one outcome the
    scan exists to prevent.
    """
    exempt = {name.lower() for name in reference_fields}
    findings: list[CredentialFinding] = []
    stack: list[tuple[Any, str, int]] = [(document, "", 0)]

    while stack:
        node, pointer, depth = stack.pop()
        if depth > MAX_SCAN_DEPTH:
            findings.append(CredentialFinding(pointer or "/", "document-too-deep"))
            continue

        if isinstance(node, Mapping):
            for key, value in node.items():
                if not isinstance(key, str):
                    findings.append(
                        CredentialFinding("%s/<%s>" % (pointer, type(key).__name__),
                                          "unscannable-key")
                    )
                    continue
                # A key is a string too, and indexing something by the credential
                # it belongs to is a perfectly ordinary shape. Scanning only
                # values would walk straight past it.
                key_rule = raw_secret_rule(key)
                # The pointer is written down, so it is built from a key with any
                # material cut out of it -- for this finding and for every finding
                # underneath it. `api_key_AKIA...` names a field worth reporting
                # and carries a credential worth never reporting; the pointer gets
                # the first half.
                here = "%s/%s" % (pointer, redacted(key))
                if key.lower() not in exempt and is_credential_name(key):
                    findings.append(CredentialFinding(here, "credential-field-name"))
                if key_rule is not None:
                    findings.append(CredentialFinding(here, key_rule))
                stack.append((value, here, depth + 1))
        elif isinstance(node, list | tuple):
            for index, item in enumerate(node):
                stack.append((item, "%s/%d" % (pointer, index), depth + 1))
        elif isinstance(node, str):
            rule = raw_secret_rule(node)
            if rule is not None:
                findings.append(CredentialFinding(pointer or "/", rule))
        elif not isinstance(node, _SCALARS):
            # Not a mapping, not a sequence, not a JSON scalar. It cannot be
            # shown to be free of credential material, so it is not waved past.
            findings.append(
                CredentialFinding(pointer or "/", "unscannable-type")
            )

    return sorted(findings, key=lambda f: (f.pointer, f.rule))


def assert_no_credentials(
    document: Any,
    *,
    surface: str,
    reference_fields: tuple[str, ...] = (),
) -> None:
    """Raise :class:`CredentialBoundaryError` if ``document`` carries a credential.

    ``surface`` names the boundary in the error -- the caller's own words for
    where the document was going, never anything read out of the document.
    """
    findings = credential_findings(document, reference_fields=reference_fields)
    if findings:
        raise CredentialBoundaryError(surface, tuple(findings))
