"""Build the Runtime Catalog, and render its rejection report.

The whole pipeline, in order:

.. code-block:: text

    GitHub architecture truth
        -> pinned source SHAs        (manifest.py, integrity enforced)
        -> deterministic load        (loader.py, no filesystem enumeration)
        -> projection / rejection    (projection.py, fail closed, no repair)
        -> immutable snapshot        (snapshot.py, catalog_version + hash)
        -> RK-02 / RK-04 consumers

Every failure that prevents a *catalog* raises and aborts. Every failure that
concerns a single *record* produces a rejection. There is no third outcome, and
in particular there is no partial catalog and no silent drop.
"""

from __future__ import annotations

import os
from collections import Counter
from typing import Any

from .authority import load_frozen_authority
from .loader import load_raw_records
from .manifest import load_manifest
from .projection import project
from .snapshot import RuntimeCatalog, build_snapshot

__all__ = ["artifacts", "build_catalog", "default_root", "rejection_report"]

#: The two artifacts committed to the repository, relative to its root.
CATALOG_ARTIFACT = "catalog/catalog.snapshot.json"
REJECTION_ARTIFACT = "catalog/rejection-report.json"


def default_root() -> str:
    """The repository root, derived from this file's location."""
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def build_catalog(
    root: str | None = None,
    manifest_path: str = "catalog/sources.manifest.json",
) -> RuntimeCatalog:
    """Build the immutable catalog from the pinned snapshot.

    Performs no network I/O and writes nothing. Re-running it on unchanged
    inputs reproduces the same ``catalog_version`` byte for byte.
    """
    resolved = default_root() if root is None else root
    manifest = load_manifest(resolved, manifest_path)
    authority = load_frozen_authority(manifest.read)
    admitted, rejected = project(load_raw_records(manifest), authority)
    return build_snapshot(manifest, authority, admitted, rejected)


def rejection_report(catalog: RuntimeCatalog) -> dict[str, Any]:
    """The machine-readable migration backlog.

    Every excluded record appears once, with its structured reasons, its source
    ref and its identity. Nothing is summarised away: the totals exist to make
    the shape of the backlog readable, not to replace the entries.

    The report is a *finding*, not a work order. Repairs belong in the
    repository that authored the record. The runtime never edits a source.
    """
    by_reason: Counter[str] = Counter()
    by_repo: Counter[str] = Counter()
    for record in catalog.rejections:
        by_repo[str(record["source_ref"]).split("@", 1)[0]] += 1
        for reason in record["reasons"]:
            by_reason[str(reason["code"])] += 1

    return {
        "algorithm_version": catalog.algorithm_version,
        "catalog_hash": catalog.catalog_hash,
        "catalog_version": catalog.catalog_version,
        "pinned_sources": [
            {
                "commit_sha": source["commit_sha"],
                "path": source["path"],
                "repo": source["repo"],
                "role": source["role"],
            }
            for source in catalog.to_document()["pinned_sources"]
        ],
        "records": catalog.to_document()["rejections"],
        "totals": {
            "admitted": catalog.admitted_count,
            "admitted_capabilities": len(catalog.capabilities),
            "admitted_skills": len(catalog.skills),
            "rejected": catalog.rejected_count,
            "rejected_by_reason": dict(sorted(by_reason.items())),
            "rejected_by_source_repo": dict(sorted(by_repo.items())),
        },
    }


def artifacts(catalog: RuntimeCatalog) -> dict[str, Any]:
    """The catalog artifacts that are committed to the repository.

    They are generated, and they are still reviewed. Committing them makes the
    projection itself reviewable in a pull request -- a change in admission
    behaviour shows up as a diff in the catalog, not only as a diff in the code
    that produced it -- and lets CI assert that the committed bytes still match a
    fresh build from the pins.
    """
    return {
        CATALOG_ARTIFACT: catalog.to_document(),
        REJECTION_ARTIFACT: rejection_report(catalog),
    }
