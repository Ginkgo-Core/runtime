"""Provider-agnostic media runtime primitives.

Architecture authority lives in Ginkgo-Core/ai-stack. This package contains
runtime interfaces only and does not authorize routing or provider selection.
"""

from .provider import (
    MediaCapabilityObservation,
    MediaJobHandle,
    MediaJobStatus,
    MediaModelObservation,
    MediaProvider,
    MediaProviderHealth,
    MediaSubmitRequest,
    MediaUpload,
)

__all__ = [
    "MediaCapabilityObservation",
    "MediaJobHandle",
    "MediaJobStatus",
    "MediaModelObservation",
    "MediaProvider",
    "MediaProviderHealth",
    "MediaSubmitRequest",
    "MediaUpload",
]
