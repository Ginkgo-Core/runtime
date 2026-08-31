"""CLI for the RK-08B/B live smoke. Refuses by default.

.. code-block:: text

    python -m ginkgo_runtime.smoke preconditions           # check, contact nothing
    python -m ginkgo_runtime.smoke pin                     # the workflow artifact digest
    python -m ginkgo_runtime.smoke ping --base-url URL     # the live dispatch

``preconditions`` and ``pin`` open no socket and are safe to run anywhere.
``ping`` opens exactly one connection, to the endpoint you name, and only after
every blocking gate has passed.

Exit codes
    0  the smoke ran and PING -> PONG normalised to an independently validated success
    1  the smoke ran and did not
    2  a pre-condition was not satisfied, so nothing was dispatched

Exit 2 is deliberately distinct from exit 1. "The runtime never contacted
anything" and "the runtime contacted n8n and the answer was wrong" are different
facts, and a runbook that could not tell them apart would send an operator to
debug a workflow that was never called.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from .n8n_ping import (
    OPT_IN_VARIABLE,
    SmokeRefused,
    check_preconditions,
    run_live_smoke,
    workflow_artifact_sha256,
)

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_REFUSED = 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ginkgo_runtime.smoke",
        description=(
            "RK-08B/B live n8n smoke. Opt-in only: set %s=1 and name an endpoint. "
            "Nothing is discovered and no endpoint is guessed." % OPT_IN_VARIABLE
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("preconditions", "ping"):
        command = sub.add_parser(name)
        command.add_argument(
            "--base-url",
            default=None,
            help="the n8n base URL, e.g. http://127.0.0.1:5678 (no credentials, no query)",
        )
        command.add_argument("--json", action="store_true", help="machine-readable output")
    sub.add_parser("pin")

    args = parser.parse_args(argv)
    environ = dict(os.environ)

    if args.command == "pin":
        print(workflow_artifact_sha256())
        return EXIT_OK

    checks = check_preconditions(environ, base_url=args.base_url)

    if args.command == "preconditions":
        if args.json:
            print(
                json.dumps(
                    [
                        {
                            "name": check.name,
                            "verdict": check.verdict,
                            "blocking": check.blocking,
                            "detail": check.detail,
                        }
                        for check in checks
                    ],
                    indent=2,
                )
            )
        else:
            for check in checks:
                print(check.as_line())
        blocked = [check for check in checks if check.blocking and not check.satisfied]
        return EXIT_REFUSED if blocked else EXIT_OK

    try:
        report = run_live_smoke(environ, base_url=args.base_url)
    except SmokeRefused as refused:
        print(str(refused), file=sys.stderr)
        return EXIT_REFUSED

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print("state              %s" % report["state"])
        print("output             %s" % json.dumps(report["output"]))
        print("independent_valid  %s" % json.dumps(report["independent_validation"]))
        print("dispatch_attempted %s" % report["evidence"]["dispatch_attempted"])
        for dispatch in report["evidence"]["dispatches"]:
            print(
                "dispatch           workflow=%s verified=%s outcome=%s status=%s"
                % (
                    dispatch["workflow_identity"],
                    dispatch["identity_verified"],
                    dispatch["outcome"],
                    dispatch["http_status"],
                )
            )
        print("PASS" if report["passed"] else "FAIL")

    return EXIT_OK if report["passed"] else EXIT_FAILED


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
