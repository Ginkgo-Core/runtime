# Promotion recommendation — `rk-reference-echo`

> **This document is a recommendation. It is not a promotion, and it does not perform one.**
>
> `ai-stack#38`: `automatic_promotion: false`. `lifecycle-and-promotion.md` §4 invariant 7:
> *"Nothing leaves `FEATURE` without evidence. No stage is reached by inference, by a green offline
> CI alone, or because a credential name is configured."*
>
> The lifecycle status of `rk-reference-echo` is **unchanged at `FEATURE`**, and
> `routing_eligible` is **unchanged at `false`**. Changing either requires a recorded human
> decision that this document is evidence *for*, never a substitute *of*.

## What the E2E run proved

One `CapabilityRequest v1` for `system.echo.execute` crossed every frozen boundary and came back
normalised, leaving exactly one evidence record behind.

```text
CapabilityRequest v1
  → RK-01 Capability Bus
  → RK-02 Domain Router
  → RK-04 Policy + Lifecycle Gate
  → RK-08A Executor Contract
  → rk-reference-echo  (tool: rk-echo-transform)
  → RuntimeExecutionResult v1
  → CapabilityResult v1  ·  SUCCESS
  → 1 × Evidence v1  +  3 × Event v1
```

Artifact: [`e2e-certificate.json`](e2e-certificate.json). Reproduce with
`python -m ginkgo_runtime.slice run`.

| Claim | How it is proven |
|---|---|
| caller equivalence | the same request under all eight `actor_type` values produces identical results |
| `FEATURE` permitted in `certification` | the run itself — `routing_eligible: false` and it routed |
| `FEATURE` barred in `production` | `BLOCKED_LIFECYCLE`, nothing executed, no evidence written |
| approval never clears a gate | `APPROVED` + `BLOCKED` and `APPROVED` + `UNKNOWN` both block, naming the gate |
| caller selects no implementation | seven implementation fields and three prefix near-misses all rejected before routing |
| no secret in an error body | probes with credential-shaped values; nothing echoed back |
| projection withholds routing provenance | `adapter_id` and `provider` **absent**, while the internal result carries them as honest nulls |
| replay writes no second evidence | the ledger short-circuits before dispatch; the executor is never reached |
| exactly one evidence record | asserted on the ledger, and the writer refuses a second write independently |
| lifecycle events persisted | three `Event v1` records, each with timestamp, actor and reason |
| normalized result | executor reports `succeeded`; RK-01 maps it to `SUCCESS`; the executor has no `status` field |
| deterministic fingerprints | two runs produce byte-identical decision, execution, evidence and events |

Permissions are proven by **execution**, not by reading the descriptor back: sockets and
write-mode `open` are blocked while the slice runs, and it still succeeds.

## What it does not prove

Verbatim from `reference-slice.md` §6, because a green result invites a claim that outruns it:

- **nothing** about any provider, adapter, credential or network path;
- **nothing** about n8n, ComfyUI, Pyre or any executor;
- **nothing** about cross-process teardown — RK-09 is in-process, which is precisely why RK-08B is
  deferred to Wave 2.

A green result is `TEST` / `EVAL` evidence, never `API_OBSERVATION`. The evidence record says so
itself, and carries the negative proofs alongside the positives:

```yaml
live_provider_validation_proven: false
mutations: OFF
credentials_in_ci: none
network: OFF
```

## Recommendation

**Recommend `FEATURE → PROTOTYPE` for `rk-reference-echo`, subject to a recorded human decision.**

`lifecycle-and-promotion.md` §1 defines the entry condition for `PROTOTYPE` as *"An implementation
exists"*, and one now does, with reproducible evidence. `PROTOTYPE` remains not
`production_eligible` and not `routing_eligible`, so the recommendation grants no new execution
authority — it records that the stage the record sits at no longer matches what exists.

**Do not recommend `routing_eligible: true`.** That belongs to `VALIDATED`, which requires the
fifteen gates in §6. Several are not met by this slice and are not the slice's job to meet.

### Gate status, honestly

| §6 gate | Status |
|---|---|
| contract test | ✅ every artifact validated against its frozen schema |
| happy path | ✅ |
| invalid input | ✅ empty message → `failed` → normalised `ERROR` |
| error normalization | ✅ |
| timeout | ❌ **not proven.** The executor carries `bounds.timeout_ms` and enforces no wall clock — time is an injected port and a pure in-process transform never waits. Enforcement belongs to RK-08B |
| retry policy | ❌ **not proven.** `max_attempts: 1`; a deterministic pure function has no materially different second attempt |
| permissions | ✅ proven by execution |
| secret scan | ✅ no credential anywhere; `credential_refs: []` asserted rather than omitted |
| resource consumption | ⚠️ trivially bounded, not measured |
| teardown | ❌ **not proven** cross-process. In-process teardown is provable by assertion only |
| idempotency | ✅ replay and conflict both |
| observability | ✅ one event per transition, with actor and reason |
| persistence / restart | ❌ **not proven.** The ledgers are in-process; invariant 9 puts the durable ones in PostgreSQL |
| E2E | ✅ this run |
| evidence registration | ✅ one `Evidence v1`, referenced from the result |

Ten of fifteen. The five gaps are structural — they need a durable store and a cross-process
executor, both of which are Wave 2 — and none of them is a defect in this slice.

## What should happen next

1. A human records the `FEATURE → PROTOTYPE` decision, or declines it. Either way it is recorded.
2. If the descriptor should live in `ginkgo-agents` rather than here, that is a manifest re-pin and
   nothing else — see the `role: kernel` note in `catalog/README.md`.
3. RK-08B (Wave 2) and a PostgreSQL-backed ledger close the five remaining gates. Only then does
   `VALIDATED` become discussable.

Nothing in this repository will change a lifecycle status on its own.
