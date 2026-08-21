"""The pinned source manifest, and the integrity gate in front of it.

GitHub is the source of truth for architecture (invariant 8), and the catalog is
a one-way projection of it. To be a projection *and* be reproducible offline, the
bytes the catalog reads are vendored into ``catalog/sources/`` at explicit commit
SHAs, and the manifest records, per file:

* ``repo`` and ``commit_sha`` -- the pin;
* ``blob_sha`` -- the git object id, which anyone can check against
  ``GET /repos/{repo}/contents/{path}?ref={commit_sha}`` without cloning;
* ``sha256`` -- the content digest the loader enforces on every build.

Three things follow, and they are the reason the manifest exists at all:

* the build performs **no network I/O**; the pin is data, not a fetch;
* a file that drifts from its pin is a :class:`SourceIntegrityError`, not a
  quietly different catalog;
* re-pinning is an explicit, reviewable commit that changes ``catalog_version``.

The manifest is also where the "no implicit join" rule is enforced structurally.
``providers.yaml``, ``tools.yaml`` and ``endpoints.yaml`` carry free-text
``capabilities[]`` tags in a namespace that is disjoint from real capability IDs,
and joining them by name would corrupt the catalog (``ai-stack#37``). Rather than
loading that data and remembering not to use it, the manifest refuses to pin it:
the bytes never enter the process.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass

from ..canonical import sha256_hex
from .errors import CatalogBuildError, SourceIntegrityError, SourceUnavailableError

__all__ = [
    "FORBIDDEN_SOURCE_BASENAMES",
    "PROJECTING_ROLES",
    "PinnedSource",
    "SourceManifest",
    "load_manifest",
]

MANIFEST_SCHEMA_VERSION = "ginkgo.runtime-catalog.source-manifest.v1"

#: Registry files whose ``capabilities[]`` tags are free text in a namespace
#: disjoint from capability IDs. Pinning one is a build error, not a lint note.
FORBIDDEN_SOURCE_BASENAMES = frozenset(
    {"providers.yaml", "tools.yaml", "endpoints.yaml"}
)

#: What a pinned source is for.
#:
#: ``authority``  a frozen contract the catalog admits *against*, never projects.
#: ``projected``  an upstream registry record set, pinned to an upstream commit.
#: ``kernel``     a record this repository itself owns.
#:
#: The third exists for exactly one thing: RK-09's reference descriptor.
#: ``system.*`` is a reserved kernel-diagnostics namespace, so the reference
#: skill is a kernel artifact rather than a business skill, and there is no
#: upstream commit to pin it to -- it lives at this repository's HEAD, which git
#: already pins. Its integrity is still enforced by the same ``sha256`` gate as
#: every other source, so it cannot drift unnoticed.
#:
#: A kernel source carries ``commit_sha: null``. That is the one relaxation, and
#: it is why the role is separate rather than reusing ``projected``: an upstream
#: source with no commit would be an unpinned projection, and that must stay
#: impossible.
VALID_ROLES = frozenset({"authority", "kernel", "projected"})

#: Roles that carry records into the catalog.
PROJECTING_ROLES = frozenset({"kernel", "projected"})

#: The two record classes the catalog projects. A capability record is a public
#: semantic contract; a skill descriptor is an internal implementation record
#: discriminated by ``kind``. They are admitted by different gates, because a
#: capability has no ``kind`` and never should.
VALID_RECORD_CLASSES = frozenset({"capability", "skill_descriptor"})


@dataclass(frozen=True)
class PinnedSource:
    """One file, pinned to one commit.

    ``record_class`` and ``collection_key`` describe *how* the file is read:
    ``collection_key`` names the top-level list the records live under, or is
    ``None`` when the whole document is one record. Keeping this in the manifest
    rather than in a lookup table inside the loader means the pinned set fully
    describes itself, and adding a source is a manifest review rather than a
    code change.
    """

    repo: str
    ref: str
    commit_sha: str
    role: str
    path: str
    snapshot_path: str
    blob_sha: str
    sha256: str
    size_bytes: int
    record_class: str | None
    collection_key: str | None

    @property
    def source_ref(self) -> str:
        """The provenance string carried on every projected record and rejection.

        A kernel-owned source has no upstream commit, so it names its content
        digest instead -- the identity that is actually pinned.
        """
        pin = self.commit_sha or "blob:%s" % self.blob_sha
        return "%s@%s:%s" % (self.repo, pin, self.path)

    def as_hash_input(self) -> dict[str, object]:
        return {
            "blob_sha": self.blob_sha,
            "collection_key": self.collection_key,
            "commit_sha": self.commit_sha,
            "path": self.path,
            "record_class": self.record_class,
            "ref": self.ref,
            "repo": self.repo,
            "role": self.role,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True)
class SourceManifest:
    """The pinned set, plus the reader that enforces its digests."""

    root: str
    sources: tuple[PinnedSource, ...]

    def by_path(self, path: str) -> PinnedSource:
        for source in self.sources:
            if source.path == path:
                return source
        raise SourceUnavailableError(
            "no pinned source for %s; the manifest does not declare it" % path
        )

    def projected(self) -> tuple[PinnedSource, ...]:
        """Every source that carries records, upstream or kernel-owned."""
        return tuple(s for s in self.sources if s.role in PROJECTING_ROLES)

    def read(self, path: str) -> bytes:
        """Return the pinned bytes for ``path``, failing closed on absence or drift."""
        source = self.by_path(path)
        return self.read_source(source)

    def read_source(self, source: PinnedSource) -> bytes:
        absolute = os.path.join(self.root, *source.snapshot_path.split("/"))
        try:
            with open(absolute, "rb") as handle:
                data = handle.read()
        except OSError as exc:
            raise SourceUnavailableError(
                "pinned source %s is unavailable at %s (%s)"
                % (source.source_ref, source.snapshot_path, exc)
            ) from exc

        digest = sha256_hex(data)
        if digest != source.sha256:
            raise SourceIntegrityError(
                "pinned source %s does not match its manifest digest "
                "(expected sha256 %s, found %s); the snapshot has drifted from "
                "the pin and the catalog will not project it"
                % (source.source_ref, source.sha256, digest)
            )
        if len(data) != source.size_bytes:
            raise SourceIntegrityError(
                "pinned source %s is %d bytes, manifest declares %d"
                % (source.source_ref, len(data), source.size_bytes)
            )
        return data

    def as_hash_input(self) -> list[dict[str, object]]:
        return [source.as_hash_input() for source in self.sources]


def _require(entry: Mapping[str, object], key: str, index: int) -> object:
    if key not in entry:
        raise CatalogBuildError("manifest source %d has no %r" % (index, key))
    return entry[key]


def load_manifest(
    root: str, manifest_path: str = "catalog/sources.manifest.json"
) -> SourceManifest:
    """Load and validate the manifest. Every defect here aborts the build."""
    absolute = os.path.join(root, *manifest_path.split("/"))
    try:
        with open(absolute, "rb") as handle:
            document = json.loads(handle.read().decode("utf-8"))
    except OSError as exc:
        raise SourceUnavailableError(
            "the source manifest is unavailable at %s (%s)" % (manifest_path, exc)
        ) from exc
    except ValueError as exc:
        raise CatalogBuildError("the source manifest is not valid JSON: %s" % exc) from exc

    if not isinstance(document, dict):
        raise CatalogBuildError("the source manifest is not a JSON object")
    if document.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise CatalogBuildError(
            "unsupported manifest schema_version %r; expected %r"
            % (document.get("schema_version"), MANIFEST_SCHEMA_VERSION)
        )

    raw = document.get("sources")
    if not isinstance(raw, list) or not raw:
        raise CatalogBuildError("the source manifest declares no sources")

    sources: list[PinnedSource] = []
    seen: set[tuple[str, str]] = set()
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise CatalogBuildError("manifest source %d is not an object" % index)

        repo = str(_require(entry, "repo", index))
        path = str(_require(entry, "path", index))
        role = str(_require(entry, "role", index))
        _require(entry, "commit_sha", index)
        commit_sha = "" if entry["commit_sha"] is None else str(entry["commit_sha"])

        if role not in VALID_ROLES:
            raise CatalogBuildError(
                "manifest source %d declares role %r; expected one of %s"
                % (index, role, sorted(VALID_ROLES))
            )
        if role == "kernel":
            if entry.get("commit_sha") is not None:
                raise CatalogBuildError(
                    "manifest source %d has role 'kernel' and must carry "
                    "commit_sha: null; a kernel source is pinned by this "
                    "repository's own history, not by an upstream commit" % index
                )
            commit_sha = ""
        elif len(commit_sha) != 40 or not all(c in "0123456789abcdef" for c in commit_sha):
            raise CatalogBuildError(
                "manifest source %d pins %r, which is not a full 40-hex commit SHA; "
                "a branch name or short SHA is not a pin" % (index, commit_sha)
            )
        basename = path.rsplit("/", 1)[-1]
        if basename in FORBIDDEN_SOURCE_BASENAMES:
            raise CatalogBuildError(
                "manifest source %d pins %s. Its capabilities[] tags are free text "
                "in a namespace disjoint from capability IDs; joining them by name "
                "would corrupt the catalog (ai-stack#37), so it is not pinnable."
                % (index, path)
            )
        key = (repo, path)
        if key in seen:
            raise CatalogBuildError(
                "manifest pins %s:%s more than once" % (repo, path)
            )
        seen.add(key)

        record_class = entry.get("record_class")
        collection_key = entry.get("collection_key")
        if role in PROJECTING_ROLES:
            if record_class not in VALID_RECORD_CLASSES:
                raise CatalogBuildError(
                    "manifest source %d is projected but declares record_class %r; "
                    "expected one of %s" % (index, record_class, sorted(VALID_RECORD_CLASSES))
                )
            if collection_key is not None and not isinstance(collection_key, str):
                raise CatalogBuildError(
                    "manifest source %d declares a non-string collection_key" % index
                )
        elif record_class is not None or collection_key is not None:
            raise CatalogBuildError(
                "manifest source %d has role 'authority' and must not declare a "
                "record_class or collection_key; an authority is admitted "
                "against, never projected" % index
            )

        sources.append(
            PinnedSource(
                repo=repo,
                ref=str(_require(entry, "ref", index)),
                commit_sha=commit_sha,
                role=role,
                path=path,
                snapshot_path=str(_require(entry, "snapshot_path", index)),
                blob_sha=str(_require(entry, "blob_sha", index)),
                sha256=str(_require(entry, "sha256", index)),
                size_bytes=int(_require(entry, "size_bytes", index)),  # type: ignore[arg-type]
                record_class=record_class,
                collection_key=collection_key,
            )
        )

    # Sorted here, once. Every downstream stage inherits the order, so no stage
    # can make the catalog depend on how the manifest happened to be written.
    sources.sort(key=lambda s: (s.repo, s.path))
    return SourceManifest(root=root, sources=tuple(sources))
