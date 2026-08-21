"""Frozen authority values, loaded from their canonical schemas. Never duplicated.

Every pattern and enum the catalog admits against is *read out of the pinned*
``ai-stack`` *schema* at build time. None is written as a literal in this
package. That is a hard rule, and it is why RK-01's own contract says the
capability pattern must be loaded "from the schema at runtime and never
duplicated as a literal" (``capability-runtime-kernel-v1.md`` section 5).

If a value cannot be read, the build raises :class:`AuthorityUnavailableError`.
A catalog that cannot read the freeze must never report that a record conforms
to it.

Authority map::

    capability name pattern
        contracts/core/capability-request.schema.json
        properties.capability.properties.name.pattern
    capability contract_version pattern
        contracts/core/capability-request.schema.json
        properties.capability.properties.contract_version.pattern
    fallback_policy enum
        contracts/core/capability-request.schema.json
        properties.constraints.properties.fallback_policy.enum
    lifecycle_status enum
        contracts/runtime/runtime-skill-descriptor.schema.json
        properties.lifecycle_status.enum
    kind enum
        contracts/runtime/runtime-skill-descriptor.schema.json
        properties.kind.enum
    record id pattern
        contracts/runtime/runtime-skill-descriptor.schema.json
        properties.id.pattern

The lifecycle *ceiling matrix* below is the one authority this module states
directly, because its canonical form is the table in
``architecture/runtime/lifecycle-and-promotion.md`` section 3 -- prose, not a
schema value that can be read. Each row is transcribed from that table, and the
table wins on any conflict. It bounds what a stage may declare; it grants
nothing, and RK-07 uses it only to reject an over-claim.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .errors import AuthorityUnavailableError

__all__ = [
    "ADVISORY_LIFECYCLE_FIELDS",
    "DEFAULT_LIFECYCLE_STATUS",
    "LIFECYCLE_CEILINGS",
    "FrozenAuthority",
    "load_frozen_authority",
]

CAPABILITY_REQUEST_SCHEMA = "contracts/core/capability-request.schema.json"
SKILL_DESCRIPTOR_SCHEMA = "contracts/runtime/runtime-skill-descriptor.schema.json"

#: ``lifecycle-and-promotion.md`` section 3, columns ``routing_eligible`` and
#: ``production_eligible``, transcribed verbatim. The stage bounds the two
#: booleans; it does not determine them -- section 3 reads "It states the
#: maximum each stage permits, not an identity."
LIFECYCLE_CEILINGS: Mapping[str, Mapping[str, bool]] = {
    "FEATURE": {"routing_eligible": False, "production_eligible": False},
    "PROTOTYPE": {"routing_eligible": False, "production_eligible": False},
    "VALIDATED": {"routing_eligible": True, "production_eligible": False},
    "INTEGRATED": {"routing_eligible": True, "production_eligible": False},
    "PRODUCTION": {"routing_eligible": True, "production_eligible": True},
    "DEPRECATED": {"routing_eligible": False, "production_eligible": False},
    "REJECTED": {"routing_eligible": False, "production_eligible": False},
}

#: ``lifecycle-and-promotion.md`` section 4 invariant 8, verbatim: "A record with
#: no ``lifecycle_status`` is admitted at ``FEATURE``" -- fail-closed, never at
#: its legacy maturity code.
DEFAULT_LIFECYCLE_STATUS = "FEATURE"

#: Advisory compatibility metadata. Read for the report, never for a decision.
#: ``runtime-skill-descriptor.schema.json``: "maturity, level, autonomy_level,
#: phase and status are advisory compatibility metadata."
ADVISORY_LIFECYCLE_FIELDS = ("autonomy_level", "level", "maturity", "phase", "status")


@dataclass(frozen=True)
class FrozenAuthority:
    """The frozen values this build admitted against, and where each came from."""

    capability_name_pattern: str
    capability_contract_version_pattern: str
    fallback_policy_enum: tuple[str, ...]
    lifecycle_status_enum: tuple[str, ...]
    kind_enum: tuple[str, ...]
    record_id_pattern: str
    #: ``required`` from ``runtime-skill-descriptor.schema.json``. Used only to
    #: report what an admitted legacy record does not declare. It is not an
    #: admission gate: no registry dialect predating the contract declares the
    #: full set, so gating on it would empty the catalog and hide the gap
    #: instead of naming it.
    descriptor_required_fields: tuple[str, ...]
    sources: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "_name_re", re.compile(self.capability_name_pattern))
        object.__setattr__(
            self, "_version_re", re.compile(self.capability_contract_version_pattern)
        )
        object.__setattr__(self, "_id_re", re.compile(self.record_id_pattern))

    def capability_name_conformant(self, name: str) -> bool:
        return self._name_re.fullmatch(name) is not None  # type: ignore[attr-defined]

    def contract_version_conformant(self, version: str) -> bool:
        return self._version_re.fullmatch(version) is not None  # type: ignore[attr-defined]

    def record_id_conformant(self, record_id: str) -> bool:
        return self._id_re.fullmatch(record_id) is not None  # type: ignore[attr-defined]

    def as_hash_input(self) -> dict[str, Any]:
        """The authority values, in the form that feeds the catalog hash.

        A change to any frozen value changes ``catalog_version`` even when every
        admitted record is otherwise identical.
        """
        return {
            "capability_contract_version_pattern": self.capability_contract_version_pattern,
            "capability_name_pattern": self.capability_name_pattern,
            "descriptor_required_fields": list(self.descriptor_required_fields),
            "fallback_policy_enum": list(self.fallback_policy_enum),
            "kind_enum": list(self.kind_enum),
            "lifecycle_ceilings": {
                stage: dict(flags) for stage, flags in LIFECYCLE_CEILINGS.items()
            },
            "lifecycle_status_enum": list(self.lifecycle_status_enum),
            "record_id_pattern": self.record_id_pattern,
            "sources": dict(self.sources),
        }


def _dig(document: Any, path: tuple[str, ...], origin: str) -> Any:
    cursor: Any = document
    for step in path:
        if not isinstance(cursor, dict) or step not in cursor:
            raise AuthorityUnavailableError(
                "%s does not carry %s; the frozen value cannot be read and the "
                "catalog refuses to substitute a literal" % (origin, ".".join(path))
            )
        cursor = cursor[step]
    return cursor


def _pattern(document: Any, path: tuple[str, ...], origin: str) -> str:
    value = _dig(document, path, origin)
    if not isinstance(value, str) or not value:
        raise AuthorityUnavailableError(
            "%s: %s is not a usable pattern" % (origin, ".".join(path))
        )
    try:
        re.compile(value)
    except re.error as exc:
        raise AuthorityUnavailableError(
            "%s: %s is not a compilable pattern (%s)" % (origin, ".".join(path), exc)
        ) from exc
    return value


def _enum(document: Any, path: tuple[str, ...], origin: str) -> tuple[str, ...]:
    value = _dig(document, path, origin)
    if not isinstance(value, list) or not value:
        raise AuthorityUnavailableError(
            "%s: %s is not a usable enum" % (origin, ".".join(path))
        )
    if not all(isinstance(item, str) for item in value):
        raise AuthorityUnavailableError(
            "%s: %s contains a non-string member" % (origin, ".".join(path))
        )
    return tuple(value)


def load_frozen_authority(read_source: Callable[[str], bytes]) -> FrozenAuthority:
    """Read every frozen value out of the pinned ``ai-stack`` schemas.

    ``read_source`` maps a repo-relative path onto the pinned bytes. It is the
    manifest-backed reader, so these values are pinned by commit SHA exactly like
    every projected record.
    """
    try:
        request_schema = json.loads(
            read_source(CAPABILITY_REQUEST_SCHEMA).decode("utf-8")
        )
        descriptor_schema = json.loads(
            read_source(SKILL_DESCRIPTOR_SCHEMA).decode("utf-8")
        )
    except Exception as exc:
        raise AuthorityUnavailableError(
            "a frozen authority schema could not be read: %s" % exc
        ) from exc

    cap = ("properties", "capability", "properties")
    return FrozenAuthority(
        capability_name_pattern=_pattern(
            request_schema, (*cap, "name", "pattern"), CAPABILITY_REQUEST_SCHEMA
        ),
        capability_contract_version_pattern=_pattern(
            request_schema,
            (*cap, "contract_version", "pattern"),
            CAPABILITY_REQUEST_SCHEMA,
        ),
        fallback_policy_enum=_enum(
            request_schema,
            ("properties", "constraints", "properties", "fallback_policy", "enum"),
            CAPABILITY_REQUEST_SCHEMA,
        ),
        lifecycle_status_enum=_enum(
            descriptor_schema,
            ("properties", "lifecycle_status", "enum"),
            SKILL_DESCRIPTOR_SCHEMA,
        ),
        kind_enum=_enum(
            descriptor_schema, ("properties", "kind", "enum"), SKILL_DESCRIPTOR_SCHEMA
        ),
        record_id_pattern=_pattern(
            descriptor_schema, ("properties", "id", "pattern"), SKILL_DESCRIPTOR_SCHEMA
        ),
        descriptor_required_fields=tuple(
            sorted(_enum(descriptor_schema, ("required",), SKILL_DESCRIPTOR_SCHEMA))
        ),
        sources={
            "capability_contract_version_pattern": CAPABILITY_REQUEST_SCHEMA,
            "capability_name_pattern": CAPABILITY_REQUEST_SCHEMA,
            "descriptor_required_fields": SKILL_DESCRIPTOR_SCHEMA,
            "fallback_policy_enum": CAPABILITY_REQUEST_SCHEMA,
            "kind_enum": SKILL_DESCRIPTOR_SCHEMA,
            "lifecycle_ceilings": "architecture/runtime/lifecycle-and-promotion.md#3",
            "lifecycle_status_enum": SKILL_DESCRIPTOR_SCHEMA,
            "record_id_pattern": SKILL_DESCRIPTOR_SCHEMA,
        },
    )
