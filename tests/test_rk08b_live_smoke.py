"""RK-08B/B — the live smoke's gates, exercised without a live anything.

The smoke is the one thing in this repository that is *supposed* to open a
connection, which makes it the one thing whose refusal path has to be tested
hardest. Every test here runs the real gates, the real pin check, the real
binding, the real validator and the real evidence assembly. What none of them
does is reach a network: the socket is injected as a fake, exactly as it is for
the transport tests.

The test that matters most is the boring one —
``test_the_smoke_is_refused_when_the_opt_in_is_absent`` and its neighbours. A
smoke that dispatches by accident against an operator's automation host is not a
test failure, it is an incident, and "it is opt-in" is a claim that has to be
executed rather than asserted in a runbook.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
from dataclasses import dataclass

import pytest

from ginkgo_runtime.catalog.build import default_root
from ginkgo_runtime.smoke import (
    OPT_IN_VARIABLE,
    PING_INPUT,
    PONG_OUTPUT,
    SMOKE_CORRELATION_ID,
    SMOKE_IDEMPOTENCY_KEY,
    SMOKE_WORKFLOW_IDENTITY,
    SmokeRefused,
    check_preconditions,
    run_live_smoke,
    workflow_artifact_sha256,
)
from ginkgo_runtime.smoke.__main__ import EXIT_FAILED, EXIT_OK, EXIT_REFUSED, main
from ginkgo_runtime.smoke.n8n_ping import BASE_URL_VARIABLE, CREDENTIAL_REF_VARIABLE

from .test_rk08b_live_transport import (
    CREDENTIAL_ENV,
    CREDENTIAL_REF,
    CREDENTIAL_VALUE,
    FakeHttpClient,
)

BASE_URL = "http://127.0.0.1:5678"

#: The environment that opts in. Written out here and nowhere else, so that
#: `test_the_opt_in_variable_is_set_nowhere_in_the_repository` can prove the
#: repository itself never sets it.
OPTED_IN = {OPT_IN_VARIABLE: "1", BASE_URL_VARIABLE: BASE_URL}


def smoke_body(identity=SMOKE_WORKFLOW_IDENTITY, value="PONG", status="success"):
    return {
        "ginkgo": {
            "workflow": identity,
            "status": status,
            "correlation_id": SMOKE_CORRELATION_ID,
            "output": {"value": value},
        }
    }


def client(**kwargs):
    kwargs.setdefault("body", smoke_body())
    return FakeHttpClient(**kwargs)


# ===========================================================================
# The gates
# ===========================================================================


@pytest.mark.parametrize(
    "environ",
    [
        {},
        {BASE_URL_VARIABLE: BASE_URL},
        {OPT_IN_VARIABLE: "", BASE_URL_VARIABLE: BASE_URL},
        {OPT_IN_VARIABLE: "0", BASE_URL_VARIABLE: BASE_URL},
        {OPT_IN_VARIABLE: "true", BASE_URL_VARIABLE: BASE_URL},
        {OPT_IN_VARIABLE: "yes", BASE_URL_VARIABLE: BASE_URL},
        {OPT_IN_VARIABLE: "TRUE", BASE_URL_VARIABLE: BASE_URL},
        {OPT_IN_VARIABLE: " 1", BASE_URL_VARIABLE: BASE_URL},
    ],
)
def test_the_smoke_is_refused_when_the_opt_in_is_absent(environ):
    """One value opts in. Everything that merely looks like consent does not."""
    fake = client()
    with pytest.raises(SmokeRefused):
        run_live_smoke(environ, client=fake)
    assert fake.requests == []


def test_the_smoke_is_refused_when_no_endpoint_is_named():
    """It does not fall back to a conventional address. Guessing is the whole risk."""
    fake = client()
    with pytest.raises(SmokeRefused) as refused:
        run_live_smoke({OPT_IN_VARIABLE: "1"}, client=fake)
    assert fake.requests == []
    assert "endpoint" in str(refused.value)
    assert "5678" not in str(refused.value)


@pytest.mark.parametrize(
    "url",
    [
        "http://user:hunter2hunter2@127.0.0.1:5678",
        "http://127.0.0.1:5678?apiKey=AKIAIOSFODNN7EXAMPLE",
        "ftp://127.0.0.1",
        "localhost:5678",
    ],
)
def test_an_endpoint_that_could_carry_a_credential_refuses_the_smoke(url):
    fake = client()
    with pytest.raises(SmokeRefused) as refused:
        run_live_smoke({OPT_IN_VARIABLE: "1", BASE_URL_VARIABLE: url}, client=fake)
    assert fake.requests == []
    assert "AKIAIOSFODNN7EXAMPLE" not in str(refused.value)
    assert "hunter2hunter2" not in str(refused.value)


def test_a_bound_reference_that_does_not_resolve_refuses_the_smoke():
    """Fail closed, before dispatch — never an unauthenticated fallback."""
    environ = dict(OPTED_IN, **{CREDENTIAL_REF_VARIABLE: CREDENTIAL_REF})
    fake = client()
    with pytest.raises(SmokeRefused):
        run_live_smoke(environ, client=fake)
    assert fake.requests == []


def test_every_gate_is_reported_at_once_rather_than_one_at_a_time():
    checks = check_preconditions({})
    assert [check.name for check in checks] == [
        "opt-in",
        "endpoint",
        "workflow-artifact",
        "credential-reference",
        "workflow-imported",
    ]
    blocking = [check for check in checks if check.blocking and not check.satisfied]
    assert {check.name for check in blocking} == {"opt-in", "endpoint"}


def test_the_unverifiable_gate_is_reported_as_unverifiable_and_never_as_a_pass():
    """Whether the workflow is imported is not knowable from here, and it says so."""
    checks = {check.name: check for check in check_preconditions(OPTED_IN)}
    imported = checks["workflow-imported"]
    assert imported.satisfied is False
    assert imported.blocking is False
    assert imported.verdict == "UNVERIFIABLE"
    # And it does not block: an operator who has imported the workflow is not
    # prevented from running the smoke by a check that cannot be satisfied.
    assert all(
        check.satisfied for check in check_preconditions(OPTED_IN) if check.blocking
    )


def test_no_precondition_message_quotes_anything_read_from_the_environment():
    environ = dict(
        OPTED_IN,
        **{CREDENTIAL_REF_VARIABLE: CREDENTIAL_REF, CREDENTIAL_ENV: CREDENTIAL_VALUE},
    )
    printed = " ".join(check.as_line() for check in check_preconditions(environ))
    assert CREDENTIAL_VALUE not in printed
    assert CREDENTIAL_ENV not in printed


# ===========================================================================
# The pin
# ===========================================================================


def test_the_committed_workflow_is_what_the_smoke_pins():
    digest = workflow_artifact_sha256()
    assert len(digest) == 64
    # Stable across calls: it is a digest of bytes on disk, not of a re-serialised
    # document, so it does not drift with whichever JSON writer last touched it.
    assert digest == workflow_artifact_sha256()


def test_the_pinned_workflow_calls_nothing_and_stores_nothing():
    """The smoke's safety claims, asserted against the document rather than the README.

    "No third-party API call, no CRM, no LLM" is a property of this file. A
    runbook can say it; only a test keeps it true when someone adds a node.
    """
    from ginkgo_runtime.smoke import WORKFLOW_ARTIFACT

    with open(os.path.join(default_root(), *WORKFLOW_ARTIFACT), encoding="utf-8") as fh:
        document = json.load(fh)

    permitted = {
        "n8n-nodes-base.webhook",
        "n8n-nodes-base.set",
        "n8n-nodes-base.respondToWebhook",
    }
    kinds = {node["type"] for node in document["nodes"]}
    assert kinds <= permitted, kinds
    assert len(document["nodes"]) == 3

    serialised = json.dumps(document)
    # No credential is attached to any node, and no node exists that could make
    # an outbound call, touch a database or reach a model.
    assert "credentials" not in serialised
    for forbidden in (
        "httpRequest",
        "postgres",
        "mysql",
        "googleSheets",
        "openAi",
        "lmChat",
        "executeWorkflow",
        "executeCommand",
        "evolution",
        "callima",
    ):
        assert forbidden.lower() not in serialised.lower(), forbidden

    # And it persists nothing: a smoke run leaves no execution data behind.
    assert document["settings"]["saveDataSuccessExecution"] == "none"
    assert document["settings"]["saveDataErrorExecution"] == "none"

    # The identity the transport verifies is in the document, not in the request.
    assert SMOKE_WORKFLOW_IDENTITY in serialised


# ===========================================================================
# The opt-in is set by an operator, and by nothing in this repository
# ===========================================================================
#
# The claim is narrow and it is a security claim: **repository executable code
# must not set or mutate** ``GINKGO_N8N_LIVE_SMOKE``. A live smoke that a CI
# job, a fixture or a helper can arrange on its own is not opt-in, and the gap
# between the two words is an unattended dispatch against an operator's
# automation host.
#
# It used to be checked by grepping for the literal ``GINKGO_N8N_LIVE_SMOKE=1``,
# which is not the claim. Text cannot tell a setter from a sentence: the module
# docstring of ``smoke/n8n_ping.py`` quotes the command an operator types, and
# grep called that a violation. The two repairs grep leaves available are to
# delete the documentation, which makes the smoke harder to run safely, or to
# allow-list the file, which blinds the check in exactly the file that matters
# most. Neither is a repair.
#
# So Python is read with the Python parser. A string is a setter when it stands
# in a position that mutates an environment -- an assignment into ``os.environ``,
# an argument to ``setenv``/``putenv``/``setdefault``/``update``, an ``env=``
# handed to a process spawn -- and not when it merely appears. Comments,
# docstrings and ordinary literals are not positions.
#
# The detector fails closed. A mutation whose key it cannot read statically is
# reported, because "I cannot tell what this writes" and "this writes the
# opt-in" are the same finding. Formats git tracks that can set a variable but
# have no parser here -- workflow YAML, shell, Dockerfiles, JSON config -- keep
# a textual check, and there the rule stays absolute: the literal belongs in
# ``docs/`` and nowhere else.


#: Method names that write to the process environment whatever the receiver is.
#: ``putenv`` is the libc call ``os`` exposes; ``setenv`` is pytest's monkeypatch.
ENV_WRITERS = frozenset({"putenv", "setenv"})

#: Method names that write to *a mapping*, and therefore write to the
#: environment only when the mapping being written is the environment.
MAPPING_WRITERS = frozenset({"update", "setdefault", "__setitem__"})

#: The mirrors. A removal cannot enable a smoke, so it is classified rather than
#: flagged -- the CLI tests below delete the variable to prove the refusal path,
#: and that has to stay legal.
ENV_REMOVERS = frozenset({"unsetenv", "delenv"})
MAPPING_REMOVERS = frozenset({"pop", "popitem", "clear"})

#: Entry points that hand a whole environment to a child process. A subprocess
#: started with the opt-in set is a live dispatch as surely as an in-process one,
#: and this suite does spawn subprocesses.
SPAWN_FUNCTIONS = frozenset(
    {
        "run",
        "call",
        "check_call",
        "check_output",
        "Popen",
        "execve",
        "execvpe",
        "spawnve",
        "spawnvpe",
        "posix_spawn",
        "posix_spawnp",
    }
)


@dataclass(frozen=True)
class EnvMutation:
    """One statement that puts something into, or takes something out of, an environment."""

    path: str
    line: int
    form: str
    #: The variable written, when the parser can read it. ``None`` means the key
    #: is computed -- and a key that cannot be read is treated as the opt-in.
    key: str | None
    #: A removal (``delenv``, ``del``, ``pop``) rather than a write.
    removes: bool

    def __str__(self):
        return "%s:%d %s key=%s" % (
            self.path,
            self.line,
            self.form,
            "<computed>" if self.key is None else self.key,
        )


def _is_environ(node):
    """``os.environ``, a bare ``environ`` imported from it, or the bytes flavour."""
    if isinstance(node, ast.Attribute):
        return node.attr in ("environ", "environb")
    if isinstance(node, ast.Name):
        return node.id in ("environ", "environb")
    return False


def _environ_subscripts(target):
    """The ``environ[...]`` subscripts in an assignment target, unpacking included."""
    if isinstance(target, ast.Subscript) and _is_environ(target.value):
        yield target
    elif isinstance(target, ast.Tuple | ast.List):
        for element in target.elts:
            yield from _environ_subscripts(element)
    elif isinstance(target, ast.Starred):
        yield from _environ_subscripts(target.value)


def _string_constants(tree):
    """``NAME = "..."`` bindings in the module, so an aliased key still resolves.

    Seeded with the symbol this suite imports, which the files that use it never
    assign. A file that binds the name itself overrides the seed, which is right:
    its own definition is what its code means.
    """
    constants = {"OPT_IN_VARIABLE": OPT_IN_VARIABLE}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    constants[target.id] = node.value.value
    return constants


def _spawn_names(tree):
    """Bare names in this module bound to a spawn entry point by ``from ... import``."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in ("subprocess", "os"):
            for alias in node.names:
                if alias.name in SPAWN_FUNCTIONS:
                    names.add(alias.asname or alias.name)
    return names


def _is_spawn(func, spawn_names):
    """Whether a call is a process spawn, and so whether its ``env=`` is an environment."""
    if isinstance(func, ast.Attribute):
        return (
            isinstance(func.value, ast.Name)
            and func.value.id in ("subprocess", "os")
            and func.attr in SPAWN_FUNCTIONS
        )
    return isinstance(func, ast.Name) and func.id in spawn_names


def _resolve(node, constants):
    """The string a key expression denotes, or ``None`` when it is computed."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    if isinstance(node, ast.Attribute):
        return constants.get(node.attr)
    return None


def _mapping_keys(node, constants):
    """The keys a mapping expression contributes.

    An empty literal contributes none; anything the parser cannot read
    contributes one unreadable key, which is what makes ``update(whatever)`` a
    finding rather than a shrug.
    """
    if isinstance(node, ast.Dict):
        return [_resolve(key, constants) for key in node.keys]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "dict":
        return _call_keys(node, constants, "update")
    return [None]


def _call_keys(call, constants, attribute):
    """The environment keys a mutating call names."""
    if attribute == "update":
        keys = []
        for argument in call.args:
            keys.extend(_mapping_keys(argument, constants))
        for keyword in call.keywords:
            if keyword.arg is None:
                keys.extend(_mapping_keys(keyword.value, constants))
            else:
                keys.append(keyword.arg)
        return keys
    if call.args:
        return [_resolve(call.args[0], constants)]
    for keyword in call.keywords:
        if keyword.arg in ("name", "key"):
            return [_resolve(keyword.value, constants)]
    return [None]


def env_mutations(source, path="<source>"):
    """Every environment write and removal in ``source``, read with the parser.

    Only syntax counts. The literal in a docstring, a comment or an ordinary
    string expression is not in a mutating position and does not appear here.
    ``os.environ[NAME] = "1"`` does, whether ``NAME`` is spelled out, held in a
    module constant, or imported under another name.
    """
    tree = ast.parse(source, filename=path)
    constants = _string_constants(tree)
    spawn_names = _spawn_names(tree)
    found = []

    def record(node, form, keys, removes):
        for key in keys:
            found.append(EnvMutation(path, node.lineno, form, key, removes))

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign | ast.AugAssign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                for subscript in _environ_subscripts(target):
                    key = _resolve(subscript.slice, constants)
                    record(node, "environ[...] = ...", [key], False)
            # ``os.environ |= {...}``: ``os._Environ`` has had ``__ior__`` since 3.9.
            if isinstance(node, ast.AugAssign) and _is_environ(node.target):
                record(node, "environ |= ...", _mapping_keys(node.value, constants), False)
        elif isinstance(node, ast.Delete):
            for target in node.targets:
                if isinstance(target, ast.Subscript) and _is_environ(target.value):
                    record(node, "del environ[...]", [_resolve(target.slice, constants)], True)
        elif isinstance(node, ast.Call):
            attribute = node.func.attr if isinstance(node.func, ast.Attribute) else None
            on_environ = isinstance(node.func, ast.Attribute) and _is_environ(node.func.value)
            if attribute in ENV_WRITERS or (on_environ and attribute in MAPPING_WRITERS):
                keys = _call_keys(node, constants, attribute)
                record(node, "%s(...)" % attribute, keys, False)
            elif attribute in ENV_REMOVERS or (on_environ and attribute in MAPPING_REMOVERS):
                keys = _call_keys(node, constants, attribute)
                record(node, "%s(...)" % attribute, keys, True)
            if _is_spawn(node.func, spawn_names):
                for keyword in node.keywords:
                    if keyword.arg == "env":
                        keys = _mapping_keys(keyword.value, constants)
                        record(node, "spawn(env=...)", keys, False)
    return found


def opt_in_setters(source, path="<source>"):
    """The mutations in ``source`` that could enable the live smoke.

    A removal is not one, and neither is a write to some other variable. A write
    whose key the parser cannot read *is* one: the check fails closed.
    """
    return [
        mutation
        for mutation in env_mutations(source, path)
        if not mutation.removes and mutation.key in (None, OPT_IN_VARIABLE)
    ]


#: Extensions git tracks that are executed or consumed as configuration, and can
#: therefore put a variable into an environment. ``.py`` is absent: it is read
#: with the parser instead. ``.md`` is absent too -- documentation is exactly
#: where the command an operator types is supposed to be written down.
TEXTUAL_SURFACES = (
    ".bash",
    ".bat",
    ".cfg",
    ".cmd",
    ".env",
    ".envrc",
    ".ini",
    ".json",
    ".mk",
    ".ps1",
    ".sh",
    ".toml",
    ".yaml",
    ".yml",
    ".zsh",
)
TEXTUAL_FILENAMES = ("Dockerfile", "Makefile", "Procfile", ".env", ".envrc")

#: For those surfaces the rule is absolute, because there is no parser here to
#: tell a comment from a setter, and guessing in the permissive direction is how
#: a check like this quietly dies.
TEXTUAL_SETTERS = (
    re.compile(r"\b%s\b\s*[:=]" % re.escape(OPT_IN_VARIABLE)),
    re.compile(r"\b(?:export|set|setx|ENV)\s+%s\b" % re.escape(OPT_IN_VARIABLE)),
)


def textual_setters(text):
    """``(line number, line)`` for every line of a non-Python surface that sets it."""
    return [
        (number, line.strip())
        for number, line in enumerate(text.splitlines(), start=1)
        if any(pattern.search(line) for pattern in TEXTUAL_SETTERS)
    ]


def scan_repository():
    """Read every tracked file the way its format deserves.

    Returns ``(python, textual, skipped, violations)`` -- the two lists of files
    actually inspected, the files no reader claimed, and the findings.
    """
    root = default_root()
    listed = subprocess.run(
        ["git", "-C", root, "ls-files", "-z"], capture_output=True, text=True, check=True
    )
    python, textual, skipped, violations = [], [], [], []
    for name in listed.stdout.split("\0"):
        if not name:
            continue
        path = os.path.join(root, *name.split("/"))
        try:
            with open(path, "rb") as handle:
                raw = handle.read()
        except OSError:
            violations.append("%s: tracked but unreadable" % name)
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            # Not text. Innocent unless it carries the variable, in which case
            # something is being smuggled and it is reported rather than passed.
            skipped.append(name)
            if OPT_IN_VARIABLE.encode("ascii") in raw:
                violations.append("%s: not text, and it names the opt-in" % name)
            continue
        if name.endswith(".py"):
            python.append(name)
            violations.extend(str(found) for found in opt_in_setters(text, name))
        elif name.endswith(TEXTUAL_SURFACES) or os.path.basename(name) in TEXTUAL_FILENAMES:
            textual.append(name)
            violations.extend(
                "%s:%d %s" % (name, number, line) for number, line in textual_setters(text)
            )
    return python, textual, skipped, violations


def repository_source(name):
    """The text of a tracked file, named the way ``git ls-files`` names it."""
    with open(os.path.join(default_root(), *name.split("/")), encoding="utf-8") as handle:
        return handle.read()


# --- the invariant ---------------------------------------------------------

SMOKE_MODULE = "src/ginkgo_runtime/smoke/n8n_ping.py"
THIS_FILE = "tests/test_rk08b_live_smoke.py"


def test_no_executable_surface_in_the_repository_sets_the_opt_in():
    """Not in CI, not in a script, not in a fixture. Only an operator sets it."""
    _, _, _, violations = scan_repository()
    assert violations == [], violations


def test_the_only_thing_the_repository_does_to_the_opt_in_is_delete_it():
    """Sharper than "no setters": name every statement that touches the variable.

    "Nothing sets it" passes just as loudly when nothing mentions it at all. This
    says what does touch it and that all of it is deletion, so a write cannot
    later arrive disguised as an edit to something that was already here.
    """
    python, _, _, _ = scan_repository()
    touching = [
        mutation
        for name in python
        for mutation in env_mutations(repository_source(name), name)
        if mutation.key == OPT_IN_VARIABLE
    ]
    assert touching, "the CLI refusal tests do delete it; if that stopped, say so here"
    assert all(mutation.removes for mutation in touching), [str(m) for m in touching]
    assert {mutation.path for mutation in touching} == {THIS_FILE}


def test_the_scan_reads_every_python_file_and_every_config_surface():
    """A check that silently inspected nothing would pass just as quietly."""
    python, textual, skipped, _ = scan_repository()
    assert skipped == [], skipped
    assert len(python) > 60, len(python)
    for name in (
        SMOKE_MODULE,
        "src/ginkgo_runtime/smoke/__main__.py",
        "src/ginkgo_runtime/smoke/__init__.py",
        THIS_FILE,
        "tests/conftest.py",
    ):
        assert name in python, name
    for name in (".github/workflows/ci.yml", "pyproject.toml"):
        assert name in textual, name


# --- positive controls: prose that quotes the command is not a setter ------


def test_the_docstring_that_quotes_the_command_is_not_a_setter():
    """The regression this section exists for.

    ``smoke/n8n_ping.py`` documents the variable an operator exports, literal
    ``=1`` and all, in its module docstring. Grep called that a setter. The
    parser does not, and the documentation stays where an operator will read it.
    """
    source = repository_source(SMOKE_MODULE)
    assert "%s=1" % OPT_IN_VARIABLE in source  # the text grep tripped over
    assert opt_in_setters(source, SMOKE_MODULE) == []  # and it is not a setter
    assert "%s=1" % OPT_IN_VARIABLE in ast.get_docstring(ast.parse(source))


def test_the_runbook_still_documents_the_command_an_operator_types():
    """Deleting the documentation is not a way to satisfy this check."""
    runbook = repository_source("docs/runbooks/RK-08B-B-live-smoke.md")
    assert "%s=1" % OPT_IN_VARIABLE in runbook


@pytest.mark.parametrize(
    "source",
    [
        '"""Export GINKGO_N8N_LIVE_SMOKE=1 to opt in."""\n',
        'def run():\n    """Run it with GINKGO_N8N_LIVE_SMOKE=1 set."""\n',
        "# export GINKGO_N8N_LIVE_SMOKE=1\n",
        'HELP = "set GINKGO_N8N_LIVE_SMOKE=1"\n',
        'raise SystemExit("refused: set %s=1" % OPT_IN_VARIABLE)\n',
        'OPT_IN_VARIABLE = "GINKGO_N8N_LIVE_SMOKE"\n',
        'import os\nopted = os.environ.get("GINKGO_N8N_LIVE_SMOKE") == "1"\n',
        'import os\nopted = os.getenv(OPT_IN_VARIABLE) == "1"\n',
        # A dict handed to a function is an argument, not the process environment.
        'run_live_smoke({"GINKGO_N8N_LIVE_SMOKE": "1"}, client=fake)\n',
        'OPTED_IN = {OPT_IN_VARIABLE: "1", BASE_URL_VARIABLE: BASE_URL}\n',
        # A write to a neighbouring variable is a write to a neighbouring variable.
        'import os\nos.environ["GINKGO_N8N_BASE_URL"] = "http://127.0.0.1:5678"\n',
        'import subprocess\nsubprocess.run(["x"], env={"PATH": ""})\n',
        'import subprocess\nsubprocess.run(["x"], env={})\n',
    ],
)
def test_prose_and_ordinary_code_that_name_the_variable_are_accepted(source):
    assert opt_in_setters(source) == [], source


@pytest.mark.parametrize(
    "source",
    [
        'def test(monkeypatch):\n    monkeypatch.delenv("GINKGO_N8N_LIVE_SMOKE", raising=False)\n',
        "def test(monkeypatch):\n    monkeypatch.delenv(OPT_IN_VARIABLE, raising=False)\n",
        'import os\ndel os.environ["GINKGO_N8N_LIVE_SMOKE"]\n',
        'import os\nos.environ.pop("GINKGO_N8N_LIVE_SMOKE", None)\n',
        'import os\nos.unsetenv("GINKGO_N8N_LIVE_SMOKE")\n',
    ],
)
def test_removing_the_variable_is_not_setting_it(source):
    """Deletion is how the refusal path is tested. It is recorded, not flagged."""
    assert opt_in_setters(source) == [], source
    removals = [mutation for mutation in env_mutations(source) if mutation.removes]
    assert [mutation.key for mutation in removals] == [OPT_IN_VARIABLE]


# --- negative controls: a real setter is caught ----------------------------

#: Written out as text on purpose, so the file that proves the detector works is
#: also the file that would have fooled grep hardest.
SETTERS = (
    'import os\nos.environ["GINKGO_N8N_LIVE_SMOKE"] = "1"\n',
    'import os\nos.environ[OPT_IN_VARIABLE] = "1"\n',
    'import os\nVAR = "GINKGO_N8N_LIVE_SMOKE"\nos.environ[VAR] = "1"\n',
    'import os\nos.environ["GINKGO_N8N_LIVE_SMOKE"] += "1"\n',
    'import os\nfirst, os.environ["GINKGO_N8N_LIVE_SMOKE"] = 1, "1"\n',
    'import os\nos.environ.setdefault("GINKGO_N8N_LIVE_SMOKE", "1")\n',
    'import os\nos.environ.update({"GINKGO_N8N_LIVE_SMOKE": "1"})\n',
    'import os\nos.environ.update(GINKGO_N8N_LIVE_SMOKE="1")\n',
    'import os\nos.environ.update(**{"GINKGO_N8N_LIVE_SMOKE": "1"})\n',
    'import os\nos.environ.__setitem__("GINKGO_N8N_LIVE_SMOKE", "1")\n',
    'import os\nos.environ |= {"GINKGO_N8N_LIVE_SMOKE": "1"}\n',
    'import os\nos.putenv("GINKGO_N8N_LIVE_SMOKE", "1")\n',
    'from os import environ\nenviron["GINKGO_N8N_LIVE_SMOKE"] = "1"\n',
    'def test(monkeypatch):\n    monkeypatch.setenv("GINKGO_N8N_LIVE_SMOKE", "1")\n',
    'def test(mp):\n    mp.setenv(OPT_IN_VARIABLE, "1")\n',
    'import subprocess\nsubprocess.run(["x"], env={"GINKGO_N8N_LIVE_SMOKE": "1"})\n',
    'from subprocess import run\nrun(["x"], env={OPT_IN_VARIABLE: "1"})\n',
    'import subprocess\nsubprocess.Popen(["x"], env={"GINKGO_N8N_LIVE_SMOKE": "1"})\n',
)

#: The same thing, obscured. The parser cannot read these keys -- and that is
#: the finding, not an excuse to let them through.
OBSCURED_SETTERS = (
    'import os\nos.environ["GINKGO" + "_N8N_LIVE_SMOKE"] = "1"\n',
    'import os\nos.environ[chosen] = "1"\n',
    'import os\nos.environ["".join(parts)] = "1"\n',
    'import os\nkeys = ["GINKGO_N8N_LIVE_SMOKE"]\nos.environ[keys[0]] = "1"\n',
    "import os\nos.environ.update(chosen)\n",
    "import os\nos.environ |= chosen\n",
    'import subprocess\nsubprocess.run(["x"], env=chosen)\n',
)


@pytest.mark.parametrize("source", SETTERS)
def test_a_real_setter_is_detected(source):
    """The negative control. If this ever passes, the invariant above proves nothing."""
    found = opt_in_setters(source)
    assert found, source
    assert all(mutation.key == OPT_IN_VARIABLE for mutation in found), [str(m) for m in found]


@pytest.mark.parametrize("source", OBSCURED_SETTERS)
def test_a_setter_whose_key_cannot_be_read_is_detected_too(source):
    """Fail closed. "I cannot tell what this writes" is not a pass."""
    found = opt_in_setters(source)
    assert found, source
    assert all(mutation.key is None for mutation in found), [str(m) for m in found]


def test_the_scan_would_fail_this_repository_if_a_setter_were_added():
    """End to end against a real file: the same text, plus one executable line."""
    source = repository_source(SMOKE_MODULE)
    assert opt_in_setters(source, SMOKE_MODULE) == []
    injected = source + '\n\nos.environ["%s"] = "1"\n' % OPT_IN_VARIABLE
    found = opt_in_setters(injected, SMOKE_MODULE)
    assert [mutation.key for mutation in found] == [OPT_IN_VARIABLE]


def test_the_file_holding_the_negative_controls_is_not_itself_a_setter():
    """Why there is no allow-list any more, and why there does not need to be one.

    This file spells out two dozen real setters as text, and quotes the operator's
    command line. Not one of those strings stands in a mutating position, so the
    parser is not fooled by the file that would have fooled grep hardest.
    """
    source = repository_source(THIS_FILE)
    assert "%s=1" % OPT_IN_VARIABLE in source
    assert opt_in_setters(source, THIS_FILE) == []
    mutations = env_mutations(source, THIS_FILE)
    touching = [mutation for mutation in mutations if mutation.key == OPT_IN_VARIABLE]
    assert touching, "the CLI tests below delete the variable"
    assert all(mutation.removes for mutation in touching), [str(m) for m in touching]
    # The one child process this file starts is handed an environment assembled
    # here rather than inherited, and the opt-in is not in it -- which is the
    # reason a spawn's ``env=`` is read at all.
    spawned = [mutation for mutation in mutations if mutation.form == "spawn(env=...)"]
    assert spawned, "the refused-run probe builds its own environment"
    assert all(mutation.key not in (None, OPT_IN_VARIABLE) for mutation in spawned), [
        str(m) for m in spawned
    ]


# --- the surfaces with no parser -------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "      GINKGO_N8N_LIVE_SMOKE: '1'",
        "  run: GINKGO_N8N_LIVE_SMOKE=1 python -m ginkgo_runtime.smoke ping",
        "export GINKGO_N8N_LIVE_SMOKE=1",
        "ENV GINKGO_N8N_LIVE_SMOKE 1",
        "setx GINKGO_N8N_LIVE_SMOKE 1",
    ],
)
def test_a_setter_in_a_surface_with_no_parser_is_caught_textually(line):
    assert textual_setters(line), line


@pytest.mark.parametrize(
    "line",
    [
        "  run: python -m ginkgo_runtime.smoke preconditions",
        "  # the operator sets the opt-in; CI never does",
        "GINKGO_N8N_BASE_URL: 'http://127.0.0.1:5678'",
    ],
)
def test_an_unrelated_config_line_is_not_a_setter(line):
    assert textual_setters(line) == [], line


# ===========================================================================
# The run itself, over a fake socket
# ===========================================================================


def test_ping_pong_normalises_to_an_independently_validated_success():
    report = run_live_smoke(OPTED_IN, client=client())
    assert report["passed"] is True
    assert report["state"] == "succeeded"
    assert report["output"] == dict(PONG_OUTPUT)
    assert report["independent_validation"] == {
        "required": True,
        "satisfied": True,
        "validator_ref": "rk08b-b-ping-pong-validator",
    }


def test_the_engines_own_word_for_success_is_not_enough():
    """GL-07. A workflow reporting `success` while returning the wrong thing fails."""
    report = run_live_smoke(OPTED_IN, client=client(body=smoke_body(value="PING")))
    assert report["state"] == "succeeded"
    assert report["independent_validation"]["satisfied"] is False
    assert report["passed"] is False


def test_the_dispatched_input_is_the_ping_and_nothing_else():
    fake = client()
    run_live_smoke(OPTED_IN, client=fake)
    body = json.loads(fake.requests[0].body.decode("utf-8"))
    assert body["input"] == dict(PING_INPUT)
    assert body["correlation_id"] == SMOKE_CORRELATION_ID
    assert body["idempotency_key"] == SMOKE_IDEMPOTENCY_KEY


def test_the_report_proves_dispatch_identity_outcome_and_correlation():
    report = run_live_smoke(OPTED_IN, client=client())
    evidence = report["evidence"]
    assert evidence["dispatch_attempted"] is True
    record = evidence["dispatches"][0]
    assert record["workflow_identity"] == SMOKE_WORKFLOW_IDENTITY
    assert record["workflow_artifact_sha256"] == workflow_artifact_sha256()
    assert record["identity_verified"] is True
    assert record["outcome"] == "observed"
    assert record["correlation_id"] == SMOKE_CORRELATION_ID
    assert evidence["normalized"]["state"] == "succeeded"
    assert evidence["normalized"]["executor_ref"] == "rk-n8n-executor"


def test_a_wrong_workflow_at_the_endpoint_fails_the_smoke_rather_than_passing_it():
    report = run_live_smoke(
        OPTED_IN, client=client(body=smoke_body(identity="callima.inbound@3"))
    )
    assert report["passed"] is False
    assert report["evidence"]["normalized"]["error_code"] == "N8N_WORKFLOW_MISMATCH"
    assert "callima" not in json.dumps(report)


def test_a_resolved_credential_never_appears_in_the_smoke_report():
    environ = dict(
        OPTED_IN,
        **{CREDENTIAL_REF_VARIABLE: CREDENTIAL_REF, CREDENTIAL_ENV: CREDENTIAL_VALUE},
    )
    fake = client()
    report = run_live_smoke(environ, client=fake)
    assert report["passed"] is True
    assert CREDENTIAL_VALUE in json.dumps(dict(fake.requests[0].headers))
    assert CREDENTIAL_VALUE not in json.dumps(report)
    assert report["evidence"]["dispatches"][0]["authenticated"] is True


# ===========================================================================
# The CLI
# ===========================================================================


def test_the_cli_reports_preconditions_without_contacting_anything(monkeypatch, capsys):
    monkeypatch.delenv(OPT_IN_VARIABLE, raising=False)
    monkeypatch.delenv(BASE_URL_VARIABLE, raising=False)
    assert main(["preconditions"]) == EXIT_REFUSED
    printed = capsys.readouterr().out
    assert "MISSING opt-in" in printed
    assert "UNVERIFIABLE workflow-imported" in printed


def test_the_cli_refuses_a_ping_without_the_opt_in(monkeypatch, capsys):
    monkeypatch.delenv(OPT_IN_VARIABLE, raising=False)
    assert main(["ping", "--base-url", BASE_URL]) == EXIT_REFUSED
    assert "refused" in capsys.readouterr().err


def test_the_cli_prints_the_pin(capsys):
    assert main(["pin"]) == EXIT_OK
    assert capsys.readouterr().out.strip() == workflow_artifact_sha256()


def test_refused_and_failed_are_different_exit_codes():
    """"Never contacted anything" and "contacted n8n and it was wrong" differ."""
    assert EXIT_OK != EXIT_FAILED != EXIT_REFUSED != EXIT_OK


def test_the_cli_never_imports_the_socket_module_for_a_refused_run(monkeypatch):
    """A refused smoke has not even loaded the module that owns the socket."""
    import sys

    probe = (
        "import sys; sys.path.insert(0, %r);"
        "from ginkgo_runtime.smoke.__main__ import main;"
        "code = main(['ping', '--base-url', 'http://127.0.0.1:5678']);"
        "loaded = [m for m in sys.modules if 'n8n_http_client' in m];"
        "print(code, loaded)" % (default_root() + "/src")
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        env={"PATH": "", "SYSTEMROOT": ""},
    )
    assert result.stdout.strip() == "2 []", (result.stdout, result.stderr)
