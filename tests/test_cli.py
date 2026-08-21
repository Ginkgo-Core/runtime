"""The CLI, and the committed catalog artifacts it keeps honest."""

from __future__ import annotations

import json
import os

from ginkgo_runtime.catalog.__main__ import (
    EXIT_BUILD_FAILED,
    EXIT_OK,
    main,
)
from ginkgo_runtime.catalog.build import CATALOG_ARTIFACT, REJECTION_ARTIFACT, artifacts

from .conftest import REPO_ROOT, make_snapshot


def test_the_committed_artifacts_match_a_fresh_build():
    """The reviewed catalog in the repository is the one the pins produce.

    Committing generated files is only safe if CI proves they are current. This
    is that proof, and it is also what makes the projection reviewable: a change
    in admission behaviour shows up as a diff in ``catalog.snapshot.json``.
    """
    assert main(["emit", "--check"]) == EXIT_OK


def test_the_committed_artifacts_are_the_two_expected_files(catalog):
    assert set(artifacts(catalog)) == {CATALOG_ARTIFACT, REJECTION_ARTIFACT}
    for relative in artifacts(catalog):
        assert os.path.isfile(os.path.join(REPO_ROOT, *relative.split("/")))


def test_the_committed_catalog_carries_its_own_version():
    with open(os.path.join(REPO_ROOT, *CATALOG_ARTIFACT.split("/")), encoding="utf-8") as fh:
        document = json.load(fh)
    assert document["catalog_version"] == "rkc1-%s" % document["catalog_hash"][:16]
    assert len(document["capabilities"]) + len(document["skills"]) == 20
    assert len(document["rejections"]) == 37


def test_build_reports_the_summary(capsys):
    assert main(["build"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "catalog_version" in out
    assert "Ginkgo-Core/ginkgo-library" in out


def test_build_json_emits_canonical_json(capsys):
    assert main(["build", "--json"]) == EXIT_OK
    document = json.loads(capsys.readouterr().out)
    assert document["algorithm_version"] == "ginkgo.runtime-catalog.v1"


def test_verify_reports_a_deterministic_rebuild(capsys):
    assert main(["verify"]) == EXIT_OK
    assert "deterministic rebuild OK" in capsys.readouterr().out


def test_reject_emits_the_backlog(capsys):
    assert main(["reject"]) == EXIT_OK
    report = json.loads(capsys.readouterr().out)
    assert report["totals"]["rejected"] == len(report["records"]) == 37


def test_a_build_failure_exits_nonzero_and_says_so(tmp_path, capsys):
    """Fail closed at the process boundary too: no catalog, no zero exit."""
    root = make_snapshot(tmp_path, [("features/one.yaml", "skill_descriptor", None, "id: x\n")])
    os.remove(os.path.join(root, "catalog", "sources", "ginkgo-agents", "features", "one.yaml"))

    assert main(["--root", root, "build"]) == EXIT_BUILD_FAILED
    assert "fail closed" in capsys.readouterr().err


def test_the_cli_offers_no_fix_command():
    """Repairing a legacy record is the authoring repository's work, never the runtime's."""
    import argparse
    import contextlib
    import io

    stderr = io.StringIO()
    swallow = contextlib.suppress(argparse.ArgumentError, SystemExit)
    with contextlib.redirect_stderr(stderr), swallow:
        main(["fix"])
    assert "invalid choice" in stderr.getvalue()
