"""The strict validator, and the frozen contracts it validates against.

The validator is the gate every caller passes through, so it gets its own tests
rather than being tested only through the Bus. The property that makes writing
one defensible at all -- *it refuses what it does not implement* -- is asserted
first, because everything else rests on it.
"""

from __future__ import annotations

import json

import pytest

from ginkgo_runtime.catalog.manifest import load_manifest
from ginkgo_runtime.contracts import (
    CAPABILITY_REQUEST,
    CAPABILITY_RESULT,
    ERROR,
    RUNTIME_EXECUTION_RESULT,
    ContractsUnavailableError,
    UnsupportedSchemaError,
    Validator,
    load_frozen_contracts,
    schema_keywords,
)
from ginkgo_runtime.contracts.jsonschema import ANNOTATION_KEYWORDS, SUPPORTED_KEYWORDS

from .conftest import REPO_ROOT, capability_request

FROZEN = (CAPABILITY_REQUEST, CAPABILITY_RESULT, ERROR, RUNTIME_EXECUTION_RESULT)


# -- the property the whole approach rests on ------------------------------


def test_an_unimplemented_keyword_raises_rather_than_being_ignored():
    """The failure mode of a hand-written validator, removed by construction.

    Silently skipping an unknown keyword is an assertion never made and a
    document that passes without being checked. This refuses instead, so the
    worst case is a validator that will not run.
    """
    with pytest.raises(UnsupportedSchemaError) as excinfo:
        Validator({"type": "object", "dependentRequired": {"a": ["b"]}})
    assert "dependentRequired" in str(excinfo.value)


def test_the_refusal_names_every_unimplemented_keyword():
    with pytest.raises(UnsupportedSchemaError) as excinfo:
        Validator({"type": "object", "propertyNames": {}, "patternProperties": {}})
    message = str(excinfo.value)
    assert "patternProperties" in message and "propertyNames" in message


@pytest.mark.parametrize("path", FROZEN)
def test_every_frozen_contract_is_fully_implemented(path):
    """No frozen contract uses a keyword this validator skips.

    If ``ai-stack`` adds one, this fails loudly here rather than quietly
    weakening the boundary.
    """
    schema = json.loads(load_manifest(REPO_ROOT).read(path).decode("utf-8"))
    known = SUPPORTED_KEYWORDS | ANNOTATION_KEYWORDS
    unknown = sorted(
        k for k in schema_keywords(schema) if k not in known and not k.startswith("x-")
    )
    assert unknown == []


def test_extension_keywords_are_not_assertions():
    """``x-ginkgo-*`` is metadata. Ignoring it cannot weaken anything."""
    validator = Validator({"type": "object", "x-ginkgo-authority": "internal-runtime"})
    assert validator.is_valid({})


# -- keyword behaviour ------------------------------------------------------


def test_additional_properties_false_rejects_an_unknown_property():
    validator = Validator(
        {"type": "object", "additionalProperties": False, "properties": {"a": {}}}
    )
    assert validator.is_valid({"a": 1})
    assert not validator.is_valid({"a": 1, "b": 2})


def test_a_boolean_is_not_an_integer():
    """``True == 1`` in Python and not in JSON. The distinction is load-bearing.

    ``evidence.required`` is a boolean and ``cost_events[].amount`` is a number.
    A validator that treated them as interchangeable would accept
    ``required: 1`` and ``amount: true``.
    """
    assert not Validator({"type": "integer"}).is_valid(True)
    assert not Validator({"type": "boolean"}).is_valid(1)
    assert Validator({"type": "integer"}).is_valid(1)
    assert Validator({"type": "boolean"}).is_valid(True)


def test_enum_membership_respects_the_same_distinction():
    assert not Validator({"enum": [1, 2]}).is_valid(True)


def test_one_of_requires_exactly_one_match():
    schema = {"oneOf": [{"type": "null"}, {"type": "object", "required": ["amount"]}]}
    validator = Validator(schema)
    assert validator.is_valid(None)
    assert validator.is_valid({"amount": 1})
    assert not validator.is_valid(5)


def test_if_then_applies_the_branch():
    schema = {
        "if": {"properties": {"state": {"const": "failed"}}, "required": ["state"]},
        "then": {"required": ["error"]},
    }
    validator = Validator(schema)
    assert validator.is_valid({"state": "succeeded"})
    assert not validator.is_valid({"state": "failed"})
    assert validator.is_valid({"state": "failed", "error": {}})


def test_unique_items_is_asserted():
    validator = Validator({"type": "array", "uniqueItems": True})
    assert validator.is_valid(["a", "b"])
    assert not validator.is_valid(["a", "a"])


def test_pattern_and_length_are_asserted():
    validator = Validator({"type": "string", "pattern": "^v[0-9]+$", "maxLength": 3})
    assert validator.is_valid("v1")
    assert not validator.is_valid("1.0")
    assert not validator.is_valid("v1234")


def test_date_time_format_is_asserted_not_annotated():
    """Deliberately stricter than the JSON Schema default. See the module docstring."""
    validator = Validator({"type": "string", "format": "date-time"})
    assert validator.is_valid("2026-08-21T12:00:00Z")
    assert validator.is_valid("2026-08-21T12:00:00+00:00")
    assert not validator.is_valid("tomorrow")
    assert not validator.is_valid("2026-13-45T99:00:00Z")


def test_an_unimplemented_format_fails_closed():
    """Asserting formats means an unknown one cannot be waved through."""
    with pytest.raises(UnsupportedSchemaError):
        Validator({"type": "string", "format": "uri"}).is_valid("x")


def test_violations_are_ordered_deterministically():
    validator = Validator(
        {"type": "object", "additionalProperties": False, "required": ["a", "b", "c"]}
    )
    first = [v.as_dict() for v in validator.violations({"z": 1, "y": 2})]
    second = [v.as_dict() for v in validator.violations({"y": 2, "z": 1})]
    assert first == second


def test_a_violation_locator_omits_the_value():
    """The property N15 depends on."""
    validator = Validator({"type": "object", "properties": {"k": {"enum": ["ok"]}}})
    violation = validator.violations({"k": "sk-secret-value"})[0]
    assert "sk-secret-value" in violation.message
    assert "sk-secret-value" not in violation.locator
    assert violation.locator == "/k (enum)"


def test_a_ref_without_a_resolver_is_refused():
    with pytest.raises(UnsupportedSchemaError):
        Validator({"$ref": "./error.schema.json"}).is_valid({})


# -- loading the frozen set -------------------------------------------------


def test_the_frozen_contracts_load_from_the_pinned_snapshot(contracts):
    assert contracts.capability_request.is_valid(capability_request())
    assert contracts.supported_contract_version == "v1"


def test_a_relative_parent_ref_resolves(contracts):
    """``runtime-execution-result.schema.json`` refs ``../core/error.schema.json``.

    Worth its own test: the first implementation used ``str.lstrip("./")``, which
    strips *characters* rather than a prefix and turned that ref into
    ``core/error.schema.json`` -- matching no basename and failing every
    validation of a failed execution.
    """
    execution = {
        "schema_version": "ginkgo.runtime-execution-result.v1",
        "execution_id": "e",
        "request_id": "r",
        "correlation_id": "c",
        "idempotency_key": "i",
        "decision_ref": {"decision_id": "d", "decision_hash": "a" * 64},
        "state": "failed",
        "terminal": True,
        "telemetry": {
            "started_at": "2026-08-21T12:00:00Z",
            "ended_at": "2026-08-21T12:00:01Z",
            "duration_ms": 1,
            "attempts": 1,
        },
        "error": {"code": "X", "message": "m", "category": "PROVIDER", "retryable": False},
    }
    assert contracts.runtime_execution_result.is_valid(execution)

    execution["error"] = {"code": "lowercase-is-invalid", "message": "m", "category": "NOPE"}
    assert not contracts.runtime_execution_result.is_valid(execution)


def test_a_ref_outside_the_pinned_set_is_refused(contracts):
    with pytest.raises(ContractsUnavailableError):
        contracts.capability_result.resolve_ref("./something-else.schema.json")


def test_a_missing_contract_fails_closed():
    def reader(path):
        if path == ERROR:
            raise OSError("gone")
        return load_manifest(REPO_ROOT).read(path)

    with pytest.raises(ContractsUnavailableError) as excinfo:
        load_frozen_contracts(reader)
    assert ERROR in str(excinfo.value)


def test_a_missing_status_mapping_fails_closed():
    """RK-01 will not invent the executor-state to result-status mapping."""
    manifest = load_manifest(REPO_ROOT)

    def reader(path):
        raw = manifest.read(path)
        if path != RUNTIME_EXECUTION_RESULT:
            return raw
        schema = json.loads(raw.decode("utf-8"))
        del schema["x-ginkgo-status-mapping"]
        return json.dumps(schema).encode("utf-8")

    with pytest.raises(ContractsUnavailableError) as excinfo:
        load_frozen_contracts(reader)
    assert "x-ginkgo-status-mapping" in str(excinfo.value)


def test_a_missing_capability_pattern_fails_closed():
    manifest = load_manifest(REPO_ROOT)

    def reader(path):
        raw = manifest.read(path)
        if path != CAPABILITY_REQUEST:
            return raw
        schema = json.loads(raw.decode("utf-8"))
        del schema["properties"]["capability"]["properties"]["name"]["pattern"]
        return json.dumps(schema).encode("utf-8")

    with pytest.raises(ContractsUnavailableError) as excinfo:
        load_frozen_contracts(reader)
    assert "refuses to substitute a literal" in str(excinfo.value)


def test_the_contracts_reach_the_bus_through_the_pinned_manifest():
    """No unpinned file, no network. The same guarantee the catalog has."""
    from ginkgo_runtime.bus import CapabilityBus, DeterministicDownstream

    bus = CapabilityBus.from_pinned_contracts(DeterministicDownstream())
    assert bus.contracts.supported_contract_version == "v1"
