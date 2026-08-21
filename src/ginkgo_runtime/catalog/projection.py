"""Normalisation and fail-closed admission -- the projection step.

This module is where a record authored in one of three registry dialects becomes
either an admitted catalog entry or a named rejection. Two rules govern
everything here:

**Normalisation is minimal.** It reconciles *shape*, never *meaning*. A
capability written ``capability: commerce.catalog.search`` and one written
``capability: {name: ..., contract_version: v1}`` reach the same projected form,
because that is a dialect difference. A missing ``contract_version`` does *not*
become ``v1``, because that would be a fabricated declaration.

**Nothing is repaired.** A non-conformant record is excluded and reported with a
structured reason, its source ref and its identity. Fixing it is the authoring
repository's work; the rejection report is that backlog. The runtime never
edits, coerces or upgrades a legacy record -- and never writes to a source.

An admitted record is *admissible*, not *complete*. Legacy dialects predate
``RuntimeSkillDescriptor v1`` and none of them declares its full required set,
so every admitted record carries ``descriptor_gaps``: the fields the frozen
schema requires that the source does not declare. RK-07 reports the gap; it does
not fill it, and it does not reject for it. RK-02 and RK-04 fail closed on a gap
they need.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .authority import (
    ADVISORY_LIFECYCLE_FIELDS,
    DEFAULT_LIFECYCLE_STATUS,
    LIFECYCLE_CEILINGS,
    FrozenAuthority,
)
from .loader import RawRecord

__all__ = ["AdmittedRecord", "RejectedRecord", "Rejection", "project"]

#: Every stage in the lifecycle matrix carries ``discoverable: yes``, so an
#: admitted record is discoverable. Discoverability grants no routing: it is the
#: weakest of the three states and exists so a record can be seen without being
#: selectable (``lifecycle-and-promotion.md`` sections 2 and 3).
DEFAULT_DISCOVERABLE = True

#: Fail-closed defaults for the two eligibility booleans. ``lifecycle-and-promotion.md``
#: section 4 invariant 5: "a missing or unreadable production_eligible is false".
DEFAULT_ROUTING_ELIGIBLE = False
DEFAULT_PRODUCTION_ELIGIBLE = False

#: The frozen default policy: invariant 6, "Default fallback is `none`".
DEFAULT_FALLBACK_POLICY = "none"

#: Legacy governance field with no boolean image. See the rejection reason.
LEGACY_APPROVAL_FIELD = "human_approval"


@dataclass(frozen=True)
class Rejection:
    """One reason a record was not admitted."""

    code: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "detail": self.detail}


@dataclass(frozen=True)
class RejectedRecord:
    """An excluded record, named precisely enough to act on."""

    record_class: str
    record_id: str | None
    source_ref: str
    record_ref: str
    #: Position in the source document. Sort key only, so the report reads in
    #: authoring order rather than in lexicographic locator order, where
    #: ``capabilities[10]`` would precede ``capabilities[2]``.
    source_index: int
    reasons: tuple[Rejection, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "record_class": self.record_class,
            "record_id": self.record_id,
            "record_ref": self.record_ref,
            "reasons": [r.as_dict() for r in self.reasons],
            "source_ref": self.source_ref,
        }


@dataclass(frozen=True)
class AdmittedRecord:
    """A record admitted into the catalog."""

    record_class: str
    record_id: str
    source_ref: str
    record_ref: str
    source_index: int
    projection: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "record_class": self.record_class,
            "record_id": self.record_id,
            "record_ref": self.record_ref,
            "source_ref": self.source_ref,
            **dict(self.projection),
        }


# --------------------------------------------------------------------------
# small typed readers -- each returns (value, rejection|None)
# --------------------------------------------------------------------------


def _as_str(value: Any, field: str) -> tuple[str | None, Rejection | None]:
    if value is None:
        return None, None
    if isinstance(value, str):
        return value.strip() or None, None
    return None, Rejection(
        "FIELD_TYPE_NON_CONFORMANT",
        "%s must be a string; found %s" % (field, type(value).__name__),
    )


def _as_bool(value: Any, field: str) -> tuple[bool | None, Rejection | None]:
    if value is None:
        return None, None
    if isinstance(value, bool):
        return value, None
    return None, Rejection(
        "FIELD_TYPE_NON_CONFORMANT",
        "%s must be a boolean; found %r" % (field, value),
    )


def _advisory(data: Mapping[str, Any]) -> dict[str, str]:
    """Legacy maturity metadata, kept for the report and used for no decision."""
    out: dict[str, str] = {}
    for field in ADVISORY_LIFECYCLE_FIELDS:
        value = data.get(field)
        if isinstance(value, str | int) and not isinstance(value, bool):
            out[field] = str(value)
    return out


#: Taxonomy roles an identifier may fill. Kept in one place so the collision check
#: below and the projection it inspects cannot drift apart.
TAXONOMY_ROLES = ("binding_id", "adapter_id", "provider", "tool_id", "skill_id")


def _taxonomy_collisions(assignments: list[tuple[str, str]]) -> list[Rejection]:
    """Reject an identifier that fills more than one taxonomy role in one record.

    ``CAPABILITY != SKILL != BINDING != TOOL != ADAPTER != PROVIDER`` (invariant 4,
    ADR-002). The static linter reports the same defect as ``P0-TAX-001``, and RK-10
    is built before the kernel precisely so the kernel cannot admit what the linter
    rejects -- so this is a rejection, not a warning.

    A record whose capability name *is* its skill id leaves the Domain Router with
    nothing to resolve: the public semantic contract and the implementation that
    serves it have collapsed into one name.
    """
    roles: dict[str, set[str]] = {}
    for role, identifier in assignments:
        if identifier:
            roles.setdefault(identifier, set()).add(role)
    return [
        Rejection(
            "TAXONOMY_IDENTIFIER_COLLISION",
            "identifier %r is used for %s at once; CAPABILITY != SKILL != BINDING != "
            "TOOL != ADAPTER != PROVIDER" % (identifier, sorted(filled)),
        )
        for identifier, filled in sorted(roles.items())
        if len(filled) > 1
    ]


# --------------------------------------------------------------------------
# capability records
# --------------------------------------------------------------------------


def _project_capability(
    raw: RawRecord, authority: FrozenAuthority
) -> tuple[dict[str, Any] | None, list[Rejection], str | None]:
    reasons: list[Rejection] = []
    data = raw.data
    if not isinstance(data, dict):
        return None, [Rejection("RECORD_NOT_A_MAPPING", "record is not a mapping")], None

    record_id = raw.declared_id()
    if record_id is None:
        reasons.append(
            Rejection("MISSING_ID", "no usable `id`; records are resolved by id, never by filename")
        )
    elif not authority.capability_name_conformant(record_id):
        reasons.append(
            Rejection(
                "CAPABILITY_NAME_NON_CONFORMANT",
                "capability id %r has %d dot-separated segment(s) and fails the "
                "frozen pattern %s"
                % (record_id, len(record_id.split(".")), authority.capability_name_pattern),
            )
        )

    ceiling, err = _as_str(data.get("lifecycle_ceiling"), "lifecycle_ceiling")
    if err:
        reasons.append(err)
    elif ceiling is not None and ceiling not in authority.lifecycle_status_enum:
        reasons.append(
            Rejection(
                "UNKNOWN_LIFECYCLE_CEILING",
                "lifecycle_ceiling %r is not in the frozen ladder %s"
                % (ceiling, list(authority.lifecycle_status_enum)),
            )
        )

    purpose, err = _as_str(data.get("purpose"), "purpose")
    if err:
        reasons.append(err)
    output_contract, err = _as_str(data.get("output_contract"), "output_contract")
    if err:
        reasons.append(err)

    # `bindings[].skill_id` is carried through as opaque text so RK-02 can see
    # what the registry declares. It is explicitly NOT resolved here: resolving
    # capability -> skill is the Domain Router's sole authority (invariant 4).
    declared: list[str] = []
    bindings = data.get("bindings")
    if isinstance(bindings, list):
        for binding in bindings:
            if isinstance(binding, dict):
                skill_id, err = _as_str(binding.get("skill_id"), "bindings[].skill_id")
                if err:
                    reasons.append(err)
                elif skill_id:
                    declared.append(skill_id)

    reasons.extend(
        _taxonomy_collisions(
            [("capability", record_id or "")] + [("skill_id", d) for d in declared]
        )
    )

    if reasons:
        return None, reasons, record_id

    projection = {
        "declared_binding_skill_ids": sorted(set(declared)),
        "lifecycle_ceiling": ceiling,
        "output_contract": output_contract,
        "purpose": purpose,
    }
    return projection, [], record_id


# --------------------------------------------------------------------------
# skill descriptor records
# --------------------------------------------------------------------------


def _read_capability(
    data: Mapping[str, Any], authority: FrozenAuthority, kind: str | None
) -> tuple[dict[str, Any] | None, list[Rejection]]:
    """Reconcile the two capability dialects without inventing a contract version."""
    reasons: list[Rejection] = []
    raw = data.get("capability")

    if raw is None:
        if kind == "internal_agent_workflow_candidate":
            return None, []
        reasons.append(
            Rejection(
                "MISSING_CAPABILITY",
                "no `capability`; only kind internal_agent_workflow_candidate may "
                "carry a null capability",
            )
        )
        return None, reasons

    if isinstance(raw, str):
        name: str | None = raw.strip() or None
        version: str | None = None
        version_declared = False
    elif isinstance(raw, dict):
        name, err = _as_str(raw.get("name"), "capability.name")
        if err:
            reasons.append(err)
        version, err = _as_str(raw.get("contract_version"), "capability.contract_version")
        if err:
            reasons.append(err)
        version_declared = "contract_version" in raw
    else:
        return None, [
            Rejection(
                "FIELD_TYPE_NON_CONFORMANT",
                "capability must be a string or a mapping; found %s" % type(raw).__name__,
            )
        ]

    if kind == "internal_agent_workflow_candidate":
        reasons.append(
            Rejection(
                "CAPABILITY_NOT_APPLICABLE_FOR_KIND",
                "kind internal_agent_workflow_candidate requires capability: null",
            )
        )

    if name is None:
        reasons.append(Rejection("MISSING_CAPABILITY", "capability name is empty"))
    elif not authority.capability_name_conformant(name):
        reasons.append(
            Rejection(
                "CAPABILITY_NAME_NON_CONFORMANT",
                "capability name %r has %d dot-separated segment(s) and fails the "
                "frozen pattern %s"
                % (name, len(name.split(".")), authority.capability_name_pattern),
            )
        )

    if version is None:
        reasons.append(
            Rejection(
                "CAPABILITY_CONTRACT_VERSION_MISSING",
                "the record names a capability but declares no contract_version; "
                "CapabilityRequest v1 requires one and the catalog invents none"
                if not version_declared
                else "contract_version is declared but empty",
            )
        )
    elif not authority.contract_version_conformant(version):
        reasons.append(
            Rejection(
                "CAPABILITY_CONTRACT_VERSION_MALFORMED",
                "contract_version %r fails the frozen pattern %s"
                % (version, authority.capability_contract_version_pattern),
            )
        )

    if reasons:
        return None, reasons
    return {"contract_version": version, "name": name}, []


def _read_fallback_policy(
    data: Mapping[str, Any], authority: FrozenAuthority
) -> tuple[str | None, list[Rejection]]:
    """Reconcile ``fallback_policy``, legacy ``fallbacks.policy`` and legacy lists.

    A policy authorises a MODE. A list of named candidates is not a policy in a
    legacy shape -- it is the thing invariant 7 forbids -- so it is rejected
    rather than reinterpreted.
    """
    reasons: list[Rejection] = []
    declared: str | None = None

    value, err = _as_str(data.get("fallback_policy"), "fallback_policy")
    if err:
        reasons.append(err)
    elif value is not None:
        declared = value

    legacy = data.get("fallbacks")
    if isinstance(legacy, dict):
        legacy_policy, err = _as_str(legacy.get("policy"), "fallbacks.policy")
        if err:
            reasons.append(err)
        elif legacy_policy is not None:
            if declared is None:
                declared = legacy_policy
            elif legacy_policy != declared:
                # Both dialects declared, and they disagree. Preferring either one
                # would silently discard a governance declaration the record makes;
                # the record has to say one thing.
                reasons.append(
                    Rejection(
                        "FALLBACK_POLICY_CONTRADICTION",
                        "fallback_policy is %r and fallbacks.policy is %r; the "
                        "catalog does not choose between two declarations"
                        % (declared, legacy_policy),
                    )
                )
        order = legacy.get("order")
        if isinstance(order, list) and order:
            reasons.append(
                Rejection(
                    "FALLBACK_NAMED_CANDIDATE_CHAIN",
                    "fallbacks.order names %d candidate(s); a policy authorises a "
                    "mode and never names a candidate (invariant 7)" % len(order),
                )
            )
    elif isinstance(legacy, list) and legacy:
        reasons.append(
            Rejection(
                "FALLBACK_NAMED_CANDIDATE_CHAIN",
                "fallbacks names %d candidate(s) (%s); a policy authorises a mode "
                "and never names a candidate (invariant 7)"
                % (len(legacy), ", ".join(str(item) for item in legacy)),
            )
        )

    effective = DEFAULT_FALLBACK_POLICY if declared is None else declared
    if effective not in authority.fallback_policy_enum:
        reasons.append(
            Rejection(
                "FALLBACK_POLICY_NOT_IN_FROZEN_ENUM",
                "fallback policy %r is not in the frozen enum %s"
                % (effective, list(authority.fallback_policy_enum)),
            )
        )

    if reasons:
        return None, reasons
    return effective, []


def _read_routing(
    data: Mapping[str, Any], lifecycle_status: str | None
) -> tuple[dict[str, Any] | None, list[Rejection]]:
    """Project the tri-state, fail closed, and reject an over-claim rather than clamp it."""
    reasons: list[Rejection] = []
    block = data.get("routing")
    if block is not None and not isinstance(block, dict):
        # The linter says the same thing as P0-LIFE-003: "routing must be a
        # mapping; a malformed eligibility block fails closed". Falling through to
        # the top-level dialect here would read an unparseable eligibility block as
        # "declares nothing" and admit the record at the fail-closed defaults --
        # which looks identical to a conformant record in the catalog.
        return None, [
            Rejection(
                "FIELD_TYPE_NON_CONFORMANT",
                "routing must be a mapping; found %s. A malformed eligibility "
                "block fails closed rather than reading as an absent one."
                % type(block).__name__,
            )
        ]
    scoped: Mapping[str, Any] = block if isinstance(block, dict) else {}

    projected: dict[str, Any] = {}
    declared_flags: dict[str, bool] = {}
    for field, default in (
        ("discoverable", DEFAULT_DISCOVERABLE),
        ("routing_eligible", DEFAULT_ROUTING_ELIGIBLE),
        ("production_eligible", DEFAULT_PRODUCTION_ELIGIBLE),
    ):
        # A record may declare the flag inside `routing:` or at the top level;
        # both dialects are in use. The nested form wins where both appear.
        raw = scoped.get(field, data.get(field))
        value, err = _as_bool(raw, field)
        if err:
            reasons.append(err)
            continue
        declared_flags[field] = raw is not None
        projected[field] = default if value is None else value

    if reasons:
        return None, reasons

    # `None` means the record declared a lifecycle the ladder does not contain.
    # It is already rejected for that; there is no stage whose ceiling applies.
    ceiling = None if lifecycle_status is None else LIFECYCLE_CEILINGS.get(lifecycle_status)
    if ceiling is not None:
        for field, permitted in ceiling.items():
            if projected.get(field) and not permitted:
                reasons.append(
                    Rejection(
                        "LIFECYCLE_FLAG_EXCEEDS_STAGE_CEILING",
                        "lifecycle_status %s permits %s: false, but the record "
                        "declares true (lifecycle-and-promotion.md section 3). The "
                        "catalog does not clamp the value down."
                        % (lifecycle_status, field),
                    )
                )

    if reasons:
        return None, reasons

    projected["declared"] = dict(sorted(declared_flags.items()))
    return projected, []


#: ``runtime-skill-descriptor.schema.json`` requires the matching reason whenever
#: the identity is null, with ``minLength: 8``: *"Do not invent an adapter
#: identity to fill the generic path; record honestly why no integration boundary
#: exists."* A null with no reason is an unexplained absence, and RK-02 cannot
#: emit an honest null from it.
NOT_APPLICABLE_REASONS = {
    "adapter_id": "adapter_not_applicable_reason",
    "provider": "provider_not_applicable_reason",
}

#: The schema's own floor for a usable reason.
MIN_REASON_LENGTH = 8


def _read_implementation_binding(
    data: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, list[Rejection]]:
    """Project implementation identity inline. No Adapter entity is modelled."""
    reasons: list[Rejection] = []
    block = data.get("implementation_binding")
    scoped: Mapping[str, Any] = block if isinstance(block, dict) else {}

    projected: dict[str, Any] = {}
    for field in ("skill_id", "skill_version", "binding_id", "adapter_id", "provider", "tool_id"):
        raw = scoped.get(field, data.get(field))
        value, err = _as_str(raw, "implementation_binding.%s" % field)
        if err:
            reasons.append(err)
            continue
        projected[field] = value

    # An honest null needs its reason projected alongside it, or the Domain
    # Router has nothing truthful to put in `*_not_applicable_reason` and would
    # have to invent one.
    #
    # The gate fires only when the identity is EXPLICITLY declared null, matching
    # the descriptor schema's own conditional, which carries
    # `required: [adapter_id]` inside its `if`. A record that never mentions
    # `adapter_id` has not claimed anything about an adapter -- that is an
    # undeclared field, reported through `descriptor_gaps`, not a null needing an
    # explanation. Conflating the two would reject every legacy record for
    # failing to explain an absence it never asserted.
    for identity_field, reason_field in NOT_APPLICABLE_REASONS.items():
        raw = scoped.get(reason_field, data.get(reason_field))
        value, err = _as_str(raw, "implementation_binding.%s" % reason_field)
        if err:
            reasons.append(err)
            continue
        projected[reason_field] = value

        declared_null = (
            identity_field in scoped or identity_field in data
        ) and projected.get(identity_field) is None
        if declared_null and (value is None or len(value) < MIN_REASON_LENGTH):
            reasons.append(
                Rejection(
                    "NULL_IDENTITY_WITHOUT_REASON",
                    "%s is declared null but %s is absent or shorter than %d "
                    "characters; an unexplained absence cannot be projected as an "
                    "honest null" % (identity_field, reason_field, MIN_REASON_LENGTH),
                )
            )

    # Invariant 17, asserted rather than switched: implementation identity is
    # never part of a caller projection.
    projected["caller_visible"] = False

    # The projection is returned even when there are findings, so the taxonomy
    # collision check downstream still sees the identities. Returning None here
    # meant a record with an unexplained null was reported for that alone and its
    # `CAPABILITY == SKILL` collision went unnamed -- against the rule that every
    # failing gate appears in the report, not only the first.
    return projected, reasons


def _descriptor_gaps(data: Mapping[str, Any], authority: FrozenAuthority) -> list[str]:
    """Required descriptor fields the source does not declare. Reported, not filled."""
    return sorted(
        field
        for field in authority.descriptor_required_fields
        if field not in data
    )


def _project_skill_descriptor(
    raw: RawRecord, authority: FrozenAuthority
) -> tuple[dict[str, Any] | None, list[Rejection], str | None]:
    reasons: list[Rejection] = []
    data = raw.data
    if not isinstance(data, dict):
        return None, [Rejection("RECORD_NOT_A_MAPPING", "record is not a mapping")], None

    record_id = raw.declared_id()
    if record_id is None:
        reasons.append(
            Rejection("MISSING_ID", "no usable `id`; records are resolved by id, never by filename")
        )
    elif not authority.record_id_conformant(record_id):
        reasons.append(
            Rejection(
                "MALFORMED_ID",
                "id %r fails the frozen pattern %s" % (record_id, authority.record_id_pattern),
            )
        )

    if LEGACY_APPROVAL_FIELD in data:
        reasons.append(
            Rejection(
                "LEGACY_APPROVAL_FIELD_NOT_ACCEPTED",
                "declares legacy %s: %r. The field is not accepted, and its value "
                "has no boolean image, so coercing it would fabricate a governance "
                "declaration." % (LEGACY_APPROVAL_FIELD, data[LEGACY_APPROVAL_FIELD]),
            )
        )

    kind, err = _as_str(data.get("kind"), "kind")
    if err:
        reasons.append(err)
    elif kind is None:
        reasons.append(
            Rejection(
                "MISSING_KIND",
                "no `kind` discriminator; CAPABILITY != SKILL != BINDING is held "
                "by the closed kind enum, so the record has no taxonomy",
            )
        )
    elif kind not in authority.kind_enum:
        reasons.append(
            Rejection(
                "UNKNOWN_KIND",
                "kind %r is not in the frozen enum %s" % (kind, list(authority.kind_enum)),
            )
        )
        kind = None

    lifecycle, err = _as_str(data.get("lifecycle_status"), "lifecycle_status")
    if err:
        reasons.append(err)
    lifecycle_declared = lifecycle is not None
    if lifecycle is None:
        # section 4 invariant 8: admitted at FEATURE, never at a legacy maturity code.
        lifecycle = DEFAULT_LIFECYCLE_STATUS
    elif lifecycle not in authority.lifecycle_status_enum:
        reasons.append(
            Rejection(
                "UNKNOWN_LIFECYCLE_STATUS",
                "lifecycle_status %r is not in the frozen ladder %s"
                % (lifecycle, list(authority.lifecycle_status_enum)),
            )
        )
        # Deliberately NOT defaulted to FEATURE here. The FEATURE default exists for
        # a record that declares no lifecycle at all; applying it to one that
        # declared an unreadable value would add a ceiling finding naming a stage
        # the record never claimed, and the report is a migration backlog someone
        # has to act on.
        lifecycle = None

    capability, capability_reasons = _read_capability(data, authority, kind)
    reasons.extend(capability_reasons)

    fallback_policy, fallback_reasons = _read_fallback_policy(data, authority)
    reasons.extend(fallback_reasons)

    routing, routing_reasons = _read_routing(data, lifecycle)
    reasons.extend(routing_reasons)

    binding, binding_reasons = _read_implementation_binding(data)
    reasons.extend(binding_reasons)

    if capability is not None and binding is not None:
        reasons.extend(
            _taxonomy_collisions(
                [("capability", capability["name"])]
                + [(role, binding.get(role) or "") for role in TAXONOMY_ROLES]
            )
        )

    if reasons:
        return None, reasons, record_id

    # Projected as declared, or null. RK-02 requires it on a ROUTED decision and
    # will refuse to route without it rather than choosing a default -- a
    # side-effect class is a governance statement, not a formality.
    side_effect_class, err = _as_str(data.get("side_effect_class"), "side_effect_class")
    if err:
        return None, [*reasons, err], record_id

    projection = {
        "advisory_metadata": _advisory(data),
        "capability": capability,
        "descriptor_gaps": _descriptor_gaps(data, authority),
        "fallback_policy": fallback_policy,
        "implementation_binding": binding,
        "kind": kind,
        "lifecycle_status": lifecycle or DEFAULT_LIFECYCLE_STATUS,
        "lifecycle_status_declared": lifecycle_declared,
        "routing": routing,
        "side_effect_class": side_effect_class,
    }
    return projection, [], record_id


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

_PROJECTORS = {
    "capability": _project_capability,
    "skill_descriptor": _project_skill_descriptor,
}


def project(
    records: tuple[RawRecord, ...], authority: FrozenAuthority
) -> tuple[tuple[AdmittedRecord, ...], tuple[RejectedRecord, ...]]:
    """Project every raw record, then enforce identity uniqueness over the survivors.

    The two stages are ordered deliberately. Per-record gates run first and
    collect *every* failing reason, so one report entry names everything wrong
    with a record instead of only the first defect. Uniqueness is then checked
    over the admitted set alone: a record that was already rejected cannot
    collide with anything, and cannot drag a conformant twin down with it.
    """
    admitted: list[AdmittedRecord] = []
    rejected: list[RejectedRecord] = []

    for raw in records:
        projector = _PROJECTORS.get(raw.record_class)
        if projector is None:  # pragma: no cover - guarded by the manifest loader
            rejected.append(
                RejectedRecord(
                    record_class=raw.record_class,
                    record_id=raw.declared_id(),
                    source_ref=raw.source_ref,
                    record_ref=raw.record_ref,
                    source_index=raw.index,
                    reasons=(
                        Rejection(
                            "FIELD_TYPE_NON_CONFORMANT",
                            "unknown record class %r" % raw.record_class,
                        ),
                    ),
                )
            )
            continue

        projection, reasons, record_id = projector(raw, authority)
        if reasons or projection is None or record_id is None:
            rejected.append(
                RejectedRecord(
                    record_class=raw.record_class,
                    record_id=record_id,
                    source_ref=raw.source_ref,
                    record_ref=raw.record_ref,
                    source_index=raw.index,
                    reasons=tuple(sorted(reasons, key=lambda r: (r.code, r.detail))),
                )
            )
            continue

        admitted.append(
            AdmittedRecord(
                record_class=raw.record_class,
                record_id=record_id,
                source_ref=raw.source_ref,
                record_ref=raw.record_ref,
                source_index=raw.index,
                projection=projection,
            )
        )

    admitted, collisions = _reject_duplicate_ids(admitted)
    rejected.extend(collisions)

    admitted.sort(key=lambda r: (r.record_class, r.record_id, r.source_ref))
    rejected.sort(key=lambda r: (r.record_class, r.source_ref, r.source_index, r.record_id or ""))
    return tuple(admitted), tuple(rejected)


def _reject_duplicate_ids(
    admitted: list[AdmittedRecord],
) -> tuple[list[AdmittedRecord], list[RejectedRecord]]:
    """Two sources claiming one id: reject every claimant.

    The catalog has no precedence rule between pinned sources and will not
    invent one. Picking a winner silently would make the catalog depend on
    manifest order, which is exactly the non-determinism this component exists
    to remove.
    """
    by_id: dict[tuple[str, str], list[AdmittedRecord]] = {}
    for record in admitted:
        by_id.setdefault((record.record_class, record.record_id), []).append(record)

    survivors: list[AdmittedRecord] = []
    collisions: list[RejectedRecord] = []
    for (record_class, record_id), claimants in by_id.items():
        if len(claimants) == 1:
            survivors.append(claimants[0])
            continue
        refs = sorted(claimant.record_ref for claimant in claimants)
        for claimant in claimants:
            collisions.append(
                RejectedRecord(
                    record_class=record_class,
                    record_id=record_id,
                    source_ref=claimant.source_ref,
                    record_ref=claimant.record_ref,
                    source_index=claimant.source_index,
                    reasons=(
                        Rejection(
                            "DUPLICATE_RECORD_ID",
                            "id %r is claimed by %d admitted records (%s); the "
                            "catalog invents no precedence between pinned sources, "
                            "so every claimant is rejected"
                            % (record_id, len(claimants), "; ".join(refs)),
                        ),
                    ),
                )
            )
    return survivors, collisions
