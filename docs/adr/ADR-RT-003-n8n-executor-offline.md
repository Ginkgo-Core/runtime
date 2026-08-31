# ADR-RT-003 — The n8n bridge lands offline first, behind a transport port

- **Status:** Accepted
- **Date:** 2026-08-31
- **Scope:** `Ginkgo-Core/ginkgo-runtime` only. Decides nothing about architecture or contracts.
  `ai-stack` remains the authority for both.
- **Context:** `ai-stack#44` (RK-08B) is open and ready. Its two documented blockers —
  `ai-stack#7` and `ai-stack#8`, which declared n8n the control plane — are closed without merge
  and are reference material only, so invariant 11 is no longer contested by an open PR.
  `architecture/runtime/executor-contract.md` §6 states the promotion gate in one line:
  *"same TaskOps job executed by n8n and a stub executor with equivalent normalized result"*.

## Decision

**Split RK-08B in two, and land only the first half.**

- **RK-08B-A — this change.** A second implementation behind the RK-08A Executor Contract,
  `N8nExecutor`, whose transport is a **port**. The kernel ships exactly one implementation of that
  port: `RecordedTransport`, which replays observations. No socket, no host, no credential value,
  no Docker, and no change to any Callima workflow, Evolution service or compose file.
- **RK-08B-B — not this change.** Detecting the existing n8n, selecting a live route, wall-clock
  timeout enforcement, cross-process teardown measurement, queue-mode and encryption-key parity.

## Why the split

**The gate is about normalisation, not about reachability.** What #32 asks to be proven is that
two executors produce equivalent normalized results. Equivalence is a property of the mapping —
n8n's status vocabulary onto the frozen executor states, its response document onto
`RuntimeExecutionResult v1` — and every part of it is decidable offline. Reaching a live engine
would add a network to the proof without adding anything to what is proven.

**A live bridge is a different proof with a different failure mode.** "0 containers at teardown",
queue mode via Redis/BullMQ, and `N8N_ENCRYPTION_KEY` parity across `n8n` and `n8n-worker` are
*measured* claims. Measured claims belong in a change whose evidence is a measurement.

**Callima is running.** The baseline is `WhatsApp → Evolution → n8n → LLM/tools → resposta`, and it
works. A change that cannot touch a workflow, cannot open a connection and cannot read a credential
also cannot disturb it. That is worth more, this week, than a live smoke test.

## What this constrains

**The transport is the only seam.** `N8nExecutor` imports nothing that can open a connection, and a
test asserts that statically over the module's own AST — so acquiring the ability requires adding an
import, and the import fails the suite before it fails anything else.

**The binding is internal and stays internal.** `tool_id` arrives from the `RuntimeRouteDecision`;
the binding it resolves to — workflow reference, credential references, output path — lives in a
registry inside the executor, exactly as the in-process tool registry does. It never enters the
envelope: `RuntimeExecutionRequest v1` lists `engine`, `workflow_id`, `node_id`, `endpoint` and
`credential` among its `x-ginkgo-forbidden-fields` and sets `additionalProperties: false`, so the
ban is structural rather than a rule someone has to remember.

**The output is projected, never forwarded.** One declared path out of the observation. A
pass-through would carry `workflowId`, node names and run data into `CapabilityResult.output`,
which is what invariant 17 exists to prevent. A projection that still contains an engine-internal
key **fails the execution** rather than being scrubbed: a scrub hides a misconfigured binding, and
a failure names it.

**Provider text is never quoted.** `error.schema.json`: *"Secrets, credentials, raw endpoints and
provider payloads MUST NOT appear in message or details."* The bridge reports the failure class it
observed and nothing the engine wrote.

**An unmapped status is a failure.** `N8N_STATUS_TO_STATE` is explicit and closed. Anything outside
it is `failed` / `INTERNAL`, never success.

## Options rejected

**A. Bridge to the live n8n now.** Rejected: it makes the first executor substitution proof depend
on a running container, an encryption key and a workflow that would have to be created inside the
instance Callima uses.

**B. A mock inside the tests instead of a port in the module.** Rejected: a mock proves the tests
can be written, not that the production path has a seam. A port is the seam, and RK-08B-B satisfies
it without editing this module.

**C. Scrub engine-internal keys out of the output.** Rejected. Fail closed. A binding whose declared
output path points at the engine's own block is a defect in the binding.

**D. Let the bridge report `independent_validation.satisfied: true` for its own successes.**
Rejected — it is the exact thing `GL-07` forbids: *"Nenhum Goal pode ser marcado como DONE apenas
porque o Executor declarou conclusão."* Without an injected validator the bridge reports `None`, and
RK-01's veto turns the run into an `ERROR`.

## Status this change does and does not confer

`RK-08B-A` is **implemented and tested offline**. It is **not** validated against a live engine and
**not** production eligible; no lifecycle status of any skill or descriptor changes here, no
workflow is registered, and no route becomes eligible. Existing code is not validation — the
evidence is the test run, and the test run is offline.

## Pre-conditions RK-08B-B inherits

1. Re-record the fixtures against the real instance and diff the document shape; the recordings here
   are hand-written and say so.
2. Decide where the bridge speaks from — the VM that mounts these folders has no Docker and cannot
   see `localhost:5678`.
3. A workflow in the real instance that implements the bound skill, created without touching any
   Callima workflow.
4. Wall-clock timeout enforcement, which belongs where there is something to interrupt.
5. Teardown measurement, so "0 containers at teardown" is evidence rather than an intention.
6. An independent validator, so a live `succeeded` is not the bridge's own word for it.
