# RK-04 — Policy + Lifecycle Gate

The component that makes the kernel fail-closed, and the **only** place invariant 16
(`FEATURE` never executes in production) and invariant 5 (approval and external gate are
independent) are enforced.

Specification: [`ai-stack#41`](https://github.com/Ginkgo-Core/ai-stack/issues/41) ·
normative matrix: [`lifecycle-and-promotion.md` §3](https://github.com/Ginkgo-Core/ai-stack/blob/master/architecture/runtime/lifecycle-and-promotion.md) ·
EPIC [`ai-stack#35`](https://github.com/Ginkgo-Core/ai-stack/issues/35)

```text
catalog record + execution mode + policy context
  + approval state + external gate state + deadline/cost context
    -> ALLOW | BLOCK  +  structured reason  +  decision ref/hash
```

**One pure function.** No mutable state, no API, no clock, no I/O. Same inputs, same verdict, same
hash — which is what lets a decision be pinned into a routing record and re-derived after a restart.
The normative tables are `MappingProxyType`, so the purity is structural rather than conventional.

## What it does not do

| | |
|---|---|
| holds no state | approval and gate **state** live in PostgreSQL (invariant 9); the catalog carries *declarations*. Both arrive as inputs |
| mutates nothing | never transitions an approval, never clears a gate |
| evaluates no business policy | the verdict arrives already decided — "policy context" is an input. RK-04 records it and never overturns a denial |
| re-derives no knowledge-corpus eligibility | `#19` owns it |
| accepts no caller override | `caller_may_override` is an assertion, not a switch |

## The canonical matrix

Transcribed cell by cell from §3, *"the single normative source. Where any other document
disagrees, this table wins."*

| Stage | `certification` | `non_production` | `production` |
|---|---|---|---|
| `FEATURE` | permitted, governed | no | no |
| `PROTOTYPE` | permitted, governed | no | no |
| `VALIDATED` | permitted | permitted | no |
| `INTEGRATED` | permitted | permitted | no |
| `PRODUCTION` | permitted | permitted | yes |
| `DEPRECATED` | permitted | no | no |
| `REJECTED` | no | no | no |

§3 defines the three values, and the definitions carry the design: **no** means *at any stage of
governance*. That clause is why `REJECTED` and `DEPRECATED`'s caller-traffic cells cannot be reached
by any amount of authorization — "no" is not "no unless someone signs for it".

The stage also **bounds** the two eligibility flags without setting them: *"A record may be more
restrictive than its stage allows, and often is."* So a `VALIDATED` record declaring
`routing_eligible: false` is genuinely not routable, and a `FEATURE` record declaring it `true` is
malformed.

All twenty-one cells are walked in `tests/test_rk04_matrix.py`, with the expectations written out
longhand rather than derived from the table — a derived expectation would pass no matter what the
code said.

### One reconciliation

§3 marks only `FEATURE` and `PROTOTYPE` as *permitted, governed*, so on the matrix alone a
`VALIDATED` certification run would need no authorization. But
`runtime-route-decision.schema.json`'s `allOf` rule 7 requires `certification_authorization_ref` on
**every** `ROUTED` certification decision.

The gate takes the stricter reading, because rule 7's own `$comment` gives a reason that does not
depend on the stage: certification *"is the one mode whose ROUTED guard consults neither lifecycle
flag — without a recorded authorization it would be an unaudited bypass of the ladder."* Requiring
it only in the matrix cells would also leave RK-02 unable to emit a valid artifact, raising where a
refusal belongs.

## Ordered, first-match-wins, default DENY

The shape `ai-stack#41` says to reuse, proven on `master` by
`architecture/policy/knowledge-corpus-classification.yaml`. `reasons.py` **is** the algorithm's
order — `evaluate` walks it — so reading it top to bottom tells you which reason a given input
produces, and a caller sees the *first* thing wrong rather than an arbitrary one.

```text
MALFORMED_GATE_INPUT                        input the gate cannot read at all
UNKNOWN_EXECUTION_MODE                      ┐
UNKNOWN_LIFECYCLE_STATUS                    │ a vocabulary the gate cannot read
UNKNOWN_APPROVAL_STATE                      │ is a denial
UNKNOWN_EXTERNAL_GATE_STATE                 ┘
LEGACY_APPROVAL_FIELD_PRESENT               `human_approval`, including `supervised`
FALLBACK_POLICY_NOT_FROZEN                  `explicit` is not a frozen policy
CALLER_OVERRIDE_REJECTED
LIFECYCLE_FLAGS_EXCEED_STAGE_CEILING
LIFECYCLE_MODE_DENIED                       the matrix cell
CERTIFICATION_AUTHORIZATION_MISSING
ROUTING_ELIGIBLE_REQUIRED
PRODUCTION_ELIGIBLE_REQUIRED
POLICY_DENIED                               the supplied verdict
EXTERNAL_GATE_NOT_CLEAR                     before approval, and independent of it
DOMAIN_DECISION_IS_NOT_GOVERNANCE_APPROVAL
APPROVAL_NOT_GRANTED
COST_CEILING_CURRENCY_MISMATCH
UNCOVERED                                   the terminal default
```

Each reason also fixes the `BLOCKED_*` outcome the Domain Router records. That mapping lives in one
place because a reason and the outcome it justifies must not drift apart.

## Approval ≠ External Gate

Gates are evaluated **before** approval and with no reference to it, so an `APPROVED` approval can
never reach a blocked gate:

- `APPROVED + BLOCKED ⇒ BLOCK`
- `APPROVED + UNKNOWN ⇒ BLOCK`

The outcome names the gate rather than the approval, because reporting `BLOCKED_APPROVAL` would
imply that granting the approval would unblock the route. Verbatim from
`PROJETO/AGENT_EXECUTION_STANDARD.md` §2, as `#41` directs:

> *"Reviewer is **not** an External Gate and cannot clear one. Human/business Approval is also
> separate from External Gate state. A successful review cannot convert `external_gate=BLOCKED`
> into an executable route."*

An `APPROVED` that came from a **domain** decision is not a governance approval.
`commerce.product.evaluate`'s economic verdict is the case `#41` names: it grants no authority to
publish, purchase, mutate pricing or inventory, or fulfil orders.

## Two vocabularies, on purpose

`verdict` is the gate's answer — `ALLOW | BLOCK`. `policy_decision` is what goes into
`RuntimeRouteDecision.policy.decision`, whose frozen enum is `ALLOW | DENY`.

They differ deliberately: a record blocked by its **lifecycle** gets `verdict: BLOCK` while
`policy_decision` stays `ALLOW`, because no policy denied anything. Collapsing them would write a
governance verdict into the audit record that no policy authority ever gave.

## Nothing frozen is restated

Every enum is read from its pinned schema: the approval and gate `state` enums, the fallback
enum, the lifecycle ladder and the execution modes. The §3 matrix is the one value transcribed,
because it is prose — the same treatment RK-07 gives the ceiling matrix and RK-01 the
forbidden-field list.

## Relationship to RK-02

> *"RK-04 **decides**; RK-02 **records the pinned verdict** and stops."* — `capability-runtime-kernel-v1.md` §5

The Domain Router delegates here. It previously carried its own copy of the §3 matrix and its own
approval/gate precedence; two copies of a normative table is the drift the architecture keeps
warning about, so the copy was removed rather than kept in sync.

## Usage

```python
from ginkgo_runtime.gate import GateRequest, PolicyContext, evaluate

decision = evaluate(
    GateRequest(
        record=catalog_record,
        execution_mode="non_production",
        policy=PolicyContext(policy_id="...", policy_version="7", decision="ALLOW", snapshot={...}),
        approval=approval_state,
        external_gates=gate_states,
        constraints=constraint_context,
    ),
    contracts,
)

decision.verdict            # ALLOW | BLOCK
decision.reason_code        # the first matching rule, or None
decision.outcome            # BLOCKED_* for the Domain Router, or None
decision.decision_ref       # policy_id@version#hash
decision.cost_ceiling       # what RK-08A then enforces
```
