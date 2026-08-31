# ADR-RT-004 — The live n8n transport, behind one socket and one failure table

- **Status:** Accepted
- **Date:** 2026-08-31
- **Scope:** `Ginkgo-Core/ginkgo-runtime` only. Decides nothing about architecture or contracts;
  `ai-stack` remains the authority for both. No Ginkgo Ops change, no Callima change, no Hermes,
  no OpenClaw, no Capability Bus redesign.
- **Supersedes nothing.** ADR-RT-003 stands; this is the B-half it named.

## Context

ADR-RT-003 split RK-08B in two and landed the A-half: a second implementation behind the RK-08A
Executor Contract whose transport is a **port**, with `RecordedTransport` as the only shipped
implementation. It closed by listing six pre-conditions the B-half would inherit. This is the
B-half, and each of those six is answered below.

The gate RK-08B/B is asked to move is narrow: *the smallest real transport necessary for*
`Runtime -> local n8n -> minimal pinned workflow -> deterministic response -> normalized result`.
Not Callima. Not a capability. Not a production route.

## Decision

**Three modules, and the split between them is the decision.**

| module | has a socket | holds |
|---|---|---|
| `executor/n8n.py` | no | the Executor Contract half: state mapping, projection, and **`N8N_TRANSPORT_FAILURES`** |
| `executor/n8n_transport.py` | no | URL construction, credential resolution, correlation, identity verification, structural validation, dispatch evidence |
| `executor/n8n_http_client.py` | **yes** | byte transfer and the operating system's error table |

Rule 11 — *network access must exist only inside the concrete transport implementation* — is
enforced structurally rather than by convention. `tests/test_boundaries.py` scans every module in
the package for a network import with **exactly one** named exemption, asserts that the exemption
list has length one, asserts that the exempt module really does import `http.client` (an exemption
pointing at an innocent module would pass vacuously), and asserts in a subprocess that
`import ginkgo_runtime.executor` does not load it. Acquiring a socket anywhere else means editing
a test.

**Normalisation stays in the Contract.** `N8N_TRANSPORT_FAILURES` is a closed table in `n8n.py`
mapping a failure *class* to a state, code, category, retryability and message. The transport
decides which class of thing went wrong; it does not decide what a caller is told. A transport
choosing its own error codes would be normalising, and a second transport would then be free to
normalise differently — which is exactly the drift the Executor Contract exists to prevent.

**`N8nTransportError` takes no message.** Its constructor accepts a failure class and nothing else,
so a raise site has nowhere to put an upstream string. Every caller-facing message is written in
`n8n.py`, in advance, with no upstream input in scope at the point it is written.

## The six leak paths a live transport opens

RK-08B/A closed the credential boundary over *documents*. A socket adds six surfaces that no
document boundary can see. Each is closed, and each has a test in
`tests/test_rk08b_transport_security.py` driven by a corpus of nine published credential shapes —
every one of which the boundary's own value rules recognise, asserted, because a leak test whose
secret is an unremarkable string proves that unremarkable strings do not appear.

1. **HTTP error bodies.** Not read. Not decoded. Not looked at. A non-2xx response is normalised
   from its status code alone. This is the richest leak path in a live bridge — n8n's error bodies
   quote the request that failed, header block included.
2. **Response headers.** Carried across the port and never read, which is deliberate: "the
   transport does not read headers" is only worth asserting if the headers were available to read.
3. **Exception text.** Every handler in both modules captures a failure *class* and raises
   **outside** the handler. `raise ... from None` was not enough: it clears `__cause__` and
   suppresses the printed chain but leaves the original on `__context__`, where anything that walks
   the chain finds an `OSError` carrying the OS error table's text or a `JSONDecodeError` carrying
   the body it failed on. Raising outside the handler is the difference between suppressed and
   discarded, and a test asserts `__context__ is None`.
4. **URLs.** `N8nEndpoint` refuses a userinfo component and refuses a query string or fragment
   outright, at construction. Not because a query string is always a secret, but because it is
   where one is habitually hidden — refusing the shape is cheaper than being right about the
   contents. The URL never enters evidence; a sha256 of it does.
5. **The n8n response payload.** Unchanged from RK-08B/A: one declared path is projected, the
   engine-internal key check fails the execution, and the outbound credential boundary refuses to
   build a result at all.
6. **Diagnostics and evidence.** `N8nDispatchRecord` has eleven fields, all identifiers, booleans,
   integers or digests. No URL, no headers, no body, no error text — **absent, not redacted**, which
   is the only version of the claim that survives someone widening a redaction later. The joined
   evidence document is scanned by the same boundary as a result before it is returned.

One finding came out of writing those tests rather than out of review: `EnvironmentCredentialResolver`
is a dataclass holding an environment, so its generated `__repr__` published every variable in the
process into any traceback that happened to include a transport. `repr=False` on that field is the
fix, and the test that found it is the one that keeps it fixed.

## What the workflow pin actually pins

Two things, and they are different.

**`declared_identity`** is baked into the committed workflow's `Set` node and is **not sent by the
transport**. The transport compares what came back to what the pin says. A design that sent the
expected identity and compared the echo would be comparing a value with itself; this one is a real
check, and `test_the_pinned_identity_is_never_sent_so_the_check_is_a_real_check` asserts the
transport's whole outgoing request does not contain it.

**`artifact_sha256`** pins the committed workflow *document*. The transport does not use it — there
is no way to ask a running n8n for the bytes it imported — but the smoke runner refuses to run if
the file on disk does not hash to it, so "the pinned workflow" names a specific set of bytes rather
than whatever is in the UI today.

n8n's own database id for the workflow is deliberately **not** pinned: it changes on re-import, and
an identity that changes when nothing changed is not an identity.

## Two deadlines, not one

`http.client` rather than `urllib.request`, because a connect deadline and a request deadline answer
different questions. A connection nothing accepts means n8n is not running and should be reported in
about two seconds; a connection that is accepted and answers slowly is a workflow doing work and gets
ten. `urllib.request` exposes a single `timeout` covering both, which would force one number to stand
for two questions. This is also the wall-clock enforcement ADR-RT-003 listed as pre-condition 4: it
now exists where there is something to interrupt, and it normalises to the same
`timed_out` / `TIMEOUT` pair the poll budget already produced — so a caller cannot tell a socket
deadline from a budget exhaustion, and does not need to.

## Options rejected

**A. `urllib.request`, or a `requests` dependency.** Rejected. `requests` breaks ADR-RT-001's single
runtime dependency; `urllib.request` collapses two deadlines into one and, through `urlopen`, brings
redirect following and proxy handling that a transport must not have — a redirect is a re-route.

**B. Let the transport pick its own error codes.** Rejected. That is normalisation, and normalisation
is the Contract's. A second transport would then be free to disagree with the first about what a
timeout is called, and the equivalence gate would be comparing two vocabularies.

**C. Include the response body or status message in the error, truncated.** Rejected, for the reason
RK-08B/A rejected quoting a status string: a truncated echo is still an echo, and
`error.schema.json` says provider payloads must not appear in `message` or `details`.

**D. Redact credentials out of the dispatch record.** Rejected. The record has no field they could
occupy. A redaction is a decision someone can widen; an absent field is not.

**E. Discover the endpoint — probe `localhost:5678`, or fall back to a conventional address.**
Rejected, twice over. Selecting a reachable executor is the Executor Router's authority, not a
transport's (rule 5); and a smoke that guesses an endpoint is a smoke that eventually guesses a
production one. The endpoint is configured or the run stops.

**F. Poll for an asynchronous execution.** Rejected as out of scope. The pinned workflow answers
synchronously through a *Respond to Webhook* node, so the first observation is terminal. `poll`
raises rather than returning a fabricated `running`, which would turn a misconfiguration into a
timeout.

**G. Run the smoke in CI, or against whatever is on `localhost:5678`.** Rejected. See below.

## The smoke, and why it is refused by default

`python -m ginkgo_runtime.smoke` is the one place a deliberate live dispatch is arranged, and it is
gated three times: an opt-in variable set nowhere in this repository (a test asserts that), an
explicitly named endpoint, and a workflow document that hashes to its pin. `preconditions` reports
every gate at once and contacts nothing. A refused run has not even *imported* the module that owns
the socket — the CLI imports it after the gates, and a test asserts it in a subprocess.

The pinned workflow is three nodes: a webhook, a `Set` that writes a constant envelope, and a
`Respond to Webhook`. It makes no third-party call, reads and writes no CRM data, touches no
Callima workflow, uses no LLM, no AnythingLLM and no ComfyUI, has no credential on any node, and
sets `saveDataSuccessExecution: none` so a run leaves nothing behind. Those claims are asserted
against the document itself in `test_the_pinned_workflow_calls_nothing_and_stores_nothing`, not
only stated in its README — a runbook can say it, but only a test keeps it true when someone adds a
node.

Exit code 2 (refused) is distinct from exit code 1 (ran and failed) because "the runtime never
contacted anything" and "the runtime contacted n8n and the answer was wrong" send an operator to
different places.

## ADR-RT-003's six inherited pre-conditions

1. **Re-record the fixtures against the real instance.** *Not done, and not required by this
   change.* The recorded fixtures still say they are hand-written. What RK-08B/B adds is a
   *live-shaped* path tested against a fake socket, and the document shape it validates is the
   kernel's own `ginkgo` envelope — which the committed workflow produces — rather than n8n's
   internal execution document. Diffing against a real capture happens with the live smoke.
2. **Decide where the bridge speaks from.** *Answered:* an explicitly configured `N8nEndpoint`,
   never discovered. Where that is, is a deployment decision the runtime does not make.
3. **A workflow in the real instance.** *Provided as a committed, disposable, pinned document.*
   Importing it is an operator action, deliberately, because importing a workflow into a live
   automation host is a mutation of the operator's system.
4. **Wall-clock timeout enforcement.** *Done.* Two deadlines, above.
5. **Teardown measurement.** *Not done, and out of scope.* "0 containers at teardown" is a claim
   about a deployment; this change starts no container and stops none.
6. **An independent validator.** *Done.* The smoke injects one that passes only on exactly
   `{"value": "PONG"}`. A live `succeeded` is not the bridge's own word for it — GL-07 — and a
   workflow that reports `success` while returning the wrong value fails the smoke.

## Status this change does and does not confer

`RK-08B-B` is **implemented and tested offline**, over fake transports, with no test in this
repository opening a socket. It is **not** validated against a live engine: no live smoke has been
run, no workflow has been imported into any instance, and no measurement exists.

`lifecycle_status` stays **FEATURE**. `routing_eligible` stays **false**. `production_eligible`
stays **false**. A transport that is implemented is not a transport that is proven, and existing
code is not validation.
