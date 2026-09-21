from dataclasses import FrozenInstanceError

import pytest

from ginkgo_runtime.media import (
    MediaCapabilityObservation,
    MediaJobHandle,
    MediaJobStatus,
    MediaModelObservation,
    MediaProvider,
    MediaProviderHealth,
    MediaSubmitRequest,
    MediaUpload,
)


class DummyProvider:
    provider_id = "dummy-media"

    def health(self):
        return MediaProviderHealth(healthy=True)

    def discover(self):
        return {"runtime": "fixture"}

    def capabilities(self):
        return [MediaCapabilityObservation("media.image.generate")]

    def models(self):
        return [MediaModelObservation("dummy/model", ("generate",), ("image",))]

    def submit(self, request):
        return MediaJobHandle(job_id=f"job:{request.correlation_id}")

    def status(self, job):
        return MediaJobStatus(job_id=job.job_id, status="SUCCEEDED", progress=1.0)

    def cancel(self, job):
        return True

    def upload(self, *, name, content, mime_type):
        return MediaUpload(asset_ref=f"asset:{name}", uri=f"memory://{name}")


def test_structural_provider_protocol():
    provider = DummyProvider()
    assert isinstance(provider, MediaProvider)
    assert provider.health().healthy is True
    assert provider.capabilities()[0].capability == "media.image.generate"


def test_submit_request_is_immutable():
    request = MediaSubmitRequest(
        capability="media.image.generate",
        correlation_id="corr-1",
        inputs={"prompt": "test"},
    )
    with pytest.raises(FrozenInstanceError):
        request.capability = "media.video.generate"


def test_provider_normalizes_job_lifecycle():
    provider = DummyProvider()
    request = MediaSubmitRequest(
        capability="media.image.generate",
        correlation_id="corr-2",
        inputs={"prompt": "test"},
    )
    job = provider.submit(request)
    status = provider.status(job)
    assert status.status == "SUCCEEDED"
    assert status.progress == 1.0
