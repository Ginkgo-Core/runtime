"""CLI for the Runtime Catalog.

.. code-block:: text

    python -m ginkgo_runtime.catalog build            # version, counts, pins
    python -m ginkgo_runtime.catalog build --json     # the whole snapshot
    python -m ginkgo_runtime.catalog reject           # the migration backlog
    python -m ginkgo_runtime.catalog verify           # rebuild twice, compare bytes

Exit codes
    0  built
    1  the catalog could not be built (fail closed)
    2  a determinism check failed

There is deliberately no ``--fix``. Repairing a legacy record is the authoring
repository's work; this tool never writes to a source.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from ..canonical import canonical_json
from .build import artifacts, build_catalog, default_root, rejection_report
from .errors import CatalogBuildError

EXIT_OK = 0
EXIT_BUILD_FAILED = 1
EXIT_NOT_DETERMINISTIC = 2


def _summary(catalog) -> str:
    lines = [
        "catalog_version   %s" % catalog.catalog_version,
        "catalog_hash      %s" % catalog.catalog_hash,
        "algorithm         %s" % catalog.algorithm_version,
        "admitted          %d  (%d capabilities, %d skills/bindings)"
        % (catalog.admitted_count, len(catalog.capabilities), len(catalog.skills)),
        "rejected          %d" % catalog.rejected_count,
        "pinned sources:",
    ]
    seen: set[tuple[str, str]] = set()
    for source in catalog.pinned_sources:
        key = (str(source["repo"]), str(source["commit_sha"]))
        if key in seen:
            continue
        seen.add(key)
        lines.append("  %-28s %s  (%s)" % (key[0], key[1], source["ref"]))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ginkgo_runtime.catalog")
    parser.add_argument("--root", default=None, help="repository root (default: inferred)")
    sub = parser.add_subparsers(dest="command", required=True)

    build_cmd = sub.add_parser("build", help="build the catalog")
    build_cmd.add_argument("--json", action="store_true", help="emit the full snapshot")

    sub.add_parser("reject", help="emit the machine-readable rejection report")
    sub.add_parser("verify", help="rebuild and assert the snapshot is byte-identical")

    emit_cmd = sub.add_parser("emit", help="write the committed catalog artifacts")
    emit_cmd.add_argument(
        "--check",
        action="store_true",
        help="do not write; fail if the committed artifacts differ from a fresh build",
    )

    args = parser.parse_args(argv)

    try:
        catalog = build_catalog(args.root)
    except CatalogBuildError as exc:
        print("catalog build failed (fail closed): %s" % exc, file=sys.stderr)
        return EXIT_BUILD_FAILED

    if args.command == "build":
        if args.json:
            print(canonical_json(catalog.to_document()))
        else:
            print(_summary(catalog))
        return EXIT_OK

    if args.command == "reject":
        print(canonical_json(rejection_report(catalog)))
        return EXIT_OK

    if args.command == "emit":
        drift = []
        for relative, document in artifacts(catalog).items():
            path = os.path.join(args.root or default_root(), *relative.split("/"))
            rendered = json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
            if args.check:
                try:
                    with open(path, encoding="utf-8") as handle:
                        current = handle.read()
                except OSError:
                    current = ""
                if current != rendered:
                    drift.append(relative)
                continue
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(rendered)
            print("wrote %s" % relative)
        if drift:
            print(
                "committed catalog artifacts are stale: %s\n"
                "run: python -m ginkgo_runtime.catalog emit" % ", ".join(drift),
                file=sys.stderr,
            )
            return EXIT_NOT_DETERMINISTIC
        if args.check:
            print("committed artifacts match a fresh build  %s" % catalog.catalog_version)
        return EXIT_OK

    # verify
    first = canonical_json(catalog.to_document())
    try:
        second = canonical_json(build_catalog(args.root).to_document())
    except CatalogBuildError as exc:
        print("rebuild failed: %s" % exc, file=sys.stderr)
        return EXIT_BUILD_FAILED
    if first != second:
        print(
            "REBUILD IS NOT DETERMINISTIC: the same pinned inputs produced two "
            "different snapshots",
            file=sys.stderr,
        )
        return EXIT_NOT_DETERMINISTIC
    print("deterministic rebuild OK  %s" % catalog.catalog_version)
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
