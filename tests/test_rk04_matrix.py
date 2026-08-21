"""Every cell of lifecycle x execution_mode, walked exhaustively.

Twenty-one cells, seven stages by three modes, each asserted against
``lifecycle-and-promotion.md`` section 3 — *"the single normative source. Where
any other document in this branch disagrees, this table wins."*

The expectations here are written out longhand rather than derived from
:data:`MATRIX`. Deriving them would make the test a restatement of the code and
it would pass no matter what the code said; written out, the test is an
independent reading of the table and disagreeing with it is a failure.
"""

from __future__ import annotations

import itertools

import pytest

from ginkgo_runtime.gate import (
    ALLOW,
    BLOCK,
    CEILINGS,
    DENIED,
    MATRIX,
    PERMITTED,
    PERMITTED_GOVERNED,
    cell,
)

from .conftest import gate_record, gate_request

STAGES = (
    "FEATURE",
    "PROTOTYPE",
    "VALIDATED",
    "INTEGRATED",
    "PRODUCTION",
    "DEPRECATED",
    "REJECTED",
)
MODES = ("certification", "non_production", "production")

#: Section 3, read off the table by hand. `G` = permitted-governed, `P` =
#: permitted, `N` = no.
EXPECTED = {
    ("FEATURE", "certification"): "G",
    ("FEATURE", "non_production"): "N",
    ("FEATURE", "production"): "N",
    ("PROTOTYPE", "certification"): "G",
    ("PROTOTYPE", "non_production"): "N",
    ("PROTOTYPE", "production"): "N",
    ("VALIDATED", "certification"): "P",
    ("VALIDATED", "non_production"): "P",
    ("VALIDATED", "production"): "N",
    ("INTEGRATED", "certification"): "P",
    ("INTEGRATED", "non_production"): "P",
    ("INTEGRATED", "production"): "N",
    ("PRODUCTION", "certification"): "P",
    ("PRODUCTION", "non_production"): "P",
    ("PRODUCTION", "production"): "P",
    ("DEPRECATED", "certification"): "P",
    ("DEPRECATED", "non_production"): "N",
    ("DEPRECATED", "production"): "N",
    ("REJECTED", "certification"): "N",
    ("REJECTED", "non_production"): "N",
    ("REJECTED", "production"): "N",
}

SYMBOL = {"G": PERMITTED_GOVERNED, "P": PERMITTED, "N": DENIED}


def test_the_matrix_covers_every_stage_and_mode():
    assert len(MATRIX) == 21
    assert set(MATRIX) == set(itertools.product(STAGES, MODES))


@pytest.mark.parametrize(("stage", "mode"), sorted(EXPECTED))
def test_every_cell_matches_the_normative_table(stage, mode):
    assert cell(stage, mode) == SYMBOL[EXPECTED[(stage, mode)]]


@pytest.mark.parametrize(("stage", "mode"), sorted(EXPECTED))
def test_every_cell_end_to_end_through_the_gate(gate, stage, mode):
    """The full function, not just the table lookup.

    Each record is built at the most permissive flags its stage allows, and every
    governance state is satisfied, so the verdict is attributable to the cell
    alone. A recorded certification authorization is supplied throughout, since
    certification requires one regardless of stage.
    """
    ceiling = CEILINGS[stage]
    record = gate_record(
        status=stage, routing_eligible=ceiling[0], production_eligible=ceiling[1]
    )
    decision = gate(
        gate_request(
            record=record,
            execution_mode=mode,
            certification_authorization_ref="cert-auth-0001",
        )
    )

    symbol = EXPECTED[(stage, mode)]
    if symbol == "N":
        assert decision.verdict == BLOCK, (stage, mode)
        assert decision.reason_code == "LIFECYCLE_MODE_DENIED"
        assert decision.outcome == "BLOCKED_LIFECYCLE"
    else:
        assert decision.verdict == ALLOW, (stage, mode, decision.reason)


def test_the_only_governed_cells_are_feature_and_prototype_in_certification():
    governed = sorted(pair for pair, value in MATRIX.items() if value == PERMITTED_GOVERNED)
    assert governed == [
        ("FEATURE", "certification"),
        ("PROTOTYPE", "certification"),
    ]


def test_rejected_is_denied_in_every_mode():
    """*"no" means at any stage of governance* — not "no unless someone signs"."""
    assert all(cell("REJECTED", mode) == DENIED for mode in MODES)


def test_deprecated_may_be_certified_but_serves_no_caller_traffic():
    assert cell("DEPRECATED", "certification") == PERMITTED
    assert cell("DEPRECATED", "non_production") == DENIED
    assert cell("DEPRECATED", "production") == DENIED


def test_production_is_the_only_stage_permitted_in_production_mode():
    permitted = sorted(
        stage for stage in STAGES if cell(stage, "production") != DENIED
    )
    assert permitted == ["PRODUCTION"]


def test_no_authorization_reaches_a_denied_cell(gate):
    """A recorded authorization is not a skeleton key."""
    for stage, mode in EXPECTED:
        if EXPECTED[(stage, mode)] != "N":
            continue
        ceiling = CEILINGS[stage]
        decision = gate(
            gate_request(
                record=gate_record(
                    status=stage, routing_eligible=ceiling[0], production_eligible=ceiling[1]
                ),
                execution_mode=mode,
                certification_authorization_ref="cert-auth-0001",
            )
        )
        assert decision.verdict == BLOCK, (stage, mode)


def test_the_ceilings_agree_with_the_matrix():
    """A stage permitted in ``production`` must permit ``production_eligible``.

    The two halves of section 3 are separate columns of one table, and this is
    the consistency the table asserts between them.
    """
    for stage in STAGES:
        routing_ceiling, production_ceiling = CEILINGS[stage]
        if cell(stage, "production") != DENIED:
            assert production_ceiling is True, stage
        if cell(stage, "non_production") != DENIED:
            assert routing_ceiling is True, stage
        if production_ceiling:
            assert routing_ceiling is True, stage


def test_an_unlisted_pair_is_denied_rather_than_permitted():
    assert cell("INVENTED_STAGE", "production") == DENIED
    assert cell("VALIDATED", "invented_mode") == DENIED
