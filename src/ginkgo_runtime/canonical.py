"""Canonical JSON serialisation and content hashing.

The kernel needs one — and only one — way to turn a value into bytes so that two
runs that mean the same thing hash the same. The primitive is deliberately the
same *idea* proven by ``ginkgo-connect/packages/core/src/canonical.ts``
(`BLK-17`, resolution ``KEEP_SEPARATE``): sorted keys, no insignificant
whitespace, SHA-256 over UTF-8. It is a conceptual reuse. Nothing is imported
from ``ginkgo-connect``, and none of its routing model — ``rankedFallbacks``,
``chosenExecutorRef``, ``FrozenDecision`` — is reproduced here.

Two rules make the output reproducible rather than merely tidy:

* **Floats are refused.** IEEE-754 repr is the classic source of cross-platform
  hash drift. The catalog projects strings, booleans, integers and null; a float
  reaching this function is a projection defect and is raised, not rounded.
* **Non-finite and non-JSON values are refused** for the same reason.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

__all__ = ["CanonicalisationError", "canonical_json", "hash_canonical", "sha256_hex"]


class CanonicalisationError(TypeError):
    """A value cannot be canonicalised deterministically."""


def _check(value: Any, path: str) -> None:
    if value is None or isinstance(value, str | bool):
        return
    if isinstance(value, int):          # bool is a subclass of int; handled above
        return
    if isinstance(value, float):
        raise CanonicalisationError(
            "float at %s: floats are not canonicalisable; project a string instead" % path
        )
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalisationError("non-string key at %s: %r" % (path, key))
            _check(item, "%s.%s" % (path, key))
        return
    if isinstance(value, list | tuple):
        for index, item in enumerate(value):
            _check(item, "%s[%d]" % (path, index))
        return
    raise CanonicalisationError(
        "unsupported type %s at %s" % (type(value).__name__, path)
    )


def canonical_json(value: Any) -> str:
    """Serialise ``value`` to canonical JSON: sorted keys, no spare whitespace, ASCII-escaped."""
    _check(value, "$")
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_canonical(value: Any) -> str:
    """SHA-256 of the canonical JSON encoding of ``value``, as lowercase hex."""
    return sha256_hex(canonical_json(value).encode("utf-8"))


# ---------------------------------------------------------------------------
# Fingerprinting arbitrary caller data
# ---------------------------------------------------------------------------
#
# `canonical_json` refuses floats on purpose: in a catalog projection a float is
# a defect, and IEEE-754 repr is the classic source of cross-platform hash drift.
#
# Caller data is a different question. `CapabilityRequest.input` is
# `additionalProperties: true` because business payload has to be, and `19.99` is
# an ordinary price, not a defect. A boundary that crashed on it would let any
# caller take the Bus down with a legal request.
#
# So fingerprinting gets its own encoding: **type-tagged**, and total over every
# JSON value.
#
# The tagging is what makes it safe rather than merely tolerant. Encoding a float
# as its bare `repr` would let the float `1.0` and the string `"1.0"` collide, and
# a collision here means two different payloads share an idempotency fingerprint
# -- a retry with a different payload silently treated as the same one. Tagging
# every scalar with its JSON type makes that impossible rather than unlikely.
#
# `repr` is used for the float itself: CPython emits the shortest string that
# round-trips, deterministically, for any IEEE-754 double.

_TAGS = {type(None): "z", bool: "b", int: "i", float: "f", str: "s"}


def _tagged(value: Any) -> Any:
    if isinstance(value, dict):
        return ["o", sorted((str(k), _tagged(v)) for k, v in value.items())]
    if isinstance(value, list | tuple):
        return ["a", [_tagged(item) for item in value]]
    tag = _TAGS.get(type(value))
    if tag is None:
        raise CanonicalisationError(
            "cannot fingerprint %s; only JSON values are fingerprintable"
            % type(value).__name__
        )
    if tag == "f":
        if value != value or value in (float("inf"), float("-inf")):
            raise CanonicalisationError("cannot fingerprint a non-finite number")
        return [tag, repr(value)]
    if tag == "z":
        return [tag]
    return [tag, value if tag != "i" else str(value)]


def fingerprint(value: Any) -> str:
    """SHA-256 over a type-tagged canonical encoding. Total over JSON values.

    Use this for caller-supplied data. Use :func:`hash_canonical` for values the
    kernel itself constructed, where a float means something went wrong.
    """
    return sha256_hex(
        json.dumps(_tagged(value), separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    )
