"""What the catalog actually does to the three registries at the pinned commits.

``ai-stack#37`` publishes a "Records that will be excluded on day one" list and
says of it: *"This list is the expected output, not a failure."* These tests pin
that expectation to the real corpus, so a drift in either the registries or the
projection shows up as a named failure rather than as a quietly different number.

One item on that list does not reproduce, and the discrepancy is asserted rather
than papered over -- see
:func:`test_no_ginkgo_agents_record_carries_the_legacy_approval_field`.
"""

from __future__ import annotations

import pytest

PINS = {
    "Ginkgo-Core/ai-stack": "b74ee58a8d9b5d86161ea42a4408d17cf2bc0d27",
    "Ginkgo-Core/ginkgo-agents": "89247220ffa15238cff209dba13088eae1a1a641",
    "Ginkgo-Core/ginkgo-library": "a9943567458f94a4737c2b08bd441e9aff01071f",
}

#: ``ai-stack#37``, verbatim: the ten sub-3-segment capability IDs on
#: ``ginkgo-library@main``, "re-counted 2026-08-20 by parsing all 24 `id:` entries".
SUB_THREE_SEGMENT_CAPABILITIES = {
    "creative.music",
    "database.query",
    "database.vector-search",
    "database.write",
    "dev.async-coding",
    "dev.prototype-ai",
    "knowledge.notebook",
    "knowledge.research",
    "reasoning.long-context",
    "storage.files",
}


def reasons_for(catalog, record_ref_suffix):
    return sorted(
        reason["code"]
        for record in catalog.rejections
        if str(record["record_ref"]).endswith(record_ref_suffix)
        for reason in record["reasons"]
    )


def test_the_pins_are_the_audited_commits(catalog):
    """The catalog projects the commits this branch audited, and says so."""
    observed = {str(s["repo"]): str(s["commit_sha"]) for s in catalog.pinned_sources}
    assert observed == PINS


def test_every_record_is_either_admitted_or_named(catalog):
    """Nothing disappears. The two sets partition the corpus exactly."""
    assert catalog.admitted_count == 20
    assert catalog.rejected_count == 37
    assert catalog.admitted_count + catalog.rejected_count == 57

    identities = {
        (str(r["record_class"]), str(r["record_ref"]))
        for r in list(catalog.capabilities) + list(catalog.skills)
    } | {(str(r["record_class"]), str(r["record_ref"])) for r in catalog.rejections}
    assert len(identities) == 57


def test_exactly_the_published_ten_capabilities_fail_the_name_pattern(catalog):
    """The count and the membership both, not just the count."""
    excluded = {
        str(record["record_id"])
        for record in catalog.rejections
        if record["record_class"] == "capability"
        and any(r["code"] == "CAPABILITY_NAME_NON_CONFORMANT" for r in record["reasons"])
    }
    assert excluded == SUB_THREE_SEGMENT_CAPABILITIES

    admitted = {str(record["record_id"]) for record in catalog.capabilities}
    assert len(admitted) == 13
    assert admitted.isdisjoint(SUB_THREE_SEGMENT_CAPABILITIES)


def test_the_one_taxonomy_collision_excludes_both_of_its_records(catalog):
    """``commerce.product.evaluate`` is its own skill id, on both sides.

    Not on the ``ai-stack#37`` day-one list, and it is a real defect rather than a
    stricter reading: the static linter reports the same records as ``P0-TAX-001``
    ARCHITECTURE violations, at ``ginkgo-agents`` HEAD and at ``ginkgo-library``
    HEAD independently. RK-10 is built before the kernel precisely so the kernel
    cannot admit what the linter rejects, so RK-07 fails closed and files it.
    """
    collisions = [
        record
        for record in catalog.rejections
        if any(r["code"] == "TAXONOMY_IDENTIFIER_COLLISION" for r in record["reasons"])
    ]
    assert {(str(r["record_class"]), str(r["record_id"])) for r in collisions} == {
        ("capability", "commerce.product.evaluate"),
        ("skill_descriptor", "commerce.product.evaluate"),
    }
    assert catalog.capability("commerce.product.evaluate") is None
    assert catalog.skill("commerce.product.evaluate") is None


def test_the_evaluate_descriptor_also_declares_two_unexplained_nulls(catalog):
    """Both findings are named, not just whichever gate ran first.

    ``features/commerce.product.evaluate.yaml`` declares ``adapter_id: null`` and
    ``provider: null`` with neither ``*_not_applicable_reason``, *and* uses its
    capability name as its skill id. An earlier revision returned from the binding
    projection as soon as it had a finding, so the taxonomy collision went unnamed
    -- against the rule that the report carries every failing gate.
    """
    entry = next(
        record
        for record in catalog.rejections
        if record["record_class"] == "skill_descriptor"
        and record["record_id"] == "commerce.product.evaluate"
    )
    assert sorted({r["code"] for r in entry["reasons"]}) == [
        "NULL_IDENTITY_WITHOUT_REASON",
        "TAXONOMY_IDENTIFIER_COLLISION",
    ]


def test_the_local_image_skill_is_excluded_for_its_fallback_policy(catalog):
    """``ai-stack#37``: "fallbacks.policy: explicit ... and no lifecycle_status at all"."""
    codes = reasons_for(catalog, "skills/media/image/generate.local/skill.yaml#$")
    assert "FALLBACK_POLICY_NOT_IN_FROZEN_ENUM" in codes
    assert "MISSING_KIND" in codes
    # The missing lifecycle_status is NOT a rejection: section 4 invariant 8
    # admits it at FEATURE. It is the fallback policy that excludes this record.
    assert "UNKNOWN_LIFECYCLE_STATUS" not in codes


def test_the_market_deep_scan_skill_is_excluded_for_its_named_candidate(catalog):
    """``ai-stack#37``: ``fallbacks: [research.web-general]`` contradicts the frozen policy."""
    matches = [
        record
        for record in catalog.rejections
        if record["record_id"] == "research.market-deep-scan"
    ]
    assert len(matches) == 1
    codes = sorted(reason["code"] for reason in matches[0]["reasons"])
    assert "FALLBACK_NAMED_CANDIDATE_CHAIN" in codes
    detail = next(
        reason["detail"]
        for reason in matches[0]["reasons"]
        if reason["code"] == "FALLBACK_NAMED_CANDIDATE_CHAIN"
    )
    assert "research.web-general" in detail


def test_fourteen_ginkgo_library_records_carry_the_legacy_approval_field(catalog):
    """The ``ginkgo-library`` half of the ``ai-stack#37`` count reproduces exactly."""
    offenders = [
        record
        for record in catalog.rejections
        if any(r["code"] == "LEGACY_APPROVAL_FIELD_NOT_ACCEPTED" for r in record["reasons"])
    ]
    assert len(offenders) == 14
    assert all("ginkgo-library" in str(r["source_ref"]) for r in offenders)

    supervised = [
        record
        for record in offenders
        for reason in record["reasons"]
        if reason["code"] == "LEGACY_APPROVAL_FIELD_NOT_ACCEPTED"
        and "supervised" in reason["detail"]
    ]
    assert [str(r["record_id"]) for r in supervised] == ["code.implement.scoped"]


def test_no_ginkgo_agents_record_carries_the_legacy_approval_field(catalog):
    """An audit finding, asserted so it cannot be lost.

    ``ai-stack#37`` says "**14** records in ``ginkgo-agents`` and **14** in
    ``ginkgo-library`` still carrying the legacy ``human_approval`` field". The
    library half is exact. The ``ginkgo-agents`` half is not: at the pinned
    commit the string ``human_approval`` occurs **zero** times anywhere in that
    repository.

    RK-07 reports what the corpus contains, so nothing here is adjusted to match
    the prose. The issue text is the thing that needs correcting, and this test
    is the evidence for that.
    """
    offenders = [
        record
        for record in catalog.rejections
        if "ginkgo-agents" in str(record["source_ref"])
        and any(r["code"] == "LEGACY_APPROVAL_FIELD_NOT_ACCEPTED" for r in record["reasons"])
    ]
    assert offenders == []


def test_the_admitted_skills_are_the_conformant_agents_descriptors(catalog):
    """Only ``ginkgo-agents/features/`` currently authors a contract-shaped record."""
    assert {str(record["record_id"]) for record in catalog.skills} == {
        "amazon-spapi.catalog.search",
        "amazon-spapi.inventory.read",
        "amazon-spapi.orders.read",
        "amazon-spapi.pricing.read",
        "shopee-open-api.catalog.read",
        "shopee-open-api.orders.read",
        "shopee.product.normalize",
    }
    assert all("ginkgo-agents" in str(r["source_ref"]) for r in catalog.skills)


def test_the_ginkgo_library_twins_are_excluded_for_a_missing_contract_version(catalog):
    """The same eight ids are authored twice, in two repos, in two dialects.

    Only the ``ginkgo-agents`` dialect declares ``contract_version``, so the
    ``ginkgo-library`` twin is excluded and no ``DUPLICATE_RECORD_ID`` arises.
    That is a real migration finding, not an accident of ordering: were the
    library twin ever to declare a contract version, both would collide and both
    would be rejected.
    """
    twins = [
        record
        for record in catalog.rejections
        if "ginkgo-library" in str(record["source_ref"])
        and [r["code"] for r in record["reasons"]] == ["CAPABILITY_CONTRACT_VERSION_MISSING"]
    ]
    # Every admitted skill has a library twin excluded for exactly this reason. The
    # set is larger by one: `commerce.product.evaluate` is excluded on both sides,
    # its library twin for the missing contract version and its ginkgo-agents record
    # for the taxonomy collision.
    assert {str(record["record_id"]) for record in twins} == {
        str(record["record_id"]) for record in catalog.skills
    } | {"commerce.product.evaluate"}
    assert not any(
        r["code"] == "DUPLICATE_RECORD_ID"
        for record in catalog.rejections
        for r in record["reasons"]
    )


def test_nothing_in_the_catalog_is_routable_or_production_eligible(catalog):
    """Day-one truth, and invariant 16 holding on every admitted record."""
    for record in catalog.skills:
        assert record["routing"]["routing_eligible"] is False
        assert record["routing"]["production_eligible"] is False
        assert record["routing"]["discoverable"] is True
        assert record["lifecycle_status"] in {"FEATURE", "PROTOTYPE"}


def test_every_admitted_record_declares_its_lifecycle(catalog):
    """No admitted record relied on the FEATURE default, so none was inferred."""
    assert all(record["lifecycle_status_declared"] is True for record in catalog.skills)


def test_the_descriptor_gaps_are_reported_on_every_admitted_record(catalog):
    """Admitted is not the same as complete, and the catalog says which is which.

    The gap is a tuple, not a list: the in-process snapshot is immutable all the
    way down, so a consumer cannot append to a record's gap list either.
    """
    for record in catalog.skills:
        assert record["descriptor_gaps"] == ("schema_version", "side_effect_class")


@pytest.mark.parametrize("reason_code", ["MISSING_KIND", "CAPABILITY_NAME_NON_CONFORMANT"])
def test_the_backlog_totals_are_reported_by_reason(report, reason_code):
    assert report["totals"]["rejected_by_reason"][reason_code] > 0


def test_the_report_names_every_excluded_record_individually(report, catalog):
    """The totals summarise the backlog; they do not replace it."""
    assert len(report["records"]) == catalog.rejected_count
    assert report["catalog_version"] == catalog.catalog_version
    assert report["totals"]["rejected_by_source_repo"] == {
        "Ginkgo-Core/ginkgo-agents": 4,
        "Ginkgo-Core/ginkgo-library": 33,
    }
