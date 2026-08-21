"""Fail-closed admission: what is rejected, what is admitted, and on whose authority.

Every rule here traces to a line that is authority in ``ai-stack@master``. The
docstrings name it, so a rule that loses its citation is visible in review.
"""

from __future__ import annotations

import pytest

from .conftest import one_record

CONFORMANT = """
id: system.echo.reference
kind: deterministic_domain_skill
version: 1.0.0
lifecycle_status: FEATURE
capability:
  name: system.echo.execute
  contract_version: v1
fallback_policy: none
"""


def codes(catalog, record_id=None):
    out = []
    for record in catalog.rejections:
        if record_id is None or record["record_id"] == record_id:
            out.extend(reason["code"] for reason in record["reasons"])
    return sorted(out)


def test_the_baseline_record_is_admitted(tmp_path):
    catalog = one_record(tmp_path, CONFORMANT)
    assert catalog.rejected_count == 0
    assert catalog.skill("system.echo.reference") is not None


# -- capability ------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "knowledge.research",
        "storage.files",
        "resolved-by-router",
        "commerce",
        "Commerce.Catalog.Search",
    ],
)
def test_a_non_conformant_capability_name_is_rejected(tmp_path, name):
    """The pattern is the one frozen in ``capability-request.schema.json``: 3+ segments."""
    catalog = one_record(tmp_path, CONFORMANT.replace("system.echo.execute", name))
    assert catalog.admitted_count == 0
    assert "CAPABILITY_NAME_NON_CONFORMANT" in codes(catalog)


def test_a_conformant_three_segment_name_is_admitted(tmp_path):
    catalog = one_record(
        tmp_path, CONFORMANT.replace("system.echo.execute", "commerce.catalog.search")
    )
    assert catalog.admitted_count == 1


def test_a_capability_with_no_contract_version_is_rejected(tmp_path):
    """A bare ``capability: <name>`` declares no contract version, and none is invented."""
    record = CONFORMANT.replace(
        "capability:\n  name: system.echo.execute\n  contract_version: v1\n",
        "capability: system.echo.execute\n",
    )
    catalog = one_record(tmp_path, record)
    assert codes(catalog) == ["CAPABILITY_CONTRACT_VERSION_MISSING"]


def test_a_malformed_contract_version_is_rejected(tmp_path):
    catalog = one_record(
        tmp_path, CONFORMANT.replace("contract_version: v1", "contract_version: '1.0'")
    )
    assert "CAPABILITY_CONTRACT_VERSION_MALFORMED" in codes(catalog)


def test_a_missing_capability_is_rejected(tmp_path):
    record = CONFORMANT.replace(
        "capability:\n  name: system.echo.execute\n  contract_version: v1\n", ""
    )
    assert "MISSING_CAPABILITY" in codes(one_record(tmp_path, record))


def test_an_internal_workflow_candidate_requires_a_null_capability(tmp_path):
    """The ``capability: null`` discriminator: the record is not reachable through the bus."""
    record = CONFORMANT.replace(
        "kind: deterministic_domain_skill", "kind: internal_agent_workflow_candidate"
    )
    assert "CAPABILITY_NOT_APPLICABLE_FOR_KIND" in codes(one_record(tmp_path, record))

    without = record.replace(
        "capability:\n  name: system.echo.execute\n  contract_version: v1\n", ""
    )
    catalog = one_record(tmp_path / "b", without)
    assert catalog.admitted_count == 1
    assert catalog.skill("system.echo.reference")["capability"] is None


# -- kind ------------------------------------------------------------------


def test_a_missing_kind_is_rejected(tmp_path):
    """``CAPABILITY != SKILL != BINDING`` is held by the closed ``kind`` enum (ai-stack#37)."""
    catalog = one_record(tmp_path, CONFORMANT.replace("kind: deterministic_domain_skill\n", ""))
    assert codes(catalog) == ["MISSING_KIND"]


def test_an_unknown_kind_is_rejected(tmp_path):
    catalog = one_record(
        tmp_path,
        CONFORMANT.replace("deterministic_domain_skill", "external-capability-provider"),
    )
    assert "UNKNOWN_KIND" in codes(catalog)


@pytest.mark.parametrize(
    "kind",
    ["deterministic_domain_skill", "implementation_binding", "provider_binding"],
)
def test_every_capability_bearing_kind_in_the_frozen_enum_is_admitted(tmp_path, kind):
    catalog = one_record(tmp_path / kind, CONFORMANT.replace("deterministic_domain_skill", kind))
    assert catalog.admitted_count == 1


# -- fallback --------------------------------------------------------------


def test_a_fallback_policy_outside_the_frozen_enum_is_rejected(tmp_path):
    """``explicit`` is not a frozen policy (invariants 6 and 7)."""
    catalog = one_record(
        tmp_path, CONFORMANT.replace("fallback_policy: none", "fallback_policy: explicit")
    )
    assert codes(catalog) == ["FALLBACK_POLICY_NOT_IN_FROZEN_ENUM"]


def test_the_legacy_fallbacks_policy_dialect_is_read_and_still_gated(tmp_path):
    record = CONFORMANT.replace(
        "fallback_policy: none", "fallbacks:\n  policy: explicit\n  order: []"
    )
    assert codes(one_record(tmp_path, record)) == ["FALLBACK_POLICY_NOT_IN_FROZEN_ENUM"]


def test_a_named_candidate_chain_is_rejected(tmp_path):
    """A policy authorises a MODE and never names a candidate (invariant 7)."""
    record = CONFORMANT.replace("fallback_policy: none", "fallbacks: [research.web-general]")
    assert "FALLBACK_NAMED_CANDIDATE_CHAIN" in codes(one_record(tmp_path, record))


def test_a_named_candidate_chain_under_the_order_key_is_rejected(tmp_path):
    record = CONFORMANT.replace(
        "fallback_policy: none", "fallbacks:\n  policy: none\n  order: [some.other.skill]"
    )
    assert "FALLBACK_NAMED_CANDIDATE_CHAIN" in codes(one_record(tmp_path, record))


def test_an_absent_fallback_policy_defaults_to_none(tmp_path):
    """Invariant 6: "Default fallback is `none`"."""
    catalog = one_record(tmp_path, CONFORMANT.replace("fallback_policy: none\n", ""))
    assert catalog.skill("system.echo.reference")["fallback_policy"] == "none"


def test_same_semantics_only_is_admitted(tmp_path):
    catalog = one_record(tmp_path, CONFORMANT.replace("none", "same_semantics_only"))
    assert catalog.skill("system.echo.reference")["fallback_policy"] == "same_semantics_only"


# -- lifecycle -------------------------------------------------------------


def test_a_missing_lifecycle_status_is_admitted_at_feature(tmp_path):
    """``lifecycle-and-promotion.md`` section 4 invariant 8, verbatim."""
    catalog = one_record(tmp_path, CONFORMANT.replace("lifecycle_status: FEATURE\n", ""))
    record = catalog.skill("system.echo.reference")
    assert catalog.rejected_count == 0
    assert record["lifecycle_status"] == "FEATURE"
    assert record["lifecycle_status_declared"] is False


def test_legacy_maturity_metadata_never_promotes_lifecycle(tmp_path):
    """``maturity: S2  # INTEGRATED`` stays advisory. The record lands at FEATURE.

    This is the specific defect the rule exists for: a record asserting an
    advanced legacy maturity code and no ``lifecycle_status`` must not inherit
    that code as its runtime lifecycle.
    """
    record = CONFORMANT.replace(
        "lifecycle_status: FEATURE\n",
        "maturity: S2\nlevel: L1\nautonomy_level: A2\nphase: PRE_APPROVAL\nstatus: skill\n",
    )
    projected = one_record(tmp_path, record).skill("system.echo.reference")

    assert projected["lifecycle_status"] == "FEATURE"
    assert projected["lifecycle_status_declared"] is False
    assert projected["advisory_metadata"] == {
        "autonomy_level": "A2",
        "level": "L1",
        "maturity": "S2",
        "phase": "PRE_APPROVAL",
        "status": "skill",
    }


def test_an_unknown_lifecycle_status_is_rejected(tmp_path):
    """P0-LIFE-003: unknown or ambiguous lifecycle fails closed."""
    catalog = one_record(
        tmp_path, CONFORMANT.replace("lifecycle_status: FEATURE", "lifecycle_status: S2")
    )
    assert "UNKNOWN_LIFECYCLE_STATUS" in codes(catalog)


# -- the tri-state ---------------------------------------------------------


def test_the_tri_state_defaults_fail_closed(tmp_path):
    """Absent flags are false. Section 4 invariant 5: "a missing ... is false"."""
    routing = one_record(tmp_path, CONFORMANT).skill("system.echo.reference")["routing"]
    assert routing["discoverable"] is True
    assert routing["routing_eligible"] is False
    assert routing["production_eligible"] is False
    assert routing["declared"] == {
        "discoverable": False,
        "production_eligible": False,
        "routing_eligible": False,
    }


def test_a_flag_above_the_stage_ceiling_is_rejected_not_clamped(tmp_path):
    """FEATURE permits neither flag. The catalog reports the over-claim; it does not repair it."""
    record = CONFORMANT + "routing:\n  routing_eligible: true\n"
    catalog = one_record(tmp_path, record)
    assert catalog.admitted_count == 0
    assert codes(catalog) == ["LIFECYCLE_FLAG_EXCEEDS_STAGE_CEILING"]


def test_production_eligible_is_rejected_below_production(tmp_path):
    """Only PRODUCTION may carry ``production_eligible`` (section 4 invariant 3)."""
    record = CONFORMANT.replace("lifecycle_status: FEATURE", "lifecycle_status: INTEGRATED")
    record += "routing:\n  routing_eligible: true\n  production_eligible: true\n"
    assert codes(one_record(tmp_path, record)) == ["LIFECYCLE_FLAG_EXCEEDS_STAGE_CEILING"]


def test_a_validated_record_may_declare_routing_eligible(tmp_path):
    record = CONFORMANT.replace("lifecycle_status: FEATURE", "lifecycle_status: VALIDATED")
    record += "routing:\n  routing_eligible: true\n"
    routing = one_record(tmp_path, record).skill("system.echo.reference")["routing"]
    assert routing["routing_eligible"] is True
    assert routing["production_eligible"] is False


def test_a_non_boolean_eligibility_flag_is_rejected(tmp_path):
    record = CONFORMANT + "routing:\n  routing_eligible: blocked\n"
    assert "FIELD_TYPE_NON_CONFORMANT" in codes(one_record(tmp_path, record))


# -- taxonomy --------------------------------------------------------------


def test_a_capability_that_is_also_its_own_skill_id_is_rejected(tmp_path):
    """``CAPABILITY != SKILL != BINDING != TOOL != ADAPTER != PROVIDER`` (invariant 4).

    The same defect the static linter reports as ``P0-TAX-001``. A record whose
    capability name *is* its skill id leaves the Domain Router nothing to resolve.
    """
    record = CONFORMANT + (
        "implementation_binding:\n"
        "  skill_id: system.echo.execute\n"
        "  skill_version: 1.0.0\n"
    )
    catalog = one_record(tmp_path, record)
    assert catalog.admitted_count == 0
    assert codes(catalog) == ["TAXONOMY_IDENTIFIER_COLLISION"]


def test_a_capability_that_is_also_its_own_provider_is_rejected(tmp_path):
    record = CONFORMANT + (
        "implementation_binding:\n"
        "  skill_id: echo.local\n"
        "  provider: system.echo.execute\n"
    )
    assert "TAXONOMY_IDENTIFIER_COLLISION" in codes(one_record(tmp_path, record))


def test_an_adapter_that_is_also_the_provider_is_rejected(tmp_path):
    record = CONFORMANT + (
        "implementation_binding:\n"
        "  skill_id: echo.local\n"
        "  adapter_id: shopee-open-api\n"
        "  provider: shopee-open-api\n"
    )
    assert "TAXONOMY_IDENTIFIER_COLLISION" in codes(one_record(tmp_path, record))


def test_distinct_identifiers_per_role_are_admitted(tmp_path):
    """The rule bars a collision, not the presence of implementation identity."""
    record = CONFORMANT + (
        "implementation_binding:\n"
        "  skill_id: echo.local\n"
        "  skill_version: 1.0.0\n"
        "  adapter_id: echo-adapter\n"
        "  provider: echo-provider\n"
    )
    catalog = one_record(tmp_path, record)
    assert catalog.admitted_count == 1
    binding = catalog.skill("system.echo.reference")["implementation_binding"]
    assert binding["skill_id"] == "echo.local"
    assert binding["provider"] == "echo-provider"


# -- governance ------------------------------------------------------------


@pytest.mark.parametrize("value", ["false", "true", "supervised"])
def test_the_legacy_human_approval_field_is_rejected(tmp_path, value):
    """``supervised`` has no boolean image, so no value of the field is coerced."""
    catalog = one_record(tmp_path / value, CONFORMANT + "human_approval: %s\n" % value)
    assert codes(catalog) == ["LEGACY_APPROVAL_FIELD_NOT_ACCEPTED"]


# -- identity --------------------------------------------------------------


def test_a_record_with_no_id_is_rejected(tmp_path):
    catalog = one_record(tmp_path, CONFORMANT.replace("id: system.echo.reference\n", ""))
    assert codes(catalog) == ["MISSING_ID"]
    assert catalog.rejections[0]["record_id"] is None
    assert catalog.rejections[0]["record_ref"].endswith("features/record.yaml#$")


def test_every_failing_rule_is_reported_not_just_the_first(tmp_path):
    """One report entry names everything wrong with the record."""
    record = """
id: broken.record
capability: two.segments
fallback_policy: explicit
human_approval: supervised
"""
    assert codes(one_record(tmp_path, record)) == [
        "CAPABILITY_CONTRACT_VERSION_MISSING",
        "CAPABILITY_NAME_NON_CONFORMANT",
        "FALLBACK_POLICY_NOT_IN_FROZEN_ENUM",
        "LEGACY_APPROVAL_FIELD_NOT_ACCEPTED",
        "MISSING_KIND",
    ]


def test_a_duplicate_id_rejects_every_claimant(tmp_path):
    """No precedence between pinned sources is invented, so no claimant survives."""
    from ginkgo_runtime.catalog.build import build_catalog

    from .conftest import make_snapshot

    root = make_snapshot(
        tmp_path,
        [
            ("features/one.yaml", "skill_descriptor", None, CONFORMANT),
            ("features/two.yaml", "skill_descriptor", None, CONFORMANT),
        ],
    )
    catalog = build_catalog(root)
    assert catalog.admitted_count == 0
    assert catalog.rejected_count == 2
    assert codes(catalog) == ["DUPLICATE_RECORD_ID", "DUPLICATE_RECORD_ID"]


def test_a_rejection_always_carries_provenance(catalog):
    """Nothing disappears silently: reason, source ref and identity, every time."""
    assert catalog.rejected_count > 0
    for record in catalog.rejections:
        assert record["reasons"]
        assert "@" in record["source_ref"] and ":" in record["source_ref"]
        assert record["record_ref"].startswith(record["source_ref"])
        assert "record_id" in record
        for reason in record["reasons"]:
            assert reason["code"] and reason["detail"]
