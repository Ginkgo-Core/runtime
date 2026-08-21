"""The E2E certificate: what the run proved, and what it did not.

`ai-stack#38`: ``automatic_promotion: false``. This document is evidence for a
promotion decision, never the decision. ``reference-slice.md`` section 6 is
explicit about the limits, and they are recorded here rather than left to be
inferred from a green result.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..canonical import fingerprint
from ..evidence import NEGATIVE_PROOFS
from .kernel import CERTIFICATION_AUTHORIZATION_REF, ReferenceKernel
from .runner import SliceRun

__all__ = ["CERTIFICATE_ARTIFACT", "build_certificate", "certificate_artifact"]

CERTIFICATE_ARTIFACT = "slice/e2e-certificate.json"

#: Verbatim from ``reference-slice.md`` section 6. Stated so no promotion claim
#: outruns the run.
PROVES_NOTHING_ABOUT = (
    "any provider, adapter, credential or network path",
    "n8n, ComfyUI, Pyre or any executor",
    "cross-process teardown -- RK-09 is in-process, which is why RK-08B is Wave 2",
)


def build_certificate(kernel: ReferenceKernel, run: SliceRun) -> dict[str, Any]:
    """The certificate document for one completed reference run."""
    decision = run.decision or {}
    packet = run.packet
    evidence = dict(packet.evidence) if packet else {}

    return {
        "certificate_version": "ginkgo.runtime-e2e-certificate.v1",
        "capability": dict(run.request["capability"]),
        "chain": [
            "RK-01 Capability Bus",
            "RK-02 Domain Router",
            "RK-04 Policy + Lifecycle Gate",
            "RK-08A Executor Contract",
            "RK-09 rk-reference-echo / rk-echo-transform",
            "RK-06 Evidence Writer",
        ],
        "resolved": {
            "skill_id": decision.get("resolved", {}).get("skill_id"),
            "skill_version": decision.get("resolved", {}).get("skill_version"),
            "tool_id": decision.get("resolved", {}).get("tool_id"),
            "adapter_id": decision.get("resolved", {}).get("adapter_id"),
            "adapter_not_applicable_reason": decision.get("resolved", {}).get(
                "adapter_not_applicable_reason"
            ),
            "provider": decision.get("resolved", {}).get("provider"),
            "provider_not_applicable_reason": decision.get("resolved", {}).get(
                "provider_not_applicable_reason"
            ),
        },
        "execution_mode": decision.get("execution_mode"),
        "certification_authorization_ref": CERTIFICATION_AUTHORIZATION_REF,
        "lifecycle": dict(decision.get("lifecycle") or {}),
        "status": run.status,
        "catalog_version": decision.get("catalog", {}).get("catalog_version"),
        "pinned_refs": [dict(ref) for ref in decision.get("catalog", {}).get("pinned_refs", [])],
        "algorithm_versions": {
            "domain_router": decision.get("algorithm_version"),
        },
        "hashes": {
            "route_decision_hash": decision.get("decision_hash"),
            "decision_inputs_hash": decision.get("decision_inputs_hash"),
            "input_fingerprint": decision.get("input_fingerprint"),
            "policy_snapshot_hash": decision.get("policy", {}).get("policy_snapshot_hash"),
            "evidence_integrity_sha256": evidence.get("integrity", {}).get("sha256"),
        },
        "evidence": {
            "evidence_id": evidence.get("evidence_id"),
            "kind": evidence.get("kind"),
            "record_count": kernel.evidence.record_count,
            "event_count": len(packet.events) if packet else 0,
            "event_types": [str(e["event_type"]) for e in packet.events] if packet else [],
            "knowledge_promotion": dict(evidence.get("knowledge_promotion") or {}),
        },
        "negative_proofs": dict(NEGATIVE_PROOFS),
        "artifact_fingerprints": run.artifact_fingerprints(),
        "promotion": {
            "automatic_promotion": False,
            "lifecycle_unchanged": decision.get("lifecycle", {}).get("status"),
            "note": (
                "This certificate is evidence for a promotion decision and is not "
                "that decision. FEATURE -> PROTOTYPE requires the fifteen gates in "
                "lifecycle-and-promotion.md section 6 and a recorded human decision."
            ),
        },
        "proves_nothing_about": list(PROVES_NOTHING_ABOUT),
    }


def certificate_artifact(certificate: Mapping[str, Any]) -> dict[str, Any]:
    """The committed form, with its own digest so a diff is attributable."""
    body = dict(certificate)
    body["certificate_hash"] = fingerprint(body)
    return body
