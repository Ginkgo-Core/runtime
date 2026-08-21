"""The immutable catalog snapshot, and the version that identifies it.

RK-07's output is an in-process value, not a service and not a store. Once built
it cannot be mutated: the container is a frozen dataclass, its collections are
tuples, and every projected mapping is wrapped in ``MappingProxyType``. The
architecture's phrasing is the requirement -- *"GitHub registry metadata must
never masquerade as mutable runtime state"* -- and a snapshot that could be
written to would be exactly that masquerade.

``catalog_version`` identifies the whole projection. Its hash input is:

* the algorithm version, so a change in projection logic is visible;
* every frozen authority value, so re-pinning ``ai-stack`` to a commit with a
  different capability pattern produces a different catalog even if no record
  changed;
* every pinned source **the catalog read**, by repo, commit SHA, blob id and
  content digest -- not every source the repository pins. Other kernel components
  pin their own frozen contracts into the same manifest, and one of them adding a
  schema it validates against is not a change in what the catalog means;
* every admitted record;
* **every rejection.** A record moving between admitted and rejected changes what
  the catalog means, so it must change the version. Hashing only the admitted
  set would let the migration backlog change silently.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from ..canonical import hash_canonical
from .authority import FrozenAuthority
from .manifest import SourceManifest
from .projection import AdmittedRecord, RejectedRecord

__all__ = ["ALGORITHM_VERSION", "RuntimeCatalog", "build_snapshot", "freeze", "thaw"]

#: Bump this when the projection's meaning changes. It is part of the hash, so a
#: silent change in admission behaviour cannot reuse an old catalog_version.
ALGORITHM_VERSION = "ginkgo.runtime-catalog.v1"

#: Prefix on ``catalog_version``: Runtime Catalog, contract v1.
VERSION_PREFIX = "rkc1"

#: Characters of the hash carried in ``catalog_version``. The full digest is
#: always available as ``catalog_hash``; the short form is for logs and refs.
VERSION_DIGEST_CHARS = 16


def freeze(value: Any) -> Any:
    """Recursively convert mappings to read-only views and sequences to tuples."""
    if isinstance(value, Mapping):
        return MappingProxyType({key: freeze(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(freeze(item) for item in value)
    return value


def thaw(value: Any) -> Any:
    """The inverse of :func:`freeze`, for serialisation and hashing."""
    if isinstance(value, Mapping):
        return {key: thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw(item) for item in value]
    return value


@dataclass(frozen=True)
class RuntimeCatalog:
    """An immutable projection of GitHub architecture truth at pinned commits.

    It reads, validates, normalises, projects, rejects and versions. It does not
    route, does not choose a provider or an executor, does not execute, holds no
    mutable state, and never consults a registry during a request.
    """

    catalog_version: str
    catalog_hash: str
    algorithm_version: str
    pinned_sources: tuple[Mapping[str, Any], ...]
    authority: Mapping[str, Any]
    capabilities: tuple[Mapping[str, Any], ...]
    skills: tuple[Mapping[str, Any], ...]
    rejections: tuple[Mapping[str, Any], ...]

    # -- read-only access ------------------------------------------------

    def capability(self, capability_id: str) -> Mapping[str, Any] | None:
        """Resolve a capability record by ``id``. Never by filename."""
        for record in self.capabilities:
            if record["record_id"] == capability_id:
                return record
        return None

    def skill(self, record_id: str) -> Mapping[str, Any] | None:
        """Resolve a skill/binding record by ``id``. Never by filename."""
        for record in self.skills:
            if record["record_id"] == record_id:
                return record
        return None

    def skills_for_capability(self, capability_name: str) -> tuple[Mapping[str, Any], ...]:
        """Every admitted descriptor declaring this capability name.

        This is a **lookup over the projection**, not a routing decision. It
        applies no eligibility, ranks nothing and returns no preference. Choosing
        among these is the Domain Router's sole authority (invariant 4).
        """
        return tuple(
            record
            for record in self.skills
            if isinstance(record.get("capability"), Mapping)
            and record["capability"].get("name") == capability_name
        )

    def __iter__(self) -> Iterator[Mapping[str, Any]]:
        return iter(self.capabilities + self.skills)

    @property
    def admitted_count(self) -> int:
        return len(self.capabilities) + len(self.skills)

    @property
    def rejected_count(self) -> int:
        return len(self.rejections)

    # -- serialisation ---------------------------------------------------

    def to_document(self) -> dict[str, Any]:
        """The catalog as plain data, suitable for canonical JSON."""
        return {
            "algorithm_version": self.algorithm_version,
            "authority": thaw(self.authority),
            "capabilities": [thaw(record) for record in self.capabilities],
            "catalog_hash": self.catalog_hash,
            "catalog_version": self.catalog_version,
            "pinned_sources": [thaw(source) for source in self.pinned_sources],
            "rejections": [thaw(record) for record in self.rejections],
            "skills": [thaw(record) for record in self.skills],
        }


def build_snapshot(
    manifest: SourceManifest,
    authority: FrozenAuthority,
    admitted: tuple[AdmittedRecord, ...],
    rejected: tuple[RejectedRecord, ...],
    consumed: Iterable[str],
) -> RuntimeCatalog:
    """Assemble the immutable snapshot and compute its version.

    ``consumed`` is the set of manifest paths this build actually read. Only those
    reach ``pinned_sources`` and the hash.
    """
    read = set(consumed)
    capabilities = [r.as_dict() for r in admitted if r.record_class == "capability"]
    skills = [r.as_dict() for r in admitted if r.record_class == "skill_descriptor"]
    rejections = [r.as_dict() for r in rejected]
    pinned = [entry for entry in manifest.as_hash_input() if entry["path"] in read]
    authority_view = authority.as_hash_input()

    catalog_hash = hash_canonical(
        {
            "admitted": {"capabilities": capabilities, "skills": skills},
            "algorithm_version": ALGORITHM_VERSION,
            "authority": authority_view,
            "rejected": rejections,
            "sources": pinned,
        }
    )
    catalog_version = "%s-%s" % (VERSION_PREFIX, catalog_hash[:VERSION_DIGEST_CHARS])

    return RuntimeCatalog(
        catalog_version=catalog_version,
        catalog_hash=catalog_hash,
        algorithm_version=ALGORITHM_VERSION,
        pinned_sources=freeze(pinned),
        authority=freeze(authority_view),
        capabilities=freeze(capabilities),
        skills=freeze(skills),
        rejections=freeze(rejections),
    )
