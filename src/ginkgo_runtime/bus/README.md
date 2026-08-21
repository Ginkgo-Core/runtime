# RK-01 — Capability Bus

The single lawful entry point (invariant 2). Named exactly **`Capability Bus`**, no qualifier,
and there is exactly one.

Specification: [`ai-stack#39`](https://github.com/Ginkgo-Core/ai-stack/issues/39) ·
[`capability-runtime-kernel-v1.md` §5](https://github.com/Ginkgo-Core/ai-stack/blob/master/architecture/runtime/capability-runtime-kernel-v1.md) ·
test matrix in [`reference-slice.md` §5](https://github.com/Ginkgo-Core/ai-stack/blob/master/architecture/runtime/reference-slice.md) ·
EPIC [`ai-stack#35`](https://github.com/Ginkgo-Core/ai-stack/issues/35)

```text
CapabilityRequest v1
  -> admission        schema · caller-agnosticism · contract version
  -> identity         payload fingerprint · idempotency
  -> dispatch         the internal routing interface — one seam
  -> normalization    RuntimeExecutionResult -> CapabilityResult v1
  -> caller projection
```

It **absorbs RK-05**. Normalisation is the return leg of the envelope, not a component:
splitting it out *"creates a hop where `routing.adapter_id` and `routing.provider` can leak into
an unprivileged projection"*. So the code that builds the envelope and the code that projects it
are the same code.

## It decides nothing about routing

| It does | It does **not** do |
|---|---|
| validate against the frozen `CapabilityRequest v1` | select a skill, adapter or provider — RK-02 |
| reject a caller that names an implementation | evaluate lifecycle, approval or gates — RK-04 |
| deduplicate by `idempotency_key` | execute anything — RK-08A |
| map executor state onto result status | persist evidence — RK-06 |
| withhold routing provenance from the caller | exist twice |

None of RK-02, RK-04, RK-08A or RK-06 is implemented in this repository. The Bus reaches all of
them through one `Protocol` with one method, which is not wide enough to express any of their
decisions. The only implementation here is `DeterministicDownstream`, a scripted stand-in with no
routing logic in it — a fake that *resolved* anything would be a second routing authority.

## Nothing frozen is restated

Every value is read from the pinned `ai-stack` schemas at load time. If one cannot be read, loading
raises: a boundary that cannot read the contract must never report that a request satisfies it.

| Value | Read from |
|---|---|
| the whole request contract | `contracts/core/capability-request.schema.json` |
| capability-name pattern · `contract_version` pattern | the same schema |
| the version this kernel implements | the same schema's `x-ginkgo-contract-version` |
| the result envelope | `contracts/core/capability-result.schema.json` |
| the error envelope | `contracts/core/error.schema.json` |
| the executor result | `contracts/runtime/runtime-execution-result.schema.json` |
| **executor state → result status** | the same schema's `x-ginkgo-status-mapping` |

Validation runs against the schemas themselves, through a strict in-tree validator that **refuses
any keyword it does not implement** — see [`ADR-RT-002`](../../../docs/adr/ADR-RT-002-strict-schema-validator.md).

One thing is transcribed rather than read: the **forbidden caller-field list**, because it is prose
in `ai-stack#39` and `contracts/runtime/README.md`, not a value in any schema. It carries its
citation, the same treatment RK-07 gives the lifecycle ceiling matrix.

## Caller-agnosticism, by prefix, recursively

Invariants 17 and 18. Two details, both easy to get wrong:

**Prefix, not exact name.** `ai-stack#39`: *"an exact-name check passes `provider_hint`,
`endpoint_url` and `commands`"* — and calls that a real gap in an existing validator.

**Recursively, not top-level.** The frozen request is `additionalProperties: false` at the top, so
a top-level `provider` never survives schema validation. `input` is `additionalProperties: true` —
business payload has to be — so `input.provider_hint` sails through the schema, and a top-level-only
scan would admit it. The scan walks the whole request.

Keys only, never values: `input.note = "ask the provider"` is business prose. Case-insensitive, so
`providerHint` does not slip past.

## Idempotency

The fingerprint covers `capability · input · constraints · governance · evidence · caller`, and
excludes `request_id · correlation_id · task_id · execution_id · idempotency_key`.

Excluding `request_id` is the whole design. A retry legitimately carries a **new** `request_id` —
that is what makes it a distinct request. Hashing it would turn every retry into a payload
mismatch, so every retry would return `CONFLICT` and the mechanism would protect nothing while
appearing to work.

- same key + same payload → the **same stored result**, and **no second dispatch** (P7);
- same key + different payload → `CONFLICT`, neither replayed nor executed (N14).

The ledger is keyed per `(tenant_id, workspace_id, idempotency_key)`, and it is **in-process and
deliberately not durable** — invariant 9 puts runtime state in PostgreSQL, and that is not RK-01's
decision to make.

## Two outcomes, and the difference matters

`reference-slice.md` §5 says *"rejected, VALIDATION"* for N1–N8 and N13, and reserves `BLOCKED`
for N9–N12, which are **results**. The Bus keeps that distinction:

- **rejection** — never admitted. Carries `Error v1` and no result. An inadmissible request cannot
  become a `CapabilityResult v1` without fabricating the `execution_id`, `skill_id` and
  `skill_version` the contract requires as non-empty strings;
- **result** — admitted, dispatched, returned. A validated `CapabilityResult v1`.

## The caller projection

`routing.adapter_id` and `routing.provider` are **removed**, not nulled. Reference slice N16 asks
for them *absent*, and null would be worse: a deterministic local skill has genuinely null adapter
and provider, so rendering "withheld" as null would destroy the distinction between *no adapter
exists* and *you are not allowed to know*.

The projection is therefore **not** a `CapabilityResult v1` — `routing.required` lists both fields.
That is the contract instructing its own projection, and a test pins it so nobody later "fixes" it.

## `independent_validation`

> *"An executor's self-reported `succeeded` is not by itself sufficient evidence of success. When
> required is true and satisfied is not true, RK-01 must not emit SUCCESS."*

The veto applies to the `succeeded` branch only — turning a `cancelled` into an `ERROR` would
discard true information rather than withhold an unproven claim. What it becomes is a kernel
decision, since the contract states only what it must *not* be: `ERROR`, because `PARTIAL` would
still assert partial success on the same unverified evidence.

## Reference slice coverage

RK-01's half of the matrix, per `ai-stack#39`'s Done-when:

| | |
|---|---|
| **N1** sub-3-segment capability | ✅ |
| **N2** implementation field | ✅ |
| **N3** `provider_hint` · `endpoint_url` · `commands` — prefix matching | ✅ |
| **N4** credential and command surfaces | ✅ |
| **N5** `fallback_policy: explicit` | ✅ |
| **N6** lowercase `data_classification` | ✅ |
| **N7** bare-number `max_cost` | ✅ |
| **N8** missing `idempotency_key` | ✅ |
| **N13** unknown `contract_version` | ✅ |
| **N14** conflicting `idempotency_key` | ✅ |
| **N15** no secret in an error body | ✅ |
| **N16** projection withholds routing provenance | ✅ |
| **P7** replay: same result, no second dispatch | ✅ |
| **P8** all caller `actor_type` values handled equivalently | ✅ |

N9–N12 (lifecycle, approval, gates, unknown capability) and N17 (deadline) belong to RK-04, RK-02
and RK-08A. P1–P6 need RK-06 and RK-09.

## Usage

```python
from ginkgo_runtime.bus import CapabilityBus, DeterministicDownstream

bus = CapabilityBus.from_pinned_contracts(DeterministicDownstream())
outcome = bus.submit(request)

outcome.accepted      # bool
outcome.status        # SUCCESS | PARTIAL | BLOCKED | ERROR, or None
outcome.result        # CapabilityResult v1, validated  (internal)
outcome.for_caller()  # the same, routing provenance withheld
outcome.error         # Error v1, validated
outcome.replayed      # came from the ledger rather than a dispatch
```
