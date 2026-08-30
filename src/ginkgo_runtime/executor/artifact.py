"""Executor-side identity and pinning for immutable workflow artifacts.

This is not a Runtime Catalog, a skill descriptor, or a caller contract. The
Executor Router resolves an opaque artifact pin before dispatch; the n8n adapter
can only verify that exact pin against its injected immutable descriptors.

The transport workflow reference is deliberately private to this object. It is
never placed on ``RuntimeExecutionRequest``, ``CapabilityRequest``, or the
normalized result.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..canonical import hash_canonical

__all__ = [
    "WorkflowArtifactDescriptor",
    "WorkflowArtifactError",
    "WorkflowArtifactPin",
]

_ARTIFACT_REF = re.compile(r"^[a-z][a-z0-9._:/-]{2,254}$")
_OPAQUE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,511}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")


class WorkflowArtifactError(ValueError):
    """A workflow descriptor or pin is malformed.

    Messages identify fields only. Values are omitted because configuration at
    this boundary must never become an accidental secret-printing surface.
    """


def _require(pattern: re.Pattern[str], value: object, field_name: str) -> None:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise WorkflowArtifactError("invalid workflow artifact %s" % field_name)


def _require_hash(value: object, field_name: str, *, optional: bool = False) -> None:
    if optional and value is None:
        return
    _require(_SHA256, value, field_name)


@dataclass(frozen=True)
class WorkflowArtifactPin:
    """The exact executor-side artifact identity authorized for one dispatch."""

    artifact_ref: str
    version: str
    content_sha256: str
    config_sha256: str | None = None

    def __post_init__(self) -> None:
        _require(_ARTIFACT_REF, self.artifact_ref, "artifact_ref")
        _require(_VERSION, self.version, "version")
        _require_hash(self.content_sha256, "content_sha256")
        _require_hash(self.config_sha256, "config_sha256", optional=True)


@dataclass(frozen=True)
class WorkflowArtifactDescriptor:
    """Immutable n8n workflow identity, version, hashes, and provenance.

    ``transport_workflow_ref`` is the raw executor-private identity understood by
    a transport. Marking it ``repr=False`` prevents routine diagnostics from
    printing it; normalized runtime output uses only ``artifact_ref`` and hashes.
    """

    artifact_ref: str
    version: str
    transport_workflow_ref: str = field(repr=False)
    content_sha256: str
    source_ref: str
    source_revision: str
    config_sha256: str | None = None

    def __post_init__(self) -> None:
        _require(_ARTIFACT_REF, self.artifact_ref, "artifact_ref")
        _require(_VERSION, self.version, "version")
        _require(_OPAQUE_REF, self.transport_workflow_ref, "transport_workflow_ref")
        _require_hash(self.content_sha256, "content_sha256")
        _require(_OPAQUE_REF, self.source_ref, "source_ref")
        _require(_OPAQUE_REF, self.source_revision, "source_revision")
        _require_hash(self.config_sha256, "config_sha256", optional=True)

    @property
    def pin(self) -> WorkflowArtifactPin:
        return WorkflowArtifactPin(
            artifact_ref=self.artifact_ref,
            version=self.version,
            content_sha256=self.content_sha256,
            config_sha256=self.config_sha256,
        )

    @property
    def provenance_fingerprint(self) -> str:
        """Hash the complete non-secret provenance record deterministically."""
        return hash_canonical(
            {
                "artifact_ref": self.artifact_ref,
                "config_sha256": self.config_sha256,
                "content_sha256": self.content_sha256,
                "source_ref": self.source_ref,
                "source_revision": self.source_revision,
                "version": self.version,
            }
        )
