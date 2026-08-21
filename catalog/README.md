# RK-07 — Runtime Catalog

An **immutable, boot-time projection** of GitHub architecture truth into an in-process catalog,
pinned to explicit commit SHAs.

Specification: [`ai-stack#37`](https://github.com/Ginkgo-Core/ai-stack/issues/37) ·
[`capability-runtime-kernel-v1.md` §5](https://github.com/Ginkgo-Core/ai-stack/blob/master/architecture/runtime/capability-runtime-kernel-v1.md) ·
EPIC [`ai-stack#35`](https://github.com/Ginkgo-Core/ai-stack/issues/35)

```text
GitHub architecture truth
  -> pinned source SHAs
  -> deterministic load
  -> validation / rejection
  -> immutable in-process snapshot
  -> RK-02 / RK-04 consumers
```

## It authorises nothing

| It does | It does **not** do |
|---|---|
| read · validate · normalise · project · reject · version a snapshot | route · choose a provider · choose an executor · execute |
| resolve records by `id:` | consult a registry during a request |
| report what is non-conformant | repair, coerce or upgrade a legacy record |
| hold one immutable value | hold mutable runtime state |
| pin sources by commit SHA | write anything back to a source |

## Layout

| Path | What it is |
|---|---|
| `sources.manifest.json` | the pin: repo · ref · **commit SHA** · path · git blob id · SHA-256 · size · how to read it |
| `sources/` | the pinned bytes, byte-identical to GitHub at those commits, read-only |
| `catalog.snapshot.json` | **generated.** The catalog the pins currently produce |
| `rejection-report.json` | **generated.** The migration backlog |

The two generated files are committed on purpose. It makes the projection reviewable in a pull
request — a change in admission behaviour shows up as a diff in the catalog, not only in the code
that produced it — and CI asserts they still match a fresh build.

## Commands

```bash
python -m ginkgo_runtime.catalog build          # version, counts, pins
python -m ginkgo_runtime.catalog build --json   # the whole snapshot, canonical JSON
python -m ginkgo_runtime.catalog reject         # the migration backlog
python -m ginkgo_runtime.catalog verify         # rebuild and compare bytes
python -m ginkgo_runtime.catalog emit --check   # committed artifacts still current?
```

There is deliberately no `--fix`.

## Why the sources are vendored

The catalog must be a projection of GitHub *and* reproducible offline, on any machine, with no
credentials. Those are only compatible if the pinned bytes travel with the repository.

Each entry carries two digests. `blob_sha` is the git object id, so anyone can check a vendored
file against GitHub without cloning:

```bash
gh api repos/Ginkgo-Core/ginkgo-library/contents/registry/skills.yaml?ref=<commit_sha> --jq .sha
```

`sha256` is the content digest the loader enforces on **every** build. A file that drifts from
its pin raises `SourceIntegrityError` and no catalog is produced.

Re-pinning is an explicit, reviewable commit, and it changes `catalog_version`.

## Frozen authority is loaded, never duplicated

Every pattern and enum is read out of the pinned `ai-stack` schemas at build time. If a value
cannot be read the build raises `AuthorityUnavailableError` — a catalog that cannot read the
freeze must never report that a record conforms to it.

| Value | Read from |
|---|---|
| capability name pattern (≥ 3 segments) | `contracts/core/capability-request.schema.json` |
| `contract_version` pattern | `contracts/core/capability-request.schema.json` |
| `fallback_policy` enum | `contracts/core/capability-request.schema.json` |
| `lifecycle_status` enum · `kind` enum · `id` pattern · descriptor `required` | `contracts/runtime/runtime-skill-descriptor.schema.json` |

One value is stated in code rather than read: the **lifecycle ceiling matrix**, because its
canonical form is the table in `architecture/runtime/lifecycle-and-promotion.md` §3 — prose, not
a schema value. `authority.py` transcribes it row for row and records that the table wins.

## What the catalog admits

Two record classes, two gates. A capability record has no `kind` and never should, so it is not
gated on one.

**Capability records** — from `ginkgo-library/registry/capabilities.yaml`:
the frozen capability-name pattern, and a known `lifecycle_ceiling` if one is declared.

**Skill / binding descriptors** — from `ginkgo-agents/features/*.yaml`,
`ginkgo-agents/skills/**/skill.yaml` and `ginkgo-library/registry/skills.yaml`:
`kind` · capability name · `contract_version` · `fallback_policy` · `lifecycle_status` ·
the tri-state against its stage ceiling · no legacy `human_approval`.

Both classes are additionally gated on **taxonomy**: one identifier may not fill two roles in
one record. `CAPABILITY != SKILL != BINDING != TOOL != ADAPTER != PROVIDER` (invariant 4,
ADR-002) is the same rule the static linter enforces as `P0-TAX-001`. RK-10 is built before the
kernel exactly so the kernel cannot admit what the linter rejects.

A record with **no** `lifecycle_status` is admitted at **`FEATURE`** — never at its legacy
maturity code (`lifecycle-and-promotion.md` §4 invariant 8). `maturity`, `level`,
`autonomy_level`, `phase` and `status` are carried as advisory metadata and decide nothing.

**Admitted is not the same as complete.** Every legacy dialect predates
`RuntimeSkillDescriptor v1`, so each admitted record carries `descriptor_gaps`: the required
fields the source does not declare. RK-07 names the gap and does not fill it.

## Rejection is a report, never a repair

Every excluded record appears in `rejection-report.json` with structured reasons, its
`source_ref` and its identity. Nothing disappears silently, and every failing rule is listed —
not only the first.

**The rejection report is the migration backlog**, and it belongs to the repository that
authored the record. The runtime never edits a source.

## Determinism

`catalog_version` is `rkc1-` plus the first 16 hex of `catalog_hash`, a SHA-256 over the
canonical JSON of: the algorithm version, every frozen authority value, every pinned source
(repo · commit · blob id · digest), every admitted record **and every rejection**.

Rejections are hashed deliberately. A record moving between admitted and rejected changes what
the catalog means, so it must change the version; hashing only the admitted set would let the
backlog change silently.

Filesystem order cannot reach the catalog, and the guarantee is structural rather than a sort
applied at the end: the loader reads exactly the manifest's paths, in manifest order, and
`os.walk`, `os.listdir` and `glob` appear nowhere in the package. A test enforces that.

## The disjoint namespaces

`providers.yaml`, `tools.yaml` and `endpoints.yaml` carry free-text `capabilities[]` tags —
`reasoning`, `web-research`, `image-generation` — in a namespace disjoint from capability IDs.
Joining them by name would corrupt the catalog.

They are not merely unused: **the manifest refuses to pin them**, so the bytes never enter the
process and the join has nothing to join. Attempting to pin one is a build error.

## Static P0 conformance

The conformance authority is `ai-stack/tools/conformance` and stays there. This repository does
not vendor it, and CI does not run it — that would be a second conformance authority, and the
runtime half of the story is RK-10's work (`ai-stack#38`).

The emitted catalog is checked against it out of band:

```bash
# from an ai-stack checkout
python -m tools.conformance check /path/to/ginkgo-runtime/catalog/catalog.snapshot.json
```

At the pinned commits this reports **PASS** — 0 architecture violations, 0 invalid inputs,
0 linter errors.

`sources.manifest.json` and `rejection-report.json` are not P0 artifacts and are not linted:
one is a list of commit pins, the other a migration backlog. The linter reads only the paths it
is given, and declaring `x-conformance-kind` on them would not help — that key adds a signal to
content the linter already recognises, and cannot make an unrecognised document recognised.

`catalog/sources/` is deliberately **not** conformant: it is a verbatim copy of the upstream
registries, whose defects are precisely what `rejection-report.json` enumerates.
