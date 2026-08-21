"""Fail closed: an unbuildable catalog raises, and never degrades into a partial one.

The distinction these tests defend is the one in ``errors.py``: a *record*
problem produces a rejection, a *catalog* problem produces an exception. A build
that cannot read its pins, or reads bytes that drifted from them, must not return
a smaller catalog that looks fine.
"""

from __future__ import annotations

import json
import os

import pytest

from ginkgo_runtime.catalog.build import build_catalog
from ginkgo_runtime.catalog.errors import (
    AuthorityUnavailableError,
    CatalogBuildError,
    SourceIntegrityError,
    SourceUnavailableError,
)

from .conftest import make_snapshot

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

SOURCES = [("features/one.yaml", "skill_descriptor", None, RECORD)]


def manifest_path(root):
    return os.path.join(root, "catalog", "sources.manifest.json")


def rewrite_manifest(root, mutate):
    path = manifest_path(root)
    with open(path, encoding="utf-8") as handle:
        document = json.load(handle)
    mutate(document)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(document, handle, indent=2)
    return root


# -- sources ---------------------------------------------------------------


def test_an_unavailable_source_fails_closed(tmp_path):
    """A pinned file that is not there aborts the build. No partial catalog."""
    root = make_snapshot(tmp_path, SOURCES)
    os.remove(os.path.join(root, "catalog", "sources", "ginkgo-agents", "features", "one.yaml"))

    with pytest.raises(SourceUnavailableError) as excinfo:
        build_catalog(root)
    assert "features/one.yaml" in str(excinfo.value)


def test_an_unavailable_manifest_fails_closed(tmp_path):
    root = make_snapshot(tmp_path, SOURCES)
    os.remove(manifest_path(root))
    with pytest.raises(SourceUnavailableError):
        build_catalog(root)


def test_a_source_that_drifted_from_its_pin_fails_closed(tmp_path):
    """Content that no longer matches the manifest digest is never projected."""
    root = make_snapshot(tmp_path, SOURCES)
    target = os.path.join(root, "catalog", "sources", "ginkgo-agents", "features", "one.yaml")
    with open(target, "a", encoding="utf-8") as handle:
        handle.write("\n# tampered\n")

    with pytest.raises(SourceIntegrityError) as excinfo:
        build_catalog(root)
    assert "does not match its manifest digest" in str(excinfo.value)


def test_an_unavailable_authority_schema_fails_closed(tmp_path):
    """A catalog that cannot read the freeze never reports that a record conforms to it."""
    root = make_snapshot(tmp_path, SOURCES)
    os.remove(
        os.path.join(
            root, "catalog", "sources", "ai-stack", "contracts", "core",
            "capability-request.schema.json",
        )
    )
    with pytest.raises(AuthorityUnavailableError):
        build_catalog(root)


def test_an_authority_schema_missing_the_frozen_pattern_fails_closed(tmp_path):
    """No literal fallback exists. The pattern is loaded or the build stops."""
    root = make_snapshot(tmp_path, SOURCES)
    target = os.path.join(
        root, "catalog", "sources", "ai-stack", "contracts", "core",
        "capability-request.schema.json",
    )
    with open(target, encoding="utf-8") as handle:
        schema = json.load(handle)
    del schema["properties"]["capability"]["properties"]["name"]["pattern"]
    data = json.dumps(schema).encode("utf-8")
    with open(target, "wb") as handle:
        handle.write(data)

    # Re-pin the digest so the integrity gate is not the thing that fires.
    import hashlib

    def repin(document):
        for entry in document["sources"]:
            if entry["path"].endswith("capability-request.schema.json"):
                entry["sha256"] = hashlib.sha256(data).hexdigest()
                entry["size_bytes"] = len(data)

    rewrite_manifest(root, repin)

    with pytest.raises(AuthorityUnavailableError) as excinfo:
        build_catalog(root)
    assert "refuses to substitute a literal" in str(excinfo.value)


# -- manifest --------------------------------------------------------------


def test_a_branch_name_is_not_a_pin(tmp_path):
    root = make_snapshot(tmp_path, SOURCES)

    def use_branch(document):
        document["sources"][0]["commit_sha"] = "main"

    rewrite_manifest(root, use_branch)
    with pytest.raises(CatalogBuildError) as excinfo:
        build_catalog(root)
    assert "not a pin" in str(excinfo.value)


def test_a_short_sha_is_not_a_pin(tmp_path):
    root = make_snapshot(tmp_path, SOURCES)
    rewrite_manifest(root, lambda d: d["sources"][0].update(commit_sha="a9943567"))
    with pytest.raises(CatalogBuildError):
        build_catalog(root)


def test_an_unknown_manifest_schema_version_fails_closed(tmp_path):
    root = make_snapshot(tmp_path, SOURCES)
    rewrite_manifest(root, lambda d: d.update(schema_version="something.else.v2"))
    with pytest.raises(CatalogBuildError):
        build_catalog(root)


def test_a_projected_source_must_declare_a_record_class(tmp_path):
    root = make_snapshot(tmp_path, SOURCES)

    def clear(document):
        for entry in document["sources"]:
            if entry["role"] == "projected":
                entry["record_class"] = None

    rewrite_manifest(root, clear)
    with pytest.raises(CatalogBuildError):
        build_catalog(root)


def test_an_authority_source_may_not_be_projected(tmp_path):
    root = make_snapshot(tmp_path, SOURCES)

    def promote(document):
        for entry in document["sources"]:
            if entry["role"] == "authority":
                entry["record_class"] = "skill_descriptor"

    rewrite_manifest(root, promote)
    with pytest.raises(CatalogBuildError) as excinfo:
        build_catalog(root)
    assert "admitted against, never projected" in str(excinfo.value)


def test_a_declared_collection_that_is_absent_fails_closed(tmp_path):
    """The manifest promised records here. An empty read is a build error, not zero records."""
    root = make_snapshot(
        tmp_path, [("registry/other.yaml", "skill_descriptor", "skills", "version: 1\n")]
    )
    with pytest.raises(CatalogBuildError) as excinfo:
        build_catalog(root)
    assert "does not carry the 'skills' collection" in str(excinfo.value)


def test_unparseable_yaml_fails_closed(tmp_path):
    root = make_snapshot(
        tmp_path, [("features/one.yaml", "skill_descriptor", None, "id: [unclosed\n")]
    )
    with pytest.raises(CatalogBuildError) as excinfo:
        build_catalog(root)
    assert "not parseable YAML" in str(excinfo.value)
