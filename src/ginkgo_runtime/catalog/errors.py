"""Failure vocabulary for the Runtime Catalog.

Two kinds of failure exist, and conflating them is the defect this split
prevents:

* a **build error** — the catalog cannot be produced at all (a pinned source is
  missing, an integrity check failed, a frozen authority is unreadable). The
  build aborts. It never degrades into a partial catalog;
* a **rejection** — one record is not admissible. The record is excluded and
  named in the rejection report, with a reason, a source ref and its identity.
  It never disappears silently.

A record is never repaired. The rejection report is the migration backlog and it
belongs to the repository that authored the record, not to the runtime.
"""

from __future__ import annotations

__all__ = [
    "REJECTION_REASONS",
    "AuthorityUnavailableError",
    "CatalogBuildError",
    "SourceIntegrityError",
    "SourceUnavailableError",
]


class CatalogBuildError(RuntimeError):
    """The catalog could not be built. Fail closed; no partial catalog is returned."""


class SourceUnavailableError(CatalogBuildError):
    """A source pinned in the manifest is not present in the snapshot."""


class SourceIntegrityError(CatalogBuildError):
    """A pinned source is present but its bytes do not match the manifest digest."""


class AuthorityUnavailableError(CatalogBuildError):
    """A frozen authority (pattern or enum) could not be read from its canonical schema.

    Loading the value is not optional and there is no fallback literal: a catalog
    that cannot read the freeze must never claim a record conforms to it.
    """


#: Structured rejection reasons. The key is the stable machine code that appears
#: in the report; the value explains the rule and cites the authority that sets
#: it. Adding a code here is a contract change and is reviewed as one.
REJECTION_REASONS: dict[str, str] = {
    # -- identity ---------------------------------------------------------
    "MISSING_ID": (
        "The record declares no `id`. Records are resolved by `id:` and never by "
        "filename (ai-stack#37)."
    ),
    "MALFORMED_ID": (
        "`id` does not match the pattern frozen in "
        "contracts/runtime/runtime-skill-descriptor.schema.json."
    ),
    "DUPLICATE_RECORD_ID": (
        "The same `id` was admitted from more than one pinned source. The catalog "
        "invents no precedence between sources, so every claimant is rejected."
    ),
    # -- taxonomy ---------------------------------------------------------
    "MISSING_KIND": (
        "No `kind` discriminator. `CAPABILITY != SKILL != BINDING` is preserved by "
        "the closed `kind` enum, so a record without it has no taxonomy "
        "(ai-stack#37; capability-runtime-kernel-v1.md §6)."
    ),
    "UNKNOWN_KIND": (
        "`kind` is outside the enum frozen in "
        "contracts/runtime/runtime-skill-descriptor.schema.json."
    ),
    # -- capability -------------------------------------------------------
    "MISSING_CAPABILITY": (
        "No `capability`. Only `kind: internal_agent_workflow_candidate` may carry "
        "`capability: null`, and it is then unreachable through the Capability Bus."
    ),
    "CAPABILITY_NAME_NON_CONFORMANT": (
        "The capability name fails the pattern frozen in "
        "contracts/core/capability-request.schema.json (minimum three "
        "dot-separated segments). No caller could ever name this record."
    ),
    "CAPABILITY_CONTRACT_VERSION_MISSING": (
        "The record names a capability but declares no `contract_version`. "
        "CapabilityRequest v1 requires it, and the catalog does not invent one."
    ),
    "CAPABILITY_CONTRACT_VERSION_MALFORMED": (
        "`contract_version` does not match the frozen pattern ^v[0-9]+$."
    ),
    "CAPABILITY_NOT_APPLICABLE_FOR_KIND": (
        "`kind: internal_agent_workflow_candidate` requires `capability: null`; the "
        "record is not reachable through the Capability Bus."
    ),
    # -- fallback ---------------------------------------------------------
    "FALLBACK_POLICY_NOT_IN_FROZEN_ENUM": (
        "`fallback_policy` is outside the enum frozen in "
        "contracts/core/capability-request.schema.json (invariants 6 and 7). "
        "`explicit` is not a frozen policy."
    ),
    "FALLBACK_POLICY_CONTRADICTION": (
        "`fallback_policy` and the legacy `fallbacks.policy` are both declared and "
        "disagree. Preferring either one would silently discard a governance "
        "declaration the record makes, so the record has to say one thing."
    ),
    "FALLBACK_NAMED_CANDIDATE_CHAIN": (
        "A named fallback candidate list is declared. A policy authorises a MODE "
        "and never names a candidate (invariant 7)."
    ),
    # -- lifecycle --------------------------------------------------------
    "UNKNOWN_LIFECYCLE_STATUS": (
        "`lifecycle_status` is outside the ladder frozen in "
        "contracts/runtime/runtime-skill-descriptor.schema.json. An unknown "
        "lifecycle fails closed; it is never mapped onto a legacy maturity code."
    ),
    "UNKNOWN_LIFECYCLE_CEILING": (
        "`lifecycle_ceiling` is outside the frozen ladder."
    ),
    "LIFECYCLE_FLAG_EXCEEDS_STAGE_CEILING": (
        "A declared routing/production flag is more permissive than the stage "
        "permits in lifecycle-and-promotion.md §3. The catalog does not clamp the "
        "value down — repairing a record is the authoring repository's work."
    ),
    # -- taxonomy collisions ----------------------------------------------
    "TAXONOMY_IDENTIFIER_COLLISION": (
        "One identifier fills more than one taxonomy role inside a single record. "
        "CAPABILITY != SKILL != BINDING != TOOL != ADAPTER != PROVIDER (invariant 4, "
        "ADR-002), and the static linter reports the same defect as P0-TAX-001. A "
        "capability whose own name is also its skill id gives the Domain Router "
        "nothing to resolve."
    ),
    # -- governance -------------------------------------------------------
    "LEGACY_APPROVAL_FIELD_NOT_ACCEPTED": (
        "The legacy `human_approval` field is present. It is explicitly not "
        "accepted (runtime-skill-descriptor.schema.json), and its `supervised` "
        "value has no boolean image, so coercing it would fabricate a governance "
        "declaration."
    ),
    # -- shape ------------------------------------------------------------
    "RECORD_NOT_A_MAPPING": "The record is not a YAML mapping.",
    "FIELD_TYPE_NON_CONFORMANT": (
        "A projected field has a type the contract does not permit."
    ),
    "NON_CANONICALISABLE_VALUE": (
        "A projected value cannot be canonicalised deterministically."
    ),
}
