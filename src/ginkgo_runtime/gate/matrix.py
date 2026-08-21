"""The canonical lifecycle x execution_mode matrix, transcribed cell by cell.

``lifecycle-and-promotion.md`` section 3 is *"the single normative source. Where
any other document in this branch disagrees, this table wins."* It is prose, not
a value in any schema, so it is transcribed here with its citation -- the same
treatment RK-07 gives the ceiling matrix and RK-01 gives the forbidden-field list.

The table, verbatim in its three mode columns:

======================  ===============  ==================  ============
Stage                   ``certification``  ``non_production``  ``production``
======================  ===============  ==================  ============
``FEATURE``             permitted, governed  no                no
``PROTOTYPE``           permitted, governed  no                no
``VALIDATED``           permitted            permitted         no
``INTEGRATED``          permitted            permitted         no
``PRODUCTION``          permitted            permitted         yes
``DEPRECATED``          permitted            no                no
``REJECTED``            no                   no                no
======================  ===============  ==================  ============

Section 3 defines the three values, and the definitions carry the whole design:

    **permitted** means allowed subject to the ordinary policy gate, and
    **permitted, governed** means additionally requiring a recorded authorization
    that names the skill, the mode and the gate the run exists to satisfy.
    **no** means no ``ROUTED`` decision in that mode may select the record, **at
    any stage of governance**.

That last clause is why ``REJECTED`` and the two caller-traffic cells of
``DEPRECATED`` cannot be reached by any amount of authorization: "no" is not "no
unless someone signs for it".

The stage also bounds the two eligibility booleans -- but bounds them, and does
not set them. Section 3 again: *"It states the maximum each stage permits, not an
identity. A record may be more restrictive than its stage allows, and often is."*
So :data:`CEILINGS` is a ceiling and never a grant: a ``VALIDATED`` record that
declares ``routing_eligible: false`` is genuinely not routable, and the gate must
honour the record over the stage.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

__all__ = [
    "CALLER_TRAFFIC_MODES",
    "CEILINGS",
    "DENIED",
    "MATRIX",
    "PERMITTED",
    "PERMITTED_GOVERNED",
    "cell",
]

#: Allowed, subject to the ordinary policy gate.
PERMITTED = "permitted"

#: Allowed, and additionally requiring a recorded authorization naming the skill,
#: the mode and the gate the run exists to satisfy.
PERMITTED_GOVERNED = "permitted_governed"

#: No ``ROUTED`` decision in this mode may select the record, at any stage of
#: governance. Not overridable by an authorization, an approval or a policy.
DENIED = "denied"

#: The modes that serve caller traffic, and therefore consult the eligibility
#: flags. ``certification`` is excluded deliberately: it is the governed,
#: evidence-producing mode a record uses *before* it is routable, and gating it on
#: ``routing_eligible`` would recreate the circularity where a record needs the
#: certificate to become routable and needs to run to earn the certificate.
CALLER_TRAFFIC_MODES = ("non_production", "production")

#: Section 3, cell by cell. Seven stages by three modes; every cell is stated
#: rather than derived, so a reader can check it against the table directly and a
#: test can walk all twenty-one.
#: Read-only. A plain dict here would let any in-process caller rewrite a cell
#: and change what the gate permits, globally and silently -- which a component
#: claiming purity must not leave open.
MATRIX: Mapping[tuple[str, str], str] = MappingProxyType({
    ("FEATURE", "certification"): PERMITTED_GOVERNED,
    ("FEATURE", "non_production"): DENIED,
    ("FEATURE", "production"): DENIED,
    ("PROTOTYPE", "certification"): PERMITTED_GOVERNED,
    ("PROTOTYPE", "non_production"): DENIED,
    ("PROTOTYPE", "production"): DENIED,
    ("VALIDATED", "certification"): PERMITTED,
    ("VALIDATED", "non_production"): PERMITTED,
    ("VALIDATED", "production"): DENIED,
    ("INTEGRATED", "certification"): PERMITTED,
    ("INTEGRATED", "non_production"): PERMITTED,
    ("INTEGRATED", "production"): DENIED,
    ("PRODUCTION", "certification"): PERMITTED,
    ("PRODUCTION", "non_production"): PERMITTED,
    ("PRODUCTION", "production"): PERMITTED,
    ("DEPRECATED", "certification"): PERMITTED,
    ("DEPRECATED", "non_production"): DENIED,
    ("DEPRECATED", "production"): DENIED,
    ("REJECTED", "certification"): DENIED,
    ("REJECTED", "non_production"): DENIED,
    ("REJECTED", "production"): DENIED,
})

#: ``(routing_eligible, production_eligible)`` maxima per stage, from the same
#: table's two flag columns. A ceiling, never a grant.
CEILINGS: Mapping[str, tuple[bool, bool]] = MappingProxyType({
    "FEATURE": (False, False),
    "PROTOTYPE": (False, False),
    "VALIDATED": (True, False),
    "INTEGRATED": (True, False),
    "PRODUCTION": (True, True),
    "DEPRECATED": (False, False),
    "REJECTED": (False, False),
})


def cell(status: str, execution_mode: str) -> str:
    """The matrix value for one stage in one mode.

    An unlisted combination returns :data:`DENIED` rather than raising. The gate
    checks both vocabularies against their frozen enums first and reports the
    unknown precisely, so reaching here with an unlisted pair means something got
    past that check -- and a missing cell must never read as permission.
    """
    return MATRIX.get((status, execution_mode), DENIED)
