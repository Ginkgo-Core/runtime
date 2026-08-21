"""Deterministic loading of raw records out of the pinned snapshot.

The loader reads *exactly* the paths the manifest declares, in manifest order,
and takes records in document order within each file. It never walks a
directory and never globs.

That is the whole determinism argument, and it is deliberately structural rather
than a sorting step bolted on at the end: if the loader never asks the
filesystem to enumerate anything, filesystem enumeration order cannot reach the
catalog. ``os.walk``, ``os.listdir`` and ``glob`` do not appear in this package,
and a test asserts it.

The loader parses and attaches provenance. It admits nothing, normalises
nothing, and repairs nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .errors import CatalogBuildError
from .manifest import PinnedSource, SourceManifest

__all__ = ["RawRecord", "load_raw_records"]


@dataclass(frozen=True)
class RawRecord:
    """One record as authored, with enough provenance to name it in a report."""

    record_class: str
    source: PinnedSource
    #: Position within the source document. Part of the record's locator so a
    #: record with no usable ``id`` can still be pointed at precisely.
    index: int
    #: ``capabilities[3]`` or ``$`` for a whole-document record. This is a
    #: *locator*, never an identity: records are resolved by ``id:``.
    locator: str
    data: Any

    @property
    def source_ref(self) -> str:
        return self.source.source_ref

    @property
    def record_ref(self) -> str:
        return "%s#%s" % (self.source_ref, self.locator)

    def declared_id(self) -> str | None:
        if isinstance(self.data, dict):
            value = self.data.get("id")
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None


def _parse_yaml(data: bytes, source: PinnedSource) -> Any:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - environment defect, not a data defect
        raise CatalogBuildError(
            "PyYAML is required to read %s. The catalog refuses to guess at YAML "
            "with an ad-hoc parser: a parser bug would corrupt the projection "
            "silently." % source.path
        ) from exc
    try:
        return yaml.safe_load(data.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise CatalogBuildError(
            "pinned source %s is not valid UTF-8 (%s)" % (source.source_ref, exc)
        ) from exc
    except yaml.YAMLError as exc:
        raise CatalogBuildError(
            "pinned source %s is not parseable YAML (%s)" % (source.source_ref, exc)
        ) from exc


def load_raw_records(manifest: SourceManifest) -> tuple[RawRecord, ...]:
    """Read every projected source into raw records, in a fully determined order."""
    records: list[RawRecord] = []

    for source in manifest.projected():
        document = _parse_yaml(manifest.read_source(source), source)

        if source.collection_key is None:
            # The whole document is one record. An empty file is a build error:
            # the manifest promised a record here.
            if document is None:
                raise CatalogBuildError(
                    "pinned source %s is empty but the manifest declares one "
                    "record in it" % source.source_ref
                )
            records.append(
                RawRecord(
                    record_class=str(source.record_class),
                    source=source,
                    index=0,
                    locator="$",
                    data=document,
                )
            )
            continue

        if not isinstance(document, dict):
            raise CatalogBuildError(
                "pinned source %s is not a mapping, so its %r collection cannot "
                "be read" % (source.source_ref, source.collection_key)
            )
        collection = document.get(source.collection_key)
        if collection is None:
            raise CatalogBuildError(
                "pinned source %s does not carry the %r collection the manifest "
                "declares" % (source.source_ref, source.collection_key)
            )
        if not isinstance(collection, list):
            raise CatalogBuildError(
                "pinned source %s: %r is not a list"
                % (source.source_ref, source.collection_key)
            )

        for index, entry in enumerate(collection):
            records.append(
                RawRecord(
                    record_class=str(source.record_class),
                    source=source,
                    index=index,
                    locator="%s[%d]" % (source.collection_key, index),
                    data=entry,
                )
            )

    return tuple(records)
