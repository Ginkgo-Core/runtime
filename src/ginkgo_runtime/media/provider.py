"""Internal media-provider protocol.

The protocol is intentionally transport-neutral and provider-neutral. It is
consumed only after the Capability Bus / Domain Router has authorized an
internal binding. Callers never use this API to choose a provider or model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable


@dataclass(frozen=True, slots=True)
class MediaProviderHealth:
    """Observed provider health; observation is not routing authority."""

    healthy: bool
    detail: str | None = None
    observed_at: str | None = None


@dataclass(frozen=True, slots=True)
class MediaCapabilityObservation:
    """One semantic operation observed behind a provider adapter."""

    capability: str
    available: bool = True
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class MediaModelObservation:
    """Provider/model metadata discovered at runtime."""

    model_id: str
    operations: tuple[str, ...]
    modalities: tuple[str, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MediaSubmitRequest:
    """Already-routed internal execution request.

    `provider_id`, `adapter_id` and routing policy are deliberately absent. The
    router owns those decisions. `model_id` is optional internal implementation
    metadata supplied by the authorized binding/runtime, never caller authority.
    """

    capability: str
    correlation_id: str
    inputs: Mapping[str, Any]
    model_id: str | None = None
    request_id: str | None = None
    execution_id: str | None = None


@dataclass(frozen=True, slots=True)
class MediaJobHandle:
    job_id: str
    provider_job_ref: str | None = None


@dataclass(frozen=True, slots=True)
class MediaJobStatus:
    job_id: str
    status: str
    progress: float | None = None
    output_refs: tuple[str, ...] = ()
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MediaUpload:
    asset_ref: str
    uri: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class MediaProvider(Protocol):
    """Structural contract implemented by local and cloud media adapters."""

    provider_id: str

    def health(self) -> MediaProviderHealth:
        """Return a bounded health observation."""

    def discover(self) -> Mapping[str, Any]:
        """Return provider discovery metadata without granting eligibility."""

    def capabilities(self) -> Sequence[MediaCapabilityObservation]:
        """Return observed semantic operations."""

    def models(self) -> Sequence[MediaModelObservation]:
        """Return discovered models/engines behind this adapter."""

    def submit(self, request: MediaSubmitRequest) -> MediaJobHandle:
        """Submit an already-authorized internal media request."""

    def status(self, job: MediaJobHandle) -> MediaJobStatus:
        """Return normalized job status."""

    def cancel(self, job: MediaJobHandle) -> bool:
        """Attempt cancellation and report whether it was accepted."""

    def upload(self, *, name: str, content: bytes, mime_type: str) -> MediaUpload:
        """Upload/ingest an asset through the provider boundary."""
