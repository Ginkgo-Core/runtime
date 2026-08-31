# Runbook — the RK-08B/B live n8n smoke

**This procedure is opt-in and is not run by CI, by the test suite, or by any tooling in this
repository.** Nothing here happens by accident: the smoke refuses to dispatch until you have set an
environment variable, named an endpoint, and imported a workflow yourself.

What it proves: that the runtime reaches a real n8n, that a pinned workflow answers, and that what
comes back normalises to a `RuntimeExecutionResult v1` the way the offline tests say it does.

What it does not prove and does not attempt: anything about Callima, any capability, any provider,
any route, or any production eligibility.

---

## What the smoke does to your instance

- **Adds** one disposable workflow, `ginkgo-rk08b-ping`, which you import and delete.
- **Sends** one HTTP POST to that workflow's webhook.
- **Nothing else.**

The workflow is three nodes — a webhook, a `Set` node that writes a constant envelope, and a
`Respond to Webhook`. It has:

- no HTTP Request node → **no third-party API call**;
- no database, Postgres, Sheets or CRM node → **no CRM data read or written**;
- no reference to any Callima workflow, and it calls no other workflow;
- no LLM, no AnythingLLM, no ComfyUI, no Evolution, no WhatsApp;
- **no credential attached to any node**;
- `saveDataSuccessExecution: none` and `saveDataErrorExecution: none`, so a run leaves no execution
  data in the n8n database.

Those are not promises in prose — `tests/test_rk08b_live_smoke.py::test_the_pinned_workflow_calls_nothing_and_stores_nothing`
asserts every one of them against the document.

Behaviour is total and deterministic: `PING` in, `PONG` out.

---

## Step 0 — check the gates without contacting anything

```
PYTHONPATH=src python -m ginkgo_runtime.smoke preconditions
```

This opens no socket. It prints one line per gate:

```
MISSING opt-in            -- set GINKGO_N8N_LIVE_SMOKE=1 ...
MISSING endpoint          -- supply the n8n base URL with --base-url or GINKGO_N8N_BASE_URL ...
PASS    workflow-artifact -- smoke/n8n/ginkgo-rk08b-ping.workflow.json sha256=...
PASS    credential-reference
UNVERIFIABLE workflow-imported
```

`UNVERIFIABLE` is honest rather than lenient: whether the workflow is imported and active in your
instance cannot be known from here. It is the one gate the smoke does not enforce, because the only
way to check it is to dispatch — and a 404 comes back as `N8N_WORKFLOW_NOT_FOUND` rather than being
guessed at.

Exit code 2 means at least one blocking gate is unsatisfied.

---

## Step 1 — verify the workflow document

```
PYTHONPATH=src python -m ginkgo_runtime.smoke pin
```

Prints the sha256 of `smoke/n8n/ginkgo-rk08b-ping.workflow.json`. The smoke recomputes this before
every dispatch and refuses if the file has changed, so the digest you import is the digest the
evidence names.

---

## Step 2 — import the workflow

Import `smoke/n8n/ginkgo-rk08b-ping.workflow.json` into your n8n instance and **activate** it:

- UI: *Workflows → Import from File →* select the document *→* toggle **Active**.
- CLI, inside the container: `n8n import:workflow --input=ginkgo-rk08b-ping.workflow.json`

Do not edit it. An edited workflow is a different workflow, and the smoke's evidence would be about
something that is not in this repository.

The webhook answers at `<base-url>/webhook/ginkgo-rk08b-ping`. If your instance serves webhooks under
a different prefix, that is a deployment fact the smoke does not guess at — the path is pinned in
`SMOKE_WORKFLOW_PATH` and changing it is a code change, not a flag.

---

## Step 3 — run it

```
GINKGO_N8N_LIVE_SMOKE=1 \
PYTHONPATH=src python -m ginkgo_runtime.smoke ping --base-url http://127.0.0.1:5678
```

Add `--json` for the full report, including the dispatch evidence.

If your instance requires authentication for webhook calls, bind a **governed reference** rather
than pasting a token:

```
GINKGO_N8N_LIVE_SMOKE=1 \
GINKGO_N8N_CREDENTIAL_REF=cred-ref-n8n-local \
GINKGO_N8N_CREDENTIAL_CRED_REF_N8N_LOCAL=<the api key> \
PYTHONPATH=src python -m ginkgo_runtime.smoke ping --base-url http://127.0.0.1:5678
```

The reference is what the binding holds; the material is resolved at the transport boundary, written
into exactly one request header, and appears in no report, no journal, no evidence record and no
exception. If the reference does not resolve, the dispatch **fails closed** — it never falls back to
an unauthenticated call.

The base URL must carry no userinfo (`http://user:pass@…`) and no query string. Both are refused at
configuration, because both are where a token gets hidden in a URL.

---

## Reading the result

```
state              succeeded
output             {"value": "PONG"}
independent_valid  {"required": true, "satisfied": true, "validator_ref": "rk08b-b-ping-pong-validator"}
dispatch_attempted True
dispatch           workflow=ginkgo.rk08b.ping@1 verified=True outcome=observed status=200
PASS
```

`satisfied: true` comes from a validator that accepts only exactly `{"value": "PONG"}` — not from
n8n's own word for it. A workflow that reports `success` while returning something else produces
`PASS`-less output and exit 1. GL-07: an executor's self-reported success is not evidence of success.

### Exit codes

| code | meaning | where to look |
|---|---|---|
| 0 | PING → PONG, independently validated | done |
| 1 | dispatched, and the answer was wrong | the workflow, or the `error_code` in the report |
| 2 | refused; **nothing was dispatched** | the gate named in the message |

### Failure codes you may see

| code | means |
|---|---|
| `N8N_CONNECTION_REFUSED` | nothing is listening at that endpoint, or the host does not resolve |
| `N8N_TRANSPORT_TIMEOUT` | the connect or request deadline elapsed (state `timed_out`) |
| `N8N_WORKFLOW_NOT_FOUND` | 404 — the workflow is not imported, not active, or the path differs |
| `N8N_UNAUTHORIZED` | 401/403 — the instance wants credentials the binding did not supply |
| `N8N_WORKFLOW_MISMATCH` | something answered, and it is **not** the pinned workflow |
| `N8N_MALFORMED_RESPONSE` | the body was not JSON |
| `N8N_INVALID_RESPONSE` | JSON, but not a `ginkgo` result envelope |
| `N8N_CORRELATION_MISMATCH` | the answer carried a different correlation id |
| `N8N_WORKFLOW_FAILED` | the workflow ran and reported failure |

None of these quotes anything n8n wrote. That is deliberate — see ADR-RT-004 — so when you need the
engine's own words, read them in n8n's execution log, not in the runtime's error.

---

## Step 4 — clean up

Deactivate and delete `ginkgo-rk08b-ping`. It is disposable and nothing in this repository depends
on it existing.

---

## What a green smoke does and does not confer

A green run is evidence that the transport reaches a live engine and normalises one deterministic
response. It is **not**:

- a capability, a route, or a production eligibility;
- evidence about any Callima workflow, any provider, or any real skill;
- a teardown measurement, a queue-mode parity check, or an encryption-key parity check.

`lifecycle_status` stays `FEATURE`, `routing_eligible` stays `false` and `production_eligible` stays
`false` until a separate change makes an argument for moving them and carries the evidence for it.
