# ADR-RT-002 — A strict, fail-closed JSON Schema validator instead of a dependency

- **Status:** Accepted
- **Date:** 2026-08-21
- **Scope:** `Ginkgo-Core/ginkgo-runtime` only. Decides nothing about architecture or contracts.
- **Context:** RK-01 (`ai-stack#39`) requires that a `CapabilityRequest` be validated *against the
  canonical authority*, with no frozen schema duplicated. That needs a JSON Schema validator, and
  [`ADR-RT-001`](ADR-RT-001-implementation-stack.md) fixes the stack at stdlib plus PyYAML.

## Decision

**Implement a validator in-tree that refuses every keyword it does not implement**, in
`src/ginkgo_runtime/contracts/jsonschema.py`. No new dependency.

The refusal is the decision. Everything else follows from it.

## The problem with each option

### A. Add `jsonschema`

Correct, battle-tested, and the obvious answer. Two things argue against it.

It is a second runtime dependency in a kernel whose EPIC says *"No framework, no new engine, no
new container"* — and the ecosystem's other Python, `ai-stack/tools/conformance`, is stdlib-only
with a CI step that **blocks `jsonschema` from importing at all**. Diverging from that in the
repository specified to host RK-10's runtime half is a cost paid twice.

### B. Hand-write checks derived from the schema

Rejected on the requirement itself. Reading `pattern` and `enum` values out of the schema and then
re-implementing their *semantics* in Python is a duplicate of the contract wearing a disguise. It
drifts exactly the way `ADR-RT-001` rejected a hand-written YAML parser for drifting.

### C. A validator that ignores what it does not implement

Rejected, and it is worth naming because it is what "write a small validator" usually means.

The danger of writing your own is **under-enforcement**: an unimplemented keyword is silently
skipped, the document passes, and the boundary reports conformance it never checked. On the
kernel's single most security-relevant gate that is worse than having no validator, because it
looks like one.

### D. A validator that *refuses* what it does not implement — **chosen**

The failure mode in C is removed by construction:

> every keyword encountered that the module does not implement raises `UnsupportedSchemaError`.
> Nothing is skipped.

The worst case becomes a validator that will not run — loud, immediate, and impossible to mistake
for a pass. `schema_keywords()` exposes the walk, and a test asserts that all four frozen contracts
use only implemented keywords, so `ai-stack` adding one fails here rather than quietly weakening
the boundary.

The bounded surface makes this tractable rather than heroic. The four frozen contracts use
nineteen assertion keywords between them: `$ref`, `additionalProperties`, `allOf`, `const`, `enum`,
`format`, `if`/`then`, `items`, `maxLength`, `minLength`, `minimum`, `not`, `oneOf`, `pattern`,
`properties`, `required`, `type`, `uniqueItems`. That is a closed set, and it is verified by test
rather than assumed.

## Consequences

- **No new dependency.** `ADR-RT-001` stands unchanged: stdlib plus PyYAML.
- **`format: date-time` is asserted, not annotated.** The specification treats `format` as an
  annotation and most validators ignore it. This one enforces it, because `deadline` reaches
  deadline arithmetic downstream and `"tomorrow"` is not a deadline. An unimplemented format
  raises rather than passing — the same rule as any other keyword.
- **`True` is not `1`.** Python says otherwise and JSON does not. `evidence.required` is a boolean
  and `cost_events[].amount` is a number; conflating them would admit `required: 1`.
- **`$ref` resolves only through the pinned set.** The resolver is supplied by the caller and
  backed by the SHA-pinned, integrity-checked manifest, so a ref cannot reach the filesystem or
  the network.
- **Violations render two ways.** `message` quotes the offending value and is for tests and logs;
  `locator` gives pointer and keyword only, and is the only form that crosses the boundary.
  `error.schema.json` forbids secrets in `message` or `details`, and the rejected value is
  precisely what might be one. Reference slice N15 is the test.

## Revisit when

A frozen contract adopts a keyword whose correct implementation is genuinely subtle —
`$dynamicRef`, `unevaluatedProperties`, full `format` assertion across many formats. At that point
the in-tree validator stops being small, the argument in A wins, and this ADR is superseded rather
than patched.
