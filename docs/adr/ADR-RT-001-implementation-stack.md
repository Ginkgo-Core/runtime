# ADR-RT-001 — Implementation stack for the Capability Runtime Kernel

- **Status:** Accepted
- **Date:** 2026-08-21
- **Scope:** `Ginkgo-Core/ginkgo-runtime` only. This ADR decides nothing about architecture,
  contracts or any other repository. Architecture authority remains `Ginkgo-Core/ai-stack`
  (invariant 8).
- **Context:** RK-07 (`ai-stack#37`) is the first implementation in this repository, and the
  repository had no implementation stack to inherit.

## Decision

**Python ≥ 3.11. Standard library, plus PyYAML as the single runtime dependency.
No framework.**

Development adds two tools, both pinned: `pytest` and `ruff`.

That is the whole stack. There is no HTTP client, no ORM, no scheduler, no dependency injection
container, no build step and no lockfile.

## Why the question arose

`ginkgo-runtime` was created by renaming `ginkgo-infra` (BLK-16). The pre-rename audit found
that repository to be *"a bare GitHub scaffold — stock Node `.gitignore`, stock MIT `LICENSE`,
two-line `README`, one root commit through the web UI, nine months untouched … **Not a template
for any repo.**"* Its `.gitignore` is therefore scaffold residue, not a stack decision, and this
change replaces it.

## Options considered

### A. TypeScript / Node

The strongest argument, and it is a real one: BLK-17's `KEEP_SEPARATE` resolution says the
kernel *"takes `canonical.ts` verbatim"*, and `ginkgo-connect`, `Sylvia` and `ginkgo-source`
are all TypeScript.

Rejected on surface area. RK-07 reads YAML and emits JSON. In Node that costs a
`package.json`, a `tsconfig.json`, a lockfile, `node_modules`, a build step **and** a YAML
dependency — every one of which Python does without. The `canonical.ts` instruction is
satisfied conceptually rather than literally, which is what the resolution actually authorises:
lift the *pattern* (sorted keys, no insignificant whitespace, SHA-256), depend on nothing.

### B. Python, standard library only, with a hand-written YAML subset parser

Rejected as the most dangerous option available. The three registries are YAML, so the parser
would sit directly between GitHub truth and the catalog. A bug in it would not fail loudly — it
would silently mis-project a record, which is precisely the failure mode this component exists
to make impossible. A well-tested dependency is the smaller risk. `loader.py` refuses to guess
at YAML for this reason.

### C. Python, standard library plus PyYAML — **chosen**

1. **The ecosystem's machine-validation authority is already this.**
   `ai-stack/tools/conformance` is Python, stdlib-only, and already treats PyYAML as the
   sanctioned YAML dependency: *"install PyYAML or pass JSON."*
2. **RK-10's runtime half is specified to live here and extend that suite.**
   `tools/conformance/README.md`: *"Part 2 belongs to `Ginkgo-Core/ginkgo-runtime` alongside the
   kernel it exercises."* A different language would make the static and runtime halves of one
   conformance story unshareable.
3. **It is the smallest surface that does the job.** One dependency, no build, no lockfile,
   `python -m ginkgo_runtime.catalog`.
4. **The kernel must stay small** (`ai-stack#35`: *"No framework, no new engine, no new
   container, no business domain."*). A stack with nothing to configure is the easiest one to
   keep small.

## Consequences

- The catalog build performs **no network I/O**. Sources are pinned by commit SHA and vendored;
  see [`catalog/README.md`](../../catalog/README.md). A test asserts no socket is opened.
- Installing the two pinned dev tools in CI is the only network the pipeline needs. It is not
  a live source fetch, and no source is ever read over the network.
- `ginkgo-connect` is **not** a dependency, in either direction (BLK-17 `KEEP_SEPARATE`). A test
  asserts that `@connect/*`, `FrozenDecision`, `rankedFallbacks` and `chosenExecutorRef` appear
  nowhere in the kernel's executable code.
- Ruff runs with `UP031` disabled so diagnostic messages keep one `%`-format style across this
  repository and `ai-stack/tools/conformance`. Every other selected rule is enforced.
- This ADR binds the kernel implementation. Any future component that needs a genuinely
  different runtime needs its own ADR, not an exception to this one.

## Revisit when

A kernel component requires long-lived processes, a database driver or an HTTP server — RK-01
and RK-08A are the plausible candidates. That is a new decision with new evidence, and it does
not retroactively make this one wrong.
