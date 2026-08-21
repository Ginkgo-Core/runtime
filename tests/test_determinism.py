"""Determinism: same inputs, same catalog -- byte for byte.

The catalog is an identity, not just a value. RK-02 pins ``catalog_version``
into every routing decision it freezes, so two processes that believe they hold
"the same catalog" must be holding literally the same bytes. These tests are the
proof of that claim.
"""

from __future__ import annotations

import json
import os

import pytest

from ginkgo_runtime.canonical import canonical_json
from ginkgo_runtime.catalog.build import build_catalog

from .conftest import FAKE_COMMIT_A, FAKE_COMMIT_B, make_snapshot

RECORD = """
id: system.echo.reference
kind: deterministic_domain_skill
version: 1.0.0
lifecycle_status: FEATURE
capability:
  name: system.echo.execute
  contract_version: v1
fallback_policy: none
"""


def test_rebuild_is_byte_identical(catalog):
    """Same pinned inputs, twice, in one process."""
    again = build_catalog()
    assert canonical_json(catalog.to_document()) == canonical_json(again.to_document())
    assert catalog.catalog_hash == again.catalog_hash
    assert catalog.catalog_version == again.catalog_version


def test_rebuild_is_byte_identical_across_processes(tmp_path):
    """Same pinned inputs, twice, in two separate interpreter processes.

    An in-process rebuild can hide state that survives between builds. A cold
    process cannot, so this is the check that actually proves a restart
    re-derives the identical catalog.
    """
    import subprocess
    import sys

    from .conftest import REPO_ROOT

    script = (
        "import json,sys;"
        "sys.path.insert(0, %r);"
        "from ginkgo_runtime.canonical import canonical_json;"
        "from ginkgo_runtime.catalog.build import build_catalog;"
        "print(canonical_json(build_catalog(%r).to_document()))"
        % (os.path.join(REPO_ROOT, "src"), REPO_ROOT)
    )
    runs = [
        subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, check=True
        ).stdout
        for _ in range(2)
    ]
    assert runs[0] == runs[1]
    assert json.loads(runs[0])["catalog_version"].startswith("rkc1-")


def test_filesystem_order_cannot_reach_the_catalog(tmp_path, monkeypatch):
    """Reversing every directory listing changes nothing.

    The loader reads exactly the manifest's paths and never enumerates a
    directory, so this test is really asserting that property end to end rather
    than asserting that a sort was applied.
    """
    root = make_snapshot(
        tmp_path / "a",
        [
            ("features/one.yaml", "skill_descriptor", None, RECORD),
            (
                "features/two.yaml",
                "skill_descriptor",
                None,
                RECORD.replace("echo", "ping"),
            ),
        ],
    )
    baseline = build_catalog(root)

    real_listdir, real_scandir = os.listdir, os.scandir
    monkeypatch.setattr(os, "listdir", lambda p=".": list(reversed(real_listdir(p))))
    monkeypatch.setattr(
        os, "scandir", lambda p=".": iter(list(reversed(list(real_scandir(p)))))
    )
    shuffled = build_catalog(root)

    assert baseline.catalog_hash == shuffled.catalog_hash


def test_manifest_order_cannot_reach_the_catalog(tmp_path):
    """Writing the manifest's sources in a different order changes nothing."""
    root = make_snapshot(
        tmp_path,
        [
            ("features/one.yaml", "skill_descriptor", None, RECORD),
            (
                "features/two.yaml",
                "skill_descriptor",
                None,
                RECORD.replace("echo", "ping"),
            ),
        ],
    )
    baseline = build_catalog(root)

    path = os.path.join(root, "catalog", "sources.manifest.json")
    with open(path, encoding="utf-8") as handle:
        document = json.load(handle)
    document["sources"].reverse()
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(document, handle, indent=2)

    assert build_catalog(root).catalog_hash == baseline.catalog_hash


def test_a_different_source_sha_changes_catalog_version(tmp_path):
    """Re-pinning to another commit is a different catalog, even byte-identical content.

    The pin is part of the catalog's meaning: two runs that projected the same
    text from different commits did not observe the same architecture truth.
    """
    sources = [("features/one.yaml", "skill_descriptor", None, RECORD)]
    first = build_catalog(make_snapshot(tmp_path / "a", sources, commit_sha=FAKE_COMMIT_A))
    second = build_catalog(make_snapshot(tmp_path / "b", sources, commit_sha=FAKE_COMMIT_B))

    assert first.skills[0]["record_id"] == second.skills[0]["record_id"]
    assert first.catalog_version != second.catalog_version


def test_a_changed_record_changes_catalog_version(tmp_path):
    sources = [("features/one.yaml", "skill_descriptor", None, RECORD)]
    changed = [
        ("features/one.yaml", "skill_descriptor", None, RECORD.replace("FEATURE", "PROTOTYPE"))
    ]
    a = build_catalog(make_snapshot(tmp_path / "a", sources))
    b = build_catalog(make_snapshot(tmp_path / "b", changed))
    assert a.catalog_version != b.catalog_version


def test_a_changed_rejection_changes_catalog_version(tmp_path):
    """The migration backlog is part of the catalog's meaning, so it is hashed.

    Hashing only the admitted set would let the backlog change under a stable
    ``catalog_version``, which is exactly the silent drift the report exists to
    prevent.
    """
    broken = RECORD.replace("kind: deterministic_domain_skill\n", "")
    worse = broken.replace("fallback_policy: none", "fallback_policy: explicit")

    a = build_catalog(
        make_snapshot(tmp_path / "a", [("features/one.yaml", "skill_descriptor", None, broken)])
    )
    b = build_catalog(
        make_snapshot(tmp_path / "b", [("features/one.yaml", "skill_descriptor", None, worse)])
    )

    assert a.admitted_count == b.admitted_count == 0
    assert a.rejected_count == b.rejected_count == 1
    assert a.catalog_version != b.catalog_version


@pytest.mark.parametrize("field", ["catalog_version", "catalog_hash", "algorithm_version"])
def test_snapshot_identity_fields_are_present(catalog, field):
    assert getattr(catalog, field)


def test_catalog_version_is_derived_from_the_hash(catalog):
    assert catalog.catalog_version == "rkc1-%s" % catalog.catalog_hash[:16]
