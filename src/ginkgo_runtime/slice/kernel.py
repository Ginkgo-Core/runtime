"""Assembling the whole kernel for one reference run.

Every component the slice needs, wired once, with every non-deterministic input
supplied rather than sampled. Nothing here is a test double except the policy
stand-in, and that one evaluates nothing -- RK-04 makes the lifecycle, approval
and gate decisions; the supplied verdict is only the *business* policy answer,
which RK-04 records and never derives.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..bus import CapabilityBus
from ..catalog.build import build_catalog, default_root
from ..catalog.manifest import load_manifest
from ..catalog.snapshot import RuntimeCatalog
from ..clock import RuntimeClock, SteppingClock
from ..contracts import FrozenContracts, load_frozen_contracts
from ..evidence import EvidenceLedger
from ..executor import InProcessExecutor
from ..router import DomainRouter, PolicyVerdict, StaticPolicy
from .runner import KernelDownstream, SliceRun

__all__ = ["CERTIFICATION_AUTHORIZATION_REF", "ReferenceKernel", "build_reference_kernel"]

#: `slice/certification-authorization.yaml`. The recorded authorization the
#: ladder requires for a "permitted, governed" run: it names the skill, the mode
#: and the gate the run exists to satisfy, and it promotes nothing.
CERTIFICATION_AUTHORIZATION_REF = "cert-auth-rk-reference-echo-0001"

#: The policy the slice runs under. It permits, and it declares that it permits
#: nothing about providers -- there are none to permit.
SLICE_POLICY_SNAPSHOT: Mapping[str, Any] = {
    "id": "rk-reference-slice-policy",
    "network": "none",
    "note": "kernel diagnostics namespace; grants no business authority",
    "rules": [],
}


@dataclass(frozen=True)
class ReferenceKernel:
    """The assembled kernel, plus the pieces a test needs to inspect."""

    bus: CapabilityBus
    downstream: KernelDownstream
    catalog: RuntimeCatalog
    contracts: FrozenContracts
    evidence: EvidenceLedger
    clock: RuntimeClock

    def submit(self, request: Mapping[str, Any]) -> SliceRun:
        """One request, all the way through, with every artifact it produced."""
        before = len(self.downstream.decisions)
        outcome = self.bus.submit(request)

        decision = (
            self.downstream.decisions[-1]
            if len(self.downstream.decisions) > before
            else None
        )
        execution = self.downstream.executions[-1] if self.downstream.executions else None
        packet = self.evidence.packet_for(request) if isinstance(request, Mapping) else None
        if decision is None:
            # A replay or a rejection: no fresh dispatch happened, so no fresh
            # decision and no fresh execution belong to this run.
            execution = None
        return SliceRun(
            request=request,
            outcome=outcome,
            decision=decision,
            execution=execution,
            packet=packet,
        )


def build_reference_kernel(
    *,
    root: str | None = None,
    execution_mode: str = "certification",
    certification_authorization_ref: str | None = CERTIFICATION_AUTHORIZATION_REF,
    policy_decision: str = "ALLOW",
    approval_required: bool = False,
    approval_state: str = "NOT_REQUIRED",
    external_gates: tuple[Mapping[str, Any], ...] = (),
    clock: RuntimeClock | None = None,
    catalog: RuntimeCatalog | None = None,
) -> ReferenceKernel:
    """Wire RK-07, RK-01, RK-02, RK-04, RK-08A, RK-09 and RK-06 together.

    The keyword arguments exist so the negative fixtures can vary exactly one
    thing at a time: the mode, the policy verdict, the approval state, the gate
    state. Everything else is fixed, so a difference in outcome is attributable.
    """
    resolved_root = default_root() if root is None else root
    contracts = load_frozen_contracts(load_manifest(resolved_root).read)
    runtime_clock = SteppingClock() if clock is None else clock
    catalog = build_catalog(resolved_root) if catalog is None else catalog

    policy = StaticPolicy(
        verdict=PolicyVerdict(
            policy_id="rk-reference-slice-policy",
            policy_version="1",
            decision=policy_decision,
            snapshot=SLICE_POLICY_SNAPSHOT,
            approval_required=approval_required,
            approval_state=approval_state,
            external_gates=external_gates,
        )
    )

    downstream = KernelDownstream(
        router=DomainRouter(catalog=catalog, policy=policy, contracts=contracts),
        executor=InProcessExecutor(contracts=contracts, clock=runtime_clock),
        evidence=EvidenceLedger(contracts=contracts, clock=runtime_clock),
        contracts=contracts,
        clock=runtime_clock,
        execution_mode=execution_mode,
        certification_authorization_ref=certification_authorization_ref,
    )
    return ReferenceKernel(
        bus=CapabilityBus(downstream=downstream, contracts=contracts),
        downstream=downstream,
        catalog=catalog,
        contracts=contracts,
        evidence=downstream.evidence,
        clock=runtime_clock,
    )
