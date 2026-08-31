"""The architectural boundaries RK-07 must not cross.

Each test here corresponds to a "Never" in ``ai-stack#37``. They are asserted
structurally where possible -- a rule the code *cannot* break beats a rule the
code merely *does not* break.
"""

from __future__ import annotations

import dataclasses
import io
import os
import socket
import sys
import tokenize

import pytest

from ginkgo_runtime.canonical import sha256_hex
from ginkgo_runtime.catalog.build import build_catalog, default_root
from ginkgo_runtime.catalog.errors import CatalogBuildError
from ginkgo_runtime.catalog.manifest import FORBIDDEN_SOURCE_BASENAMES, load_manifest

from .conftest import REPO_ROOT, make_snapshot

PACKAGE = os.path.join(REPO_ROOT, "src", "ginkgo_runtime")

#: The single module permitted to import a network library, as a repo-relative
#: path.
#:
#: RK-08B/B's rule 11 -- *"network access must exist only inside the concrete
#: transport implementation"* -- is enforced here and nowhere else, because here
#: is the only place it can be enforced structurally. Everything the live n8n
#: transport does that is worth arguing about lives in
#: ``executor/n8n_transport.py``, which has no socket; what is left in the
#: exempt module is byte transfer.
#:
#: The exemption is a **list of one** and is asserted to be a list of one below.
#: Widening it is a visible edit to a test, in a diff a reviewer reads.
NETWORK_EXEMPT_MODULES = (
    os.path.join("src", "ginkgo_runtime", "executor", "n8n_http_client.py"),
)


def package_sources():
    """Every kernel module, as *executable code only*.

    Comments and string literals are stripped before the forbidden-token scans
    below. Without that, a module documenting why it does not call ``os.walk``
    would fail the test that asserts it does not call ``os.walk`` -- and the
    honest response to that would be to delete the explanation, which is
    precisely backwards.
    """
    for base, dirs, names in os.walk(PACKAGE):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in sorted(names):
            if not name.endswith(".py"):
                continue
            path = os.path.join(base, name)
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            yield path, _code_only(text)


def _code_only(text):
    words = (tokenize.NAME, tokenize.NUMBER)
    kept: list[str] = []
    previous = None
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type in (tokenize.COMMENT, tokenize.STRING):
            continue
        if not token.string.strip():
            continue
        # One space between adjacent words so `import requests` survives;
        # nothing between anything else so `os.walk` survives too.
        if previous in words and token.type in words:
            kept.append(" ")
        kept.append(token.string)
        previous = token.type
    return "".join(kept)


# -- no implicit join ------------------------------------------------------


def test_the_disjoint_registries_are_not_pinned(catalog):
    """``providers.yaml`` / ``tools.yaml`` / ``endpoints.yaml`` never enter the process.

    Their ``capabilities[]`` tags are free text in a namespace disjoint from
    capability IDs, and a name-join would corrupt the catalog (ai-stack#37). The
    strongest available guarantee is that the bytes are never loaded, so the join
    has nothing to join.
    """
    pinned = {str(source["path"]).rsplit("/", 1)[-1] for source in catalog.pinned_sources}
    assert pinned.isdisjoint(FORBIDDEN_SOURCE_BASENAMES)


def test_pinning_a_disjoint_registry_is_a_build_error(tmp_path):
    """The rule is enforced, not just observed."""
    root = make_snapshot(
        tmp_path, [("registry/providers.yaml", "skill_descriptor", "providers", "providers: []\n")]
    )
    with pytest.raises(CatalogBuildError) as excinfo:
        build_catalog(root)
    assert "disjoint from capability IDs" in str(excinfo.value)


def test_no_free_text_provider_or_tool_tag_became_a_capability(catalog):
    """Every admitted capability name traces to a real ``id:`` in the pinned registry.

    The free-text tags that would be joined -- ``reasoning``, ``web-research``,
    ``image-generation`` and the rest -- are single-segment and could not pass the
    frozen pattern even if they arrived, but this asserts the stronger property:
    the admitted set is exactly the declared set.
    """
    declared = {str(record["record_id"]) for record in catalog.capabilities}
    named = {
        str(record["capability"]["name"])
        for record in catalog.skills
        if record["capability"] is not None
    }
    # `system.*` is a reserved kernel-diagnostics namespace and has no record in
    # `ginkgo-library`'s registry -- deliberately, since it is not a business
    # domain. Every OTHER capability a skill names is one the registry declares.
    business = {name for name in named if not name.startswith("system.")}
    assert business <= declared
    for name in declared | named:
        assert len(name.split(".")) >= 3


def test_the_catalog_resolves_by_id_and_not_by_filename(catalog):
    """Three filename conventions are in use; none of them is an identity."""
    record = catalog.skill("shopee.product.normalize")
    assert record is not None
    # The file it came from is named for a legacy alias, not for the record id.
    assert record["source_ref"].endswith("features/commerce.shopee.product.normalize.yaml")


# -- one-way projection ----------------------------------------------------


def test_the_build_writes_nothing(tmp_path):
    """Not to the pinned snapshot, not anywhere.

    Any write attempt is caught at ``open`` rather than inferred from a digest
    comparison afterwards, so a write that was later reverted would still fail
    the test.
    """
    import builtins

    real_open = builtins.open
    attempted: list[tuple[str, str]] = []

    def guarded(file, mode="r", *args, **kwargs):
        if any(flag in mode for flag in ("w", "a", "x", "+")):
            attempted.append((str(file), mode))
        return real_open(file, mode, *args, **kwargs)

    builtins.open = guarded
    try:
        build_catalog(REPO_ROOT)
        observed_during_build = list(attempted)
        # Prove the guard can fire before trusting that it did not. An empty list
        # is only evidence if a real write would have filled it.
        with guarded(os.path.join(tmp_path, "probe"), "w") as probe:
            probe.write("x")
    finally:
        builtins.open = real_open

    assert observed_during_build == []
    assert attempted, "the write guard never fired, so the assertion above proved nothing"


def test_the_pinned_snapshot_is_unchanged_by_a_build():
    """Belt and braces: every pinned file still hashes to its manifest digest."""
    manifest = load_manifest(REPO_ROOT)
    before = {
        source.path: sha256_hex(manifest.read_source(source)) for source in manifest.sources
    }
    build_catalog(REPO_ROOT)
    after = {
        source.path: sha256_hex(manifest.read_source(source)) for source in manifest.sources
    }
    assert before == after
    assert all(after[s.path] == s.sha256 for s in manifest.sources)


def test_the_build_opens_no_socket():
    """No live registry fetch, mid-request or at boot. The pin is data, not a fetch."""

    class Blocked(socket.socket):
        def __init__(self, *args, **kwargs):
            raise AssertionError("the catalog build attempted network I/O")

    real_socket = socket.socket
    real_create = socket.create_connection
    socket.socket = Blocked  # type: ignore[misc,assignment]
    socket.create_connection = lambda *a, **k: (_ for _ in ()).throw(  # type: ignore[assignment]
        AssertionError("the catalog build attempted network I/O")
    )
    try:
        catalog = build_catalog(REPO_ROOT)
    finally:
        socket.socket = real_socket  # type: ignore[misc]
        socket.create_connection = real_create  # type: ignore[assignment]

    assert catalog.admitted_count > 0


def test_the_forbidden_token_scan_still_detects_a_violation():
    """The scans above are only worth running if they can fail.

    Stripping comments and strings could easily strip too much and leave the
    three scans passing vacuously, so this pins the scanner itself: real code is
    kept, prose about that code is not.
    """
    stripped = _code_only(
        '"""A docstring naming os.walk and import requests."""\n'
        "# a comment naming FrozenDecision\n"
        "import requests\n"
        "for base, dirs, names in os.walk(path):\n"
        "    pass\n"
    )
    assert "import requests" in stripped
    assert "os.walk" in stripped
    assert "docstring" not in stripped
    assert "FrozenDecision" not in stripped


def test_the_package_never_enumerates_the_filesystem():
    """``os.walk`` / ``listdir`` / ``glob`` do not appear in the kernel package.

    Determinism against filesystem order is structural: the loader reads exactly
    the manifest's paths. This test keeps it that way.
    """
    offenders = []
    for path, text in package_sources():
        forbidden = ("os.walk", "os.listdir", "os.scandir", "glob.", "iglob", "Path.rglob")
        for token in forbidden:
            if token in text:
                offenders.append((os.path.relpath(path, REPO_ROOT), token))
    assert offenders == []


def test_the_package_never_imports_a_network_client():
    """Every module except the one concrete transport. RK-08B/B rule 11.

    ``urllib.parse`` is not on the forbidden list and is not an oversight: it
    parses strings and opens nothing. ``urllib.request`` -- the half that does
    open something -- is, and ``test_the_transport_module_imports_only_the_string_half_of_urllib``
    in ``test_rk08b_live_transport.py`` pins that distinction over the AST rather
    than over a substring.
    """
    offenders = []
    for path, text in package_sources():
        relative = os.path.relpath(path, REPO_ROOT)
        if relative.replace("/", os.sep) in NETWORK_EXEMPT_MODULES:
            continue
        forbidden = (
            "import requests",
            "import httpx",
            "urllib.request",
            "import socket",
            "http.client",
        )
        for token in forbidden:
            if token in text:
                offenders.append((relative, token))
    assert offenders == []


def test_exactly_one_module_is_exempt_and_it_is_the_concrete_transport():
    """The exemption above is only meaningful if it stays a list of one.

    A test that skipped "the network modules" would grow quietly. This one names
    the count, so adding a second socket to the kernel fails here first.
    """
    assert len(NETWORK_EXEMPT_MODULES) == 1
    exempt = os.path.join(REPO_ROOT, NETWORK_EXEMPT_MODULES[0])
    assert os.path.isfile(exempt)
    with open(exempt, encoding="utf-8") as handle:
        text = _code_only(handle.read())
    # And it must actually be the transport: an exemption pointing at a module
    # that imports nothing would pass the test above while the real socket lived
    # somewhere else.
    assert "http.client" in text


def test_the_exempt_module_is_not_reachable_by_importing_the_package():
    """``import ginkgo_runtime.executor`` must not drag a socket in with it.

    Every other component imports that package. If the network module came along
    transitively, "the kernel opens no socket" would be a claim about what the
    kernel *does* rather than about what it *can* do -- and every offline proof in
    this suite would be one accidental module-level call away from being false.
    """
    import subprocess

    probe = (
        "import sys; sys.path.insert(0, %r);"
        "import ginkgo_runtime.executor;"
        "import ginkgo_runtime.bus, ginkgo_runtime.router, ginkgo_runtime.gate;"
        "import ginkgo_runtime.evidence, ginkgo_runtime.slice;"
        "loaded = [m for m in sys.modules if 'n8n_http_client' in m];"
        "print(loaded)" % os.path.join(REPO_ROOT, "src")
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]", result.stdout


def test_the_kernel_does_not_depend_on_ginkgo_connect():
    """BLK-17 resolution ``KEEP_SEPARATE``: the pattern is reused, the package is not."""
    offenders = []
    for path, text in package_sources():
        forbidden = (
            "@connect/",
            "ginkgo_connect",
            "FrozenDecision",
            "rankedFallbacks",
            "chosenExecutorRef",
        )
        for token in forbidden:
            if token in text:
                offenders.append((os.path.relpath(path, REPO_ROOT), token))
    assert offenders == []


# -- authorises nothing ----------------------------------------------------


def test_the_catalog_has_no_routing_surface(catalog):
    """It reads, validates, normalises, projects, rejects and versions. Nothing else."""
    forbidden = ("route", "resolve_provider", "choose", "select_skill", "execute", "dispatch")
    surface = [name for name in dir(catalog) if not name.startswith("_")]
    assert [name for name in surface if any(word in name for word in forbidden)] == []


def test_lookup_by_capability_ranks_nothing(catalog):
    """``skills_for_capability`` is a lookup, not a decision: no order, no preference.

    ``commerce.orders.read`` has two admitted bindings. The catalog returns both
    and expresses no preference between them; choosing is invariant 4's Domain
    Router work.
    """
    found = catalog.skills_for_capability("commerce.orders.read")
    assert {record["record_id"] for record in found} == {
        "amazon-spapi.orders.read",
        "shopee-open-api.orders.read",
    }
    assert all(record["routing"]["routing_eligible"] is False for record in found)


def test_the_snapshot_is_immutable(catalog):
    """It never becomes mutable runtime state."""
    with pytest.raises(dataclasses.FrozenInstanceError):
        catalog.catalog_version = "tampered"  # type: ignore[misc]
    with pytest.raises(TypeError):
        catalog.skills[0]["lifecycle_status"] = "PRODUCTION"  # type: ignore[index]
    with pytest.raises(AttributeError):
        catalog.skills.append({})  # type: ignore[attr-defined]
    nested = catalog.skills[0]["routing"]
    with pytest.raises(TypeError):
        nested["routing_eligible"] = True  # type: ignore[index]


def test_implementation_identity_is_never_caller_visible(catalog):
    """Invariant 17, asserted on every admitted binding rather than switched."""
    for record in catalog.skills:
        assert record["implementation_binding"]["caller_visible"] is False


def test_default_root_points_at_this_repository():
    assert os.path.isfile(os.path.join(default_root(), "catalog", "sources.manifest.json"))


def test_the_vendored_bytes_are_byte_identical_to_github():
    """Every pinned file's git object id equals the blob GitHub reports at that commit.

    This is the provenance claim the manifest makes, checked against git itself
    rather than against the manifest's own SHA-256 -- so it catches the one thing
    a self-consistent snapshot cannot: git rewriting the bytes on the way in.

    It is not hypothetical. ``runtime-skill-descriptor.schema.json`` is stored with
    CRLF line endings upstream, and with `core.autocrlf` on, the first `git add`
    normalised it to LF: still self-consistent on the machine that committed it,
    and a broken integrity gate for everyone who cloned it. `.gitattributes` marks
    the snapshot `-text` for exactly this reason.
    """
    import json
    import subprocess

    result = subprocess.run(
        ["git", "-C", REPO_ROOT, "ls-files", "-s", "catalog/sources"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip("not a git checkout")

    staged = {}
    for line in result.stdout.strip().splitlines():
        meta, path = line.split("\t", 1)
        staged[path.strip()] = meta.split()[1]

    with open(os.path.join(REPO_ROOT, "catalog", "sources.manifest.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)

    assert staged, "no pinned sources are tracked"
    for entry in manifest["sources"]:
        if entry["role"] == "kernel":
            # Owned by this repository; there is no upstream blob to compare to.
            # Its integrity is enforced by the manifest's sha256 like every other
            # source, and `test_a_kernel_source_is_still_integrity_checked` covers it.
            continue
        assert staged.get(entry["snapshot_path"]) == entry["blob_sha"], (
            "%s is stored as a different blob than %s@%s declares"
            % (entry["snapshot_path"], entry["repo"], entry["commit_sha"])
        )


def test_a_kernel_source_is_still_integrity_checked():
    """A kernel-owned source has no upstream commit and is still pinned.

    ``role: kernel`` relaxes exactly one thing -- ``commit_sha`` is null, because
    the file lives at this repository's HEAD, which git already pins. The content
    digest is enforced on every build exactly as it is for an upstream source, so
    the file cannot drift unnoticed.
    """
    import pytest

    from ginkgo_runtime.catalog.errors import SourceIntegrityError

    manifest = load_manifest(REPO_ROOT)
    kernel = [source for source in manifest.sources if source.role == "kernel"]
    assert kernel, "the reference descriptor should be a kernel source"

    for source in kernel:
        assert source.commit_sha == ""
        assert sha256_hex(manifest.read_source(source)) == source.sha256
        assert source.source_ref.startswith("Ginkgo-Core/ginkgo-runtime@blob:")

    tampered = dataclasses.replace(kernel[0], sha256="0" * 64)
    with pytest.raises(SourceIntegrityError):
        manifest.read_source(tampered)


def test_a_kernel_source_may_not_carry_a_commit(tmp_path):
    """An upstream source with no commit would be an unpinned projection.

    That is why the role is separate rather than ``projected`` with a nullable
    commit: the relaxation must not reach an upstream pin.
    """
    import json

    import pytest

    from ginkgo_runtime.catalog.errors import CatalogBuildError

    descriptor = "id: x\nkind: provider_binding\n"
    root = make_snapshot(
        tmp_path, [("features/one.yaml", "skill_descriptor", None, descriptor)]
    )
    path = os.path.join(root, "catalog", "sources.manifest.json")
    with open(path, encoding="utf-8") as handle:
        document = json.load(handle)
    for entry in document["sources"]:
        if entry["role"] == "projected":
            entry["role"] = "kernel"
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(document, handle, indent=2)

    with pytest.raises(CatalogBuildError) as excinfo:
        build_catalog(root)
    assert "commit_sha: null" in str(excinfo.value)
