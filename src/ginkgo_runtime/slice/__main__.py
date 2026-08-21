"""CLI for the reference slice.

.. code-block:: text

    python -m ginkgo_runtime.slice run            # run it, print the summary
    python -m ginkgo_runtime.slice run --json     # the certificate
    python -m ginkgo_runtime.slice emit           # write slice/e2e-certificate.json
    python -m ginkgo_runtime.slice emit --check   # is the committed one current?

Exit codes
    0  the slice ran and produced exactly one evidence record
    1  the slice did not complete
    2  the committed certificate is stale

It promotes nothing. ``automatic_promotion: false``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from ..catalog.build import default_root
from .certificate import CERTIFICATE_ARTIFACT, build_certificate, certificate_artifact
from .kernel import build_reference_kernel

EXIT_OK = 0
EXIT_INCOMPLETE = 1
EXIT_STALE = 2

#: The one request the certificate is built from. Fixed, so the artifact is a
#: function of the kernel rather than of whoever ran it.
CERTIFIED_REQUEST = {
    "request_id": "req-slice-0001",
    "correlation_id": "corr-slice-0001",
    "caller": {
        "actor_id": "rk-10-certificate",
        "actor_type": "API",
        "tenant_id": "ginkgo",
        "workspace_id": "kernel-diagnostics",
    },
    "capability": {"name": "system.echo.execute", "contract_version": "v1"},
    "input": {"message": "hello from the reference slice"},
    "constraints": {"fallback_policy": "none"},
    "governance": {"approval_context": {}, "external_gate_context": {}},
    "evidence": {"required": True},
    "idempotency_key": "idem-slice-0001",
}


def _summary(certificate) -> str:
    resolved = certificate["resolved"]
    evidence = certificate["evidence"]
    lines = [
        "capability        %s %s"
        % (certificate["capability"]["name"], certificate["capability"]["contract_version"]),
        "skill             %s@%s" % (resolved["skill_id"], resolved["skill_version"]),
        "tool              %s" % resolved["tool_id"],
        "adapter           %s" % (resolved["adapter_id"] or "null (not applicable)"),
        "provider          %s" % (resolved["provider"] or "null (not applicable)"),
        "execution_mode    %s" % certificate["execution_mode"],
        "lifecycle         %s  routing_eligible=%s production_eligible=%s"
        % (
            certificate["lifecycle"].get("status"),
            certificate["lifecycle"].get("routing_eligible"),
            certificate["lifecycle"].get("production_eligible"),
        ),
        "status            %s" % certificate["status"],
        "evidence          %s  records=%d events=%d"
        % (evidence["evidence_id"], evidence["record_count"], evidence["event_count"]),
        "catalog_version   %s" % certificate["catalog_version"],
        "decision_hash     %s" % certificate["hashes"]["route_decision_hash"],
        "negative proofs   %s"
        % ", ".join("%s=%s" % kv for kv in sorted(certificate["negative_proofs"].items())),
        "promotion         automatic_promotion=false, lifecycle unchanged at %s"
        % certificate["promotion"]["lifecycle_unchanged"],
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ginkgo_runtime.slice")
    parser.add_argument("--root", default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    run_cmd = sub.add_parser("run", help="run the slice end to end")
    run_cmd.add_argument("--json", action="store_true", help="emit the certificate")

    emit_cmd = sub.add_parser("emit", help="write the committed certificate")
    emit_cmd.add_argument("--check", action="store_true", help="do not write; compare")

    args = parser.parse_args(argv)
    root = args.root or default_root()

    kernel = build_reference_kernel(root=root)
    run = kernel.submit(CERTIFIED_REQUEST)

    if run.status != "SUCCESS" or kernel.evidence.record_count != 1:
        print(
            "the reference slice did not complete: status=%s evidence_records=%d error=%s"
            % (run.status, kernel.evidence.record_count, run.outcome.error),
            file=sys.stderr,
        )
        return EXIT_INCOMPLETE

    certificate = certificate_artifact(build_certificate(kernel, run))

    if args.command == "run":
        if args.json:
            print(json.dumps(certificate, indent=2, sort_keys=True))
        else:
            print(_summary(certificate))
        return EXIT_OK

    path = os.path.join(root, *CERTIFICATE_ARTIFACT.split("/"))
    rendered = json.dumps(certificate, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if args.check:
        try:
            with open(path, encoding="utf-8") as handle:
                current = handle.read()
        except OSError:
            current = ""
        if current != rendered:
            print(
                "the committed certificate is stale\nrun: python -m ginkgo_runtime.slice emit",
                file=sys.stderr,
            )
            return EXIT_STALE
        print(
            "committed certificate matches a fresh run  %s"
            % certificate["certificate_hash"][:16]
        )
        return EXIT_OK

    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(rendered)
    print("wrote %s" % CERTIFICATE_ARTIFACT)
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
