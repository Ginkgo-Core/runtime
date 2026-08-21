"""RK-10 -- the runtime E2E certificate half.

`ai-stack#38` splits RK-10 in two: the *linter* half lives in ``ai-stack`` with
the contracts it checks (merged as `#47`), and the *E2E certificate* half lives
here, with the kernel it exercises.

This package assembles the whole kernel and runs one request through it, then
emits a certificate recording what was proven and -- just as importantly -- what
was not.

It promotes nothing. `#38`: ``automatic_promotion: false``.
"""

from __future__ import annotations

from .certificate import (
    CERTIFICATE_ARTIFACT,
    build_certificate,
    certificate_artifact,
)
from .kernel import ReferenceKernel, build_reference_kernel
from .runner import KernelDownstream, SliceRun

__all__ = [
    "CERTIFICATE_ARTIFACT",
    "KernelDownstream",
    "ReferenceKernel",
    "SliceRun",
    "build_certificate",
    "build_reference_kernel",
    "certificate_artifact",
]
