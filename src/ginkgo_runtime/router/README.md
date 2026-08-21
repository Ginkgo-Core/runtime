# RK-02 — Domain Router

The only authority permitted to resolve implementation (invariant 4, ADR-001). Named exactly
**`Domain Router`**, and the kernel ships no other component whose name ends in `Router`.

Specification: [`ai-stack#40`](https://github.com/Ginkgo-Core/ai-stack/issues/40) ·
[`capability-runtime-kernel-v1.md` §5](https://github.com/Ginkgo-Core/ai-stack/blob/master/architecture/runtime/capability-runtime-kernel-v1.md) ·
EPIC [`ai-stack#35`](https://github.com/Ginkgo-Core/ai-stack/issues/35)

```text
capability + normalized input + internal policy context + immutable catalog
  -> RuntimeRouteDecision v1
```

**Absorbs RK-03.** A skill and a binding are one record discriminated by `kind`, so resolution is a
lookup over one record set, not a component. Splitting it out creates a second routing authority
for zero capability.

## Two absences are load-bearing

**No executor reference.** Fusing skill selection and executor selection into one decision is the
defect the contract exists to prevent. The Executor Router lives outside the kernel, reached from
TaskOps. This router has nothing to put in such a field and its code never names one.

**No candidate list.** `fallback_policy` authorises a *mode*, never a named alternative.
`same_semantics_only` means "a same-semantics substitution would be permitted **if one were
proven**", and proving one requires a `same_semantics_proof_ref`. This router never produces one,
so it never sets `fallback.used`.

## Ambiguity fails closed

When more than one admitted descriptor serves the same capability at the same contract version, the
router emits `NO_ELIGIBLE_ROUTE` rather than choosing.

Picking "the first", "the preferred" or "the most mature" is a ranked candidate chain wearing a
different hat. `ginkgo-library` does declare a discriminator — `routing_key` with `bindings[].when`
— and RK-07 does not project it, so no governed basis for choosing exists yet. Until one does,
refusing is the honest answer. Today this affects `commerce.orders.read`, which declares an Amazon
binding and a Shopee one.

## The pipeline

| Step | Fails closed with |
|---|---|
| capability lookup over the immutable catalog | `UNKNOWN_CAPABILITY` · `UNKNOWN_CONTRACT_VERSION` · `NO_ELIGIBLE_ROUTE` |
| lifecycle guard, per `execution_mode` | `BLOCKED_LIFECYCLE` |
| policy verdict, **recorded not re-decided** | `BLOCKED_POLICY` · `BLOCKED_EXTERNAL_GATE` · `BLOCKED_APPROVAL` |
| the pin: skill, version, kind, side-effect class, honest nulls | `NO_ELIGIBLE_ROUTE` |

A non-`ROUTED` decision names **no** implementation. Reporting the descriptor it *would* have
chosen would leak a route the ladder or the policy refused, and would read as a suggestion.

### Lifecycle, per mode

Transcribed from `lifecycle-and-promotion.md` §3 and the schema's own guards:

- `REJECTED` is terminal — not routable in **any** mode, certification included;
- `DEPRECATED` may still be certified (re-running a retired record reproduces historical
  evidence) but may not serve caller traffic;
- `certification` consults **neither** eligibility flag, and requires a recorded
  `certification_authorization_ref` instead. Without that escape a record would need the
  certificate to become routable and need to run to earn the certificate;
- `non_production` requires `routing_eligible`;
- `production` requires both flags.

### Policy: RK-04 decides, RK-02 records

> *"The `BLOCKED_POLICY`, `BLOCKED_APPROVAL` and `BLOCKED_EXTERNAL_GATE` outcomes on
> `RuntimeRouteDecision` are RK-02 reporting RK-04's decision in the audit record, never RK-02
> re-deciding it."*

RK-04 is **not implemented here** (`ai-stack#41`). `policy.py` defines the port and a `StaticPolicy`
stand-in that evaluates nothing.

Precedence when more than one applies: **denial → gates → approval**. Gates sit above approval
because invariant 5 makes a gate un-clearable *by* an approval — `APPROVED + BLOCKED ⇒ BLOCKED`,
`APPROVED + UNKNOWN ⇒ BLOCKED` — so reporting `BLOCKED_APPROVAL` there would name the wrong cause
and imply an approval would fix it.

`policy_snapshot_hash` is **computed here, not accepted here**. The port returns the pinned
snapshot; the router hashes it. Taking a hash the port asserted would let a policy authority claim
provenance for a snapshot nobody can check.

## The three hashes

| | Answers |
|---|---|
| `input_fingerprint` | what was asked about — *"never the input itself"* |
| `decision_inputs_hash` | what question was asked |
| `decision_hash` | what answer was given |

`decision_hash` excludes `decision_id`, `resolved_at`, `request_id`, `correlation_id` and `task_id`.
Every one differs per call by construction; hashing them would make `decision_hash` a unique id —
still valid, still apparently reproducible, and never once equal for two identical routings. The
requirement is *identical inputs produce an identical hash*, so what varies per call is outside it.

All three use the **type-tagged** fingerprint, not the catalog's canonical encoder. The catalog
refuses floats — correctly, a float in a projection is a defect. `constraints_applied.max_cost.amount`
is declared `number` in the frozen contract and a policy snapshot carries thresholds like
`minimum_multiplier: 5.0`, so a routing decision handles floats routinely.

`decision_id` and `resolved_at` are **injected** through `RoutingContext`, never generated. A router
that called a clock or a UUID factory would not be a pure function of its inputs.

## At the pinned commits, nothing routes

Asserted in `tests/test_rk02_pinned_corpus.py`, for two independent reasons:

1. **No admitted descriptor declares `side_effect_class`.** The frozen decision requires it on a
   `ROUTED` outcome and the router refuses to default it — a side-effect class is a governance
   statement about what an execution may do.
2. **Every admitted descriptor is `routing_eligible: false`**, sitting at `FEATURE` or `PROTOTYPE`.

Both are migration backlog for `ginkgo-agents` and `ginkgo-library`, not kernel defects. In
`certification` mode the ladder stops blocking and the *next* honest refusal surfaces — the missing
`side_effect_class` — which is the two reasons being genuinely independent.

## Prior art

The freeze/hash/pin model is generalized from `FrozenDecision` in `ginkgo-connect/packages/core`,
as `ai-stack#40` directs. Nothing is imported from it (BLK-17 `KEEP_SEPARATE`), and its
`rankedFallbacks` and `chosenExecutorRef` are absent by design rather than by omission — tests
assert both, over executable code with comments and docstrings stripped.

## Usage

```python
from ginkgo_runtime.router import DomainRouter, RoutingContext, StaticPolicy

router = DomainRouter(catalog=catalog, policy=policy, contracts=contracts)
decision = router.route(
    admitted_request,
    RoutingContext(
        execution_mode="non_production",
        decision_id="dec-...",
        resolved_at="2026-08-21T12:00:00Z",
    ),
)
```

`to_route_view(decision)` projects it into the narrow slice RK-01 consumes.
