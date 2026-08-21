# The reference slice — RK-08A · RK-09 · RK-06 · RK-10

The first executable vertical slice of the Capability Runtime Kernel. It proves the **boundary**,
not the usefulness of the capability.

```text
CapabilityRequest v1
  → RK-01 Capability Bus
  → RK-02 Domain Router
  → RK-04 Policy + Lifecycle Gate
  → RK-08A Executor Contract
  → rk-reference-echo  (tool: rk-echo-transform)
  → RuntimeExecutionResult v1
  → CapabilityResult v1
  → exactly one Evidence v1  +  one Event v1 per transition
```

| | |
|---|---|
| capability | `system.echo.execute` |
| skill | `rk-reference-echo` |
| tool | `rk-echo-transform` |
| adapter | `null` + reason |
| provider | `null` + reason |
| execution mode | `certification` |
| network · writes · credentials | OFF · OFF · none |

## Run it

```bash
python -m ginkgo_runtime.slice run
python -m ginkgo_runtime.slice run --json      # the certificate
python -m ginkgo_runtime.slice emit --check    # is the committed one current?
```

## Files

| Path | What it is |
|---|---|
| `rk-reference-echo.descriptor.yaml` | the canonical `RuntimeSkillDescriptor v1`, pinned as a `role: kernel` catalog source |
| `certification-authorization.yaml` | the recorded authorization the ladder requires for a governed run |
| `e2e-certificate.json` | **generated.** What the run proved, and what it did not |
| `PROMOTION-RECOMMENDATION.md` | the separate recommendation. It is not a promotion |

## Why `system.*` and why here

`system.*` is a **reserved kernel-diagnostics namespace**, not a business domain, and it must never
accumulate business semantics. That is why the descriptor lives in this repository rather than in
`ginkgo-agents`, and why the tool registry has exactly one entry — adding a second is a review, not
a config change.

`system.echo` would be invalid: the frozen capability pattern requires a minimum of three
dot-separated segments. `system.echo.execute` is the minimal conformant repair.

## Three entities exist here, and two honestly do not

| Role | Present? | Identity |
|---|---|---|
| CAPABILITY | yes | `system.echo.execute` — what the caller names |
| SKILL | yes | `rk-reference-echo` — what RK-02 resolves to |
| TOOL | yes | `rk-echo-transform` — the concrete callable |
| BINDING | **no** | `kind: deterministic_domain_skill` folds skill and binding into one record |
| ADAPTER | **no** | `null` + reason — no integration boundary exists |
| PROVIDER | **no** | `null` + reason — the tool is a pure function |

Inventing an adapter identity to fill the generic path would misrepresent the implementation. This
is the whole reason `RuntimeSkillDescriptor` supports honest nulls.

## Time is an input

Three frozen contracts require a timestamp. None is read from the machine's clock: `RuntimeClock`
is a port, and the certificate supplies a `SteppingClock`.

That is what makes the certificate mean something. A run whose timestamps came from `now()` could
not be re-derived, so "deterministic evidence fingerprint" would be a claim nobody could check.
With time as an input, re-running the slice is a **verification** rather than a second opinion.

## `FEATURE` runs, and stays `FEATURE`

`rk-reference-echo` is `FEATURE` with `routing_eligible: false` — and it routes, because
`certification` consults neither eligibility flag and carries a recorded authorization instead.
Without that escape the record would need the certificate to become routable and need to run to
earn the certificate.

The same record is `BLOCKED_LIFECYCLE` in `production` and in `non_production`. It is subject to
exactly the same lifecycle law as a business skill; that is the point of it.

**Nothing here promotes anything.** See [`PROMOTION-RECOMMENDATION.md`](PROMOTION-RECOMMENDATION.md).
