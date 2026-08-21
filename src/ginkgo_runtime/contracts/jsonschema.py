"""A strict, fail-closed JSON Schema validator for the frozen contracts.

The kernel validates a caller's request against ``contracts/core/capability-request.schema.json``
itself -- not against a hand-written restatement of it. That is the whole point:
the schema is the authority, and a second expression of the same rules in Python
is a duplicate that will drift.

**Why not a third-party validator.** ADR-RT-001 fixes the stack at stdlib plus
PyYAML, and the ecosystem's other Python -- ``ai-stack/tools/conformance`` --
is stdlib-only with a CI step that blocks ``jsonschema`` from importing. This
module keeps that property.

**Why that is safe here, when a hand-written YAML parser was not.** The danger of
writing your own is *under-enforcement*: a keyword you did not implement is
silently ignored, and an invalid document passes. That failure mode is removed by
construction --

    every keyword encountered that this module does not implement raises
    :class:`UnsupportedSchemaError`. Nothing is skipped.

So the worst case is a build that refuses to validate, never a validation that
silently permits. :func:`schema_keywords` exposes the walk so a test can assert
the frozen contracts use nothing unimplemented.

**Enforced beyond the JSON Schema default.** ``format: date-time`` is an
annotation in the specification and most validators ignore it. This one enforces
it: ``deadline`` reaches deadline arithmetic downstream, and ``"tomorrow"`` is not
a deadline. The choice is deliberate and tested.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

__all__ = [
    "ANNOTATION_KEYWORDS",
    "SUPPORTED_KEYWORDS",
    "SchemaViolation",
    "UnsupportedSchemaError",
    "Validator",
    "schema_keywords",
]


class UnsupportedSchemaError(Exception):
    """The schema uses a keyword this validator does not implement.

    Raised, never ignored. A validator that skipped what it did not understand
    would report conformance it never checked.
    """


#: Keywords that carry no assertion. Ignoring these cannot weaken validation.
ANNOTATION_KEYWORDS = frozenset(
    {
        "$schema",
        "$id",
        "$comment",
        "title",
        "description",
        "default",
        "examples",
        "deprecated",
        "readOnly",
        "writeOnly",
    }
)

#: Keywords this validator asserts on.
SUPPORTED_KEYWORDS = frozenset(
    {
        "$ref",
        "additionalProperties",
        "allOf",
        "anyOf",
        "const",
        "else",
        "enum",
        "exclusiveMaximum",
        "exclusiveMinimum",
        "format",
        "if",
        "items",
        "maxItems",
        "maxLength",
        "maximum",
        "minItems",
        "minLength",
        "minimum",
        "not",
        "oneOf",
        "pattern",
        "properties",
        "required",
        "then",
        "type",
        "uniqueItems",
    }
)

_TYPES: dict[str, Any] = {
    "object": dict,
    "array": list,
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "null": type(None),
}


def _is_type(value: Any, name: str) -> bool:
    if name == "boolean":
        return isinstance(value, bool)
    if name in ("number", "integer"):
        # `True` is an int in Python. It is not a number in JSON.
        if isinstance(value, bool):
            return False
        return isinstance(value, _TYPES[name])
    expected = _TYPES.get(name)
    if expected is None:
        raise UnsupportedSchemaError("unknown type %r" % name)
    return isinstance(value, expected)


@dataclass(frozen=True)
class SchemaViolation:
    """One assertion that failed, located by JSON pointer."""

    pointer: str
    keyword: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"keyword": self.keyword, "message": self.message, "pointer": self.pointer}

    @property
    def locator(self) -> str:
        """Where and which rule -- and deliberately NOT what the value was.

        ``message`` quotes the offending value, which is useful in a test and
        forbidden in anything the caller sees: ``error.schema.json`` says secrets
        and credentials must not appear in ``message`` or ``details``, and the
        rejected value is exactly the thing that might be one. Anything crossing
        the boundary renders through here.
        """
        return "%s (%s)" % (self.pointer or "/", self.keyword)

    def __str__(self) -> str:
        return "%s: %s (%s)" % (self.pointer or "/", self.message, self.keyword)


def schema_keywords(schema: Any) -> set[str]:
    """Every keyword appearing in ``schema``, for the "nothing unimplemented" test.

    Descends only where keywords live. It does not descend into ``properties``
    *names*, ``enum`` members or ``x-`` extensions, whose keys are data.
    """
    found: set[str] = set()
    if isinstance(schema, list):
        for item in schema:
            found |= schema_keywords(item)
        return found
    if not isinstance(schema, dict):
        return found

    for key, value in schema.items():
        if key.startswith("x-"):
            continue
        found.add(key)
        if key == "properties":
            for sub in value.values():
                found |= schema_keywords(sub)
        elif key in ("enum", "const", "required", "type", "format", "pattern"):
            continue
        else:
            found |= schema_keywords(value)
    return found


@dataclass
class Validator:
    """Validates a document against one frozen schema.

    ``resolve_ref`` loads a sibling schema for a relative ``$ref`` such as
    ``./error.schema.json``. It is supplied by the caller so refs resolve through
    the same pinned, integrity-checked reader as everything else -- never off the
    filesystem or the network.
    """

    schema: dict[str, Any]
    resolve_ref: Any = None
    _compiled: dict[str, re.Pattern[str]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self.assert_supported()

    # -- keyword support -------------------------------------------------

    def assert_supported(self) -> None:
        known = SUPPORTED_KEYWORDS | ANNOTATION_KEYWORDS
        unknown = sorted(
            k
            for k in schema_keywords(self.schema)
            if k not in known and not k.startswith("x-")
        )
        if unknown:
            raise UnsupportedSchemaError(
                "schema uses keywords this validator does not implement: %s. It "
                "refuses rather than ignoring them, because an ignored keyword is "
                "an assertion silently not made." % ", ".join(unknown)
            )

    # -- entry points ----------------------------------------------------

    def violations(self, document: Any) -> list[SchemaViolation]:
        out: list[SchemaViolation] = []
        self._check(document, self.schema, "", out)
        out.sort(key=lambda v: (v.pointer, v.keyword, v.message))
        return out

    def is_valid(self, document: Any) -> bool:
        return not self.violations(document)

    # -- the walk --------------------------------------------------------

    def _pattern(self, expression: str) -> re.Pattern[str]:
        compiled = self._compiled.get(expression)
        if compiled is None:
            compiled = re.compile(expression)
            self._compiled[expression] = compiled
        return compiled

    def _subschema_valid(self, value: Any, schema: Any) -> bool:
        probe: list[SchemaViolation] = []
        self._check(value, schema, "", probe)
        return not probe

    def _check(self, value: Any, schema: Any, pointer: str, out: list[SchemaViolation]) -> None:
        if schema is True or schema == {}:
            return
        if schema is False:
            out.append(SchemaViolation(pointer, "false", "no value is valid here"))
            return
        if not isinstance(schema, dict):
            raise UnsupportedSchemaError("schema fragment is not an object: %r" % schema)

        if "$ref" in schema:
            if self.resolve_ref is None:
                raise UnsupportedSchemaError(
                    "schema uses $ref %r but no resolver was supplied" % schema["$ref"]
                )
            self._check(value, self.resolve_ref(schema["$ref"]), pointer, out)
            # A frozen contract never mixes $ref with sibling assertions; if one
            # ever does, the remaining keywords are still walked below.

        for keyword, constraint in schema.items():
            if keyword in ANNOTATION_KEYWORDS or keyword.startswith("x-") or keyword == "$ref":
                continue
            handler = getattr(self, "_kw_" + keyword.replace("$", "d_"), None)
            if handler is None:
                raise UnsupportedSchemaError("unimplemented keyword %r" % keyword)
            handler(value, constraint, schema, pointer, out)

    # -- keyword handlers ------------------------------------------------

    def _kw_type(self, value, constraint, schema, pointer, out):
        names = [constraint] if isinstance(constraint, str) else list(constraint)
        if not any(_is_type(value, name) for name in names):
            out.append(
                SchemaViolation(
                    pointer, "type", "expected %s, found %s" % ("/".join(names), _name_of(value))
                )
            )

    def _kw_enum(self, value, constraint, schema, pointer, out):
        if not any(value == member and _same_kind(value, member) for member in constraint):
            out.append(
                SchemaViolation(pointer, "enum", "%r is not one of %r" % (value, constraint))
            )

    def _kw_const(self, value, constraint, schema, pointer, out):
        if not (value == constraint and _same_kind(value, constraint)):
            out.append(
                SchemaViolation(pointer, "const", "expected %r, found %r" % (constraint, value))
            )

    def _kw_required(self, value, constraint, schema, pointer, out):
        if not isinstance(value, dict):
            return
        for name in constraint:
            if name not in value:
                out.append(
                    SchemaViolation(
                        pointer, "required", "missing required property %r" % name
                    )
                )

    def _kw_properties(self, value, constraint, schema, pointer, out):
        if not isinstance(value, dict):
            return
        for name, subschema in constraint.items():
            if name in value:
                self._check(value[name], subschema, "%s/%s" % (pointer, name), out)

    def _kw_additionalProperties(self, value, constraint, schema, pointer, out):
        if not isinstance(value, dict):
            return
        declared = set(schema.get("properties", {}))
        for name in sorted(set(value) - declared):
            if constraint is False:
                out.append(
                    SchemaViolation(
                        "%s/%s" % (pointer, name),
                        "additionalProperties",
                        "property %r is not permitted by the contract" % name,
                    )
                )
            else:
                self._check(value[name], constraint, "%s/%s" % (pointer, name), out)

    def _kw_items(self, value, constraint, schema, pointer, out):
        if not isinstance(value, list):
            return
        for index, item in enumerate(value):
            self._check(item, constraint, "%s/%d" % (pointer, index), out)

    def _kw_uniqueItems(self, value, constraint, schema, pointer, out):
        if not constraint or not isinstance(value, list):
            return
        seen: list[Any] = []
        for item in value:
            if any(item == other and _same_kind(item, other) for other in seen):
                out.append(SchemaViolation(pointer, "uniqueItems", "duplicate item %r" % (item,)))
                return
            seen.append(item)

    def _kw_minItems(self, value, constraint, schema, pointer, out):
        if isinstance(value, list) and len(value) < constraint:
            out.append(SchemaViolation(pointer, "minItems", "fewer than %d items" % constraint))

    def _kw_maxItems(self, value, constraint, schema, pointer, out):
        if isinstance(value, list) and len(value) > constraint:
            out.append(SchemaViolation(pointer, "maxItems", "more than %d items" % constraint))

    def _kw_minLength(self, value, constraint, schema, pointer, out):
        if isinstance(value, str) and len(value) < constraint:
            out.append(
                SchemaViolation(pointer, "minLength", "shorter than %d characters" % constraint)
            )

    def _kw_maxLength(self, value, constraint, schema, pointer, out):
        if isinstance(value, str) and len(value) > constraint:
            out.append(
                SchemaViolation(pointer, "maxLength", "longer than %d characters" % constraint)
            )

    def _kw_pattern(self, value, constraint, schema, pointer, out):
        if isinstance(value, str) and self._pattern(constraint).search(value) is None:
            out.append(
                SchemaViolation(pointer, "pattern", "%r does not match %s" % (value, constraint))
            )

    def _kw_minimum(self, value, constraint, schema, pointer, out):
        if isinstance(value, int | float) and not isinstance(value, bool) and value < constraint:
            out.append(SchemaViolation(pointer, "minimum", "less than %r" % constraint))

    def _kw_maximum(self, value, constraint, schema, pointer, out):
        if isinstance(value, int | float) and not isinstance(value, bool) and value > constraint:
            out.append(SchemaViolation(pointer, "maximum", "greater than %r" % constraint))

    def _kw_exclusiveMinimum(self, value, constraint, schema, pointer, out):
        if isinstance(value, int | float) and not isinstance(value, bool) and value <= constraint:
            out.append(
                SchemaViolation(pointer, "exclusiveMinimum", "not greater than %r" % constraint)
            )

    def _kw_exclusiveMaximum(self, value, constraint, schema, pointer, out):
        if isinstance(value, int | float) and not isinstance(value, bool) and value >= constraint:
            out.append(
                SchemaViolation(pointer, "exclusiveMaximum", "not less than %r" % constraint)
            )

    def _kw_format(self, value, constraint, schema, pointer, out):
        if not isinstance(value, str):
            return
        if constraint != "date-time":
            raise UnsupportedSchemaError(
                "format %r is not implemented; this validator asserts formats rather "
                "than annotating them, so an unknown one fails closed" % constraint
            )
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            out.append(
                SchemaViolation(pointer, "format", "%r is not an RFC 3339 date-time" % value)
            )

    def _kw_oneOf(self, value, constraint, schema, pointer, out):
        matches = sum(1 for sub in constraint if self._subschema_valid(value, sub))
        if matches != 1:
            out.append(
                SchemaViolation(
                    pointer, "oneOf", "matched %d of %d permitted shapes, expected exactly 1"
                    % (matches, len(constraint))
                )
            )

    def _kw_anyOf(self, value, constraint, schema, pointer, out):
        if not any(self._subschema_valid(value, sub) for sub in constraint):
            out.append(SchemaViolation(pointer, "anyOf", "matched none of the permitted shapes"))

    def _kw_allOf(self, value, constraint, schema, pointer, out):
        for sub in constraint:
            self._check(value, sub, pointer, out)

    def _kw_not(self, value, constraint, schema, pointer, out):
        if self._subschema_valid(value, constraint):
            out.append(SchemaViolation(pointer, "not", "matched a shape the contract forbids"))

    def _kw_if(self, value, constraint, schema, pointer, out):
        branch = "then" if self._subschema_valid(value, constraint) else "else"
        if branch in schema:
            self._check(value, schema[branch], pointer, out)

    def _kw_then(self, value, constraint, schema, pointer, out):
        return  # applied by _kw_if

    def _kw_else(self, value, constraint, schema, pointer, out):
        return  # applied by _kw_if


def _name_of(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _same_kind(left: Any, right: Any) -> bool:
    """``True == 1`` in Python and not in JSON. Equality checks go through here."""
    return isinstance(left, bool) == isinstance(right, bool)


def load_schema(path: str) -> dict[str, Any]:
    """Read a schema off disk. Used only by tooling; the kernel reads through the manifest."""
    with open(os.path.abspath(path), encoding="utf-8") as handle:
        return json.load(handle)
