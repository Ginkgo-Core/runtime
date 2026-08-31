"""RK-08B-B -- the opt-in live smoke. Separated from the kernel on purpose.

Nothing in ``ginkgo_runtime`` reaches a network as a consequence of being
imported, and nothing in the test suite reaches one at all. This package is the
one place a *deliberate* live dispatch is arranged, and it is arranged the way a
live dispatch should be: refused by default, refused again unless every
pre-condition is named explicitly, and refused a third time if the pinned
workflow document on disk is not the one the pin describes.

The three gates are in :mod:`ginkgo_runtime.smoke.n8n_ping`. They are not
convenience checks -- they are the difference between a smoke test and an
accident against a production automation host.

Run ``python -m ginkgo_runtime.smoke preconditions`` to see what is and is not
satisfied without contacting anything.
"""

from __future__ import annotations

from .n8n_ping import (
    OPT_IN_VARIABLE,
    PING_INPUT,
    PONG_OUTPUT,
    SMOKE_BINDING_ID,
    SMOKE_CORRELATION_ID,
    SMOKE_IDEMPOTENCY_KEY,
    SMOKE_TOOL_ID,
    SMOKE_WORKFLOW_IDENTITY,
    SMOKE_WORKFLOW_PATH,
    SMOKE_WORKFLOW_REF,
    WORKFLOW_ARTIFACT,
    Precondition,
    SmokeRefused,
    check_preconditions,
    run_live_smoke,
    workflow_artifact_sha256,
)

__all__ = [
    "OPT_IN_VARIABLE",
    "PING_INPUT",
    "PONG_OUTPUT",
    "SMOKE_BINDING_ID",
    "SMOKE_CORRELATION_ID",
    "SMOKE_IDEMPOTENCY_KEY",
    "SMOKE_TOOL_ID",
    "SMOKE_WORKFLOW_IDENTITY",
    "SMOKE_WORKFLOW_PATH",
    "SMOKE_WORKFLOW_REF",
    "WORKFLOW_ARTIFACT",
    "Precondition",
    "SmokeRefused",
    "check_preconditions",
    "run_live_smoke",
    "workflow_artifact_sha256",
]
