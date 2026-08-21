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
