"""Shared fixtures.

Two kinds of test live in this suite and they prove different things:

* tests over the **real pinned corpus** prove what the catalog actually does to
  the three registries as they stand at the pinned commits;
* tests over a **synthetic snapshot** prove one rule in isolation, including
  rules no current record triggers. Those are built with :func:`make_snapshot`,
  which writes a manifest and its files exactly the way the real one is written,
  so a synthetic root exercises the same loader, the same integrity gate and the
  same projection.

Both are offline. Neither reads or writes anything outside the test's own
directory and the repository's read-only snapshot.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil

import pytest

from ginkgo_runtime.catalog.authority import (
    CAPABILITY_REQUEST_SCHEMA,
    SKILL_DESCRIPTOR_SCHEMA,
)
from ginkgo_runtime.catalog.build import build_catalog, default_root

REPO_ROOT = default_root()

#: The two frozen schemas every build needs, and where they sit in the snapshot.
AUTHORITY_SOURCES = (
    ("Ginkgo-Core/ai-stack", CAPABILITY_REQUEST_SCHEMA, "ai-stack"),
    ("Ginkgo-Core/ai-stack", SKILL_DESCRIPTOR_SCHEMA, "ai-stack"),
)

FAKE_COMMIT_A = "a" * 40
FAKE_COMMIT_B = "b" * 40


def git_blob_sha(data: bytes) -> str:
    """The git object id for ``data``, the same value the GitHub API reports."""
    header = b"blob %d\0" % len(data)
    return hashlib.sha1(header + data).hexdigest()


def _entry(repo, path, slug, role, record_class, collection_key, commit_sha, data):
    return {
        "blob_sha": git_blob_sha(data),
        "collection_key": collection_key,
        "commit_sha": commit_sha,
        "path": path,
        "record_class": record_class,
        "ref": "main",
        "repo": repo,
        "role": role,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
        "snapshot_path": "catalog/sources/%s/%s" % (slug, path),
    }


def make_snapshot(root, sources, commit_sha=FAKE_COMMIT_A, include_authority=True):
    """Write a manifest and its pinned files under ``root``. Returns ``str(root)``.

    ``sources`` is a list of ``(path, record_class, collection_key, text)``
    tuples, all attributed to ``Ginkgo-Core/ginkgo-agents`` unless the path is
    given as a ``(repo, slug, path)`` triple.
    """
    root = str(root)
    entries = []

    if include_authority:
        for repo, path, slug in AUTHORITY_SOURCES:
            origin = os.path.join(REPO_ROOT, "catalog", "sources", slug, *path.split("/"))
            with open(origin, "rb") as handle:
                data = handle.read()
            destination = os.path.join(root, "catalog", "sources", slug, *path.split("/"))
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            shutil.copyfile(origin, destination)
            entries.append(
                _entry(repo, path, slug, "authority", None, None, commit_sha, data)
            )

    for source in sources:
        path, record_class, collection_key, text = source
        repo, slug = "Ginkgo-Core/ginkgo-agents", "ginkgo-agents"
        data = text.encode("utf-8") if isinstance(text, str) else text
        destination = os.path.join(root, "catalog", "sources", slug, *path.split("/"))
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        with open(destination, "wb") as handle:
            handle.write(data)
        entries.append(
            _entry(repo, path, slug, "projected", record_class, collection_key, commit_sha, data)
        )

    entries.sort(key=lambda e: (e["repo"], e["path"]))
    manifest_path = os.path.join(root, "catalog", "sources.manifest.json")
    os.makedirs(os.path.dirname(manifest_path), exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(
            {
                "schema_version": "ginkgo.runtime-catalog.source-manifest.v1",
                "description": "synthetic snapshot for tests",
                "sources": entries,
            },
            handle,
            indent=2,
            sort_keys=True,
        )
        handle.write("\n")
    return root


def one_record(tmp_path, yaml_text, **kwargs):
    """Build a catalog whose only projected source is one skill descriptor."""
    root = make_snapshot(
        tmp_path, [("features/record.yaml", "skill_descriptor", None, yaml_text)], **kwargs
    )
    return build_catalog(root)


@pytest.fixture(scope="session")
def catalog():
    """The real catalog, built from the repository's pinned snapshot."""
    return build_catalog(REPO_ROOT)


@pytest.fixture(scope="session")
def report(catalog):
    from ginkgo_runtime.catalog.build import rejection_report

    return rejection_report(catalog)
