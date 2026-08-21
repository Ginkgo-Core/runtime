"""The canonical JSON and hashing primitive.

This is the one place in the kernel that turns a value into bytes, so the
catalog's determinism claim reduces to this module behaving. The reuse is
conceptual, from ``ginkgo-connect/packages/core/src/canonical.ts`` under BLK-17's
``KEEP_SEPARATE`` resolution: same idea, no dependency.
"""

from __future__ import annotations

import pytest

from ginkgo_runtime.canonical import (
    CanonicalisationError,
    canonical_json,
    fingerprint,
    hash_canonical,
    sha256_hex,
)


def test_key_order_does_not_change_the_encoding():
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})
    assert canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'


def test_nested_key_order_does_not_change_the_encoding():
    left = {"outer": {"z": [{"b": 1, "a": 2}]}}
    right = {"outer": {"z": [{"a": 2, "b": 1}]}}
    assert hash_canonical(left) == hash_canonical(right)


def test_encoding_carries_no_insignificant_whitespace():
    assert canonical_json({"a": [1, 2]}) == '{"a":[1,2]}'


def test_non_ascii_is_escaped_so_the_bytes_are_platform_independent():
    encoded = canonical_json({"purpose": "gerar imagem local reproduzível"})
    assert encoded.isascii()
    assert "\\u00ed" in encoded


def test_lists_and_tuples_encode_identically():
    assert canonical_json({"a": [1, 2]}) == canonical_json({"a": (1, 2)})


def test_a_float_is_refused_rather_than_rounded():
    """IEEE-754 repr is the classic cross-platform hash drift. It never reaches the hash."""
    with pytest.raises(CanonicalisationError) as excinfo:
        canonical_json({"minimum_multiplier": 5.0})
    assert "minimum_multiplier" in str(excinfo.value)


def test_a_nested_float_is_refused_with_its_path():
    with pytest.raises(CanonicalisationError) as excinfo:
        canonical_json({"gates": [{"threshold": 0.6}]})
    assert "$.gates[0].threshold" in str(excinfo.value)


def test_a_non_string_key_is_refused():
    with pytest.raises(CanonicalisationError):
        canonical_json({1: "one"})


def test_an_unsupported_type_is_refused():
    with pytest.raises(CanonicalisationError) as excinfo:
        canonical_json({"when": object()})
    assert "unsupported type" in str(excinfo.value)


def test_booleans_are_not_treated_as_integers():
    assert canonical_json({"a": True, "b": 1}) == '{"a":true,"b":1}'


def test_hash_is_sha256_over_the_utf8_encoding():
    value = {"a": 1}
    assert hash_canonical(value) == sha256_hex(canonical_json(value).encode("utf-8"))
    assert len(hash_canonical(value)) == 64


def test_hash_is_stable_for_equal_values():
    assert hash_canonical({"a": [1, {"b": None}]}) == hash_canonical({"a": [1, {"b": None}]})


def test_hash_differs_for_different_values():
    assert hash_canonical({"a": 1}) != hash_canonical({"a": 2})


# ---------------------------------------------------------------------------
# fingerprint -- the encoding for caller data
# ---------------------------------------------------------------------------


def test_a_float_is_fingerprintable_even_though_it_is_not_canonicalisable():
    """Found by adversarial review: a legal request crashed the boundary.

    ``CapabilityRequest.input`` is ``additionalProperties: true`` because business
    payload has to be, so ``{"price": 19.99}`` is an ordinary request. The
    catalog's stricter primitive refuses floats -- correctly, for a projection --
    and using it on caller data let any caller take the Bus down.
    """
    with pytest.raises(CanonicalisationError):
        canonical_json({"price": 19.99})
    assert len(fingerprint({"price": 19.99})) == 64


def test_the_fingerprint_is_stable():
    assert fingerprint({"a": 1, "b": [1, 2]}) == fingerprint({"b": [1, 2], "a": 1})


@pytest.mark.parametrize(
    ("left", "right"),
    [
        (1.0, "1.0"),
        (1, 1.0),
        (1, "1"),
        (True, 1),
        (None, "None"),
        (None, ""),
        ([1], "1"),
    ],
)
def test_the_fingerprint_never_collides_across_types(left, right):
    """Type tagging, and why it is not optional.

    A bare-``repr`` encoding would make the float ``1.0`` and the string ``"1.0"``
    hash the same. A collision here means two different payloads share an
    idempotency fingerprint -- a retry with a *different* payload silently
    treated as the same one, which is a fail-open in the one mechanism that
    exists to prevent double execution.
    """
    assert fingerprint({"v": left}) != fingerprint({"v": right})


def test_the_fingerprint_distinguishes_nesting():
    assert fingerprint({"a": {"b": 1}}) != fingerprint({"a": [["b", 1]]})


def test_a_non_finite_number_is_refused():
    with pytest.raises(CanonicalisationError):
        fingerprint({"v": float("nan")})
    with pytest.raises(CanonicalisationError):
        fingerprint({"v": float("inf")})


def test_a_non_json_value_is_refused():
    with pytest.raises(CanonicalisationError):
        fingerprint({"v": object()})
