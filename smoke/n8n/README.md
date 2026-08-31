# The RK-08B/B live smoke workflow

`ginkgo-rk08b-ping.workflow.json` is the **entire** workflow the live smoke uses.
Three nodes: a webhook, one `Set` node that writes a constant result envelope, and a
`Respond to Webhook`.

It exists so that a live smoke proves the *transport* and proves nothing else. What it
does **not** do is the point of it:

- no HTTP Request node, so **no third-party API call**;
- no database, Postgres, Sheets or CRM node, so **no CRM data is read or written**;
- no Callima workflow is referenced, imported, called or modified;
- no LLM, no AnythingLLM, no ComfyUI, no Evolution, no WhatsApp;
- no credential of any kind is attached to any node;
- `saveDataSuccessExecution` and `saveDataErrorExecution` are `none`, so a smoke run
  leaves no execution data behind in the n8n database.

Its behaviour is total and deterministic: `{"input": {"value": "PING"}}` in,
`{"ginkgo": {"workflow": "ginkgo.rk08b.ping@1", "status": "success",
"correlation_id": <echoed>, "output": {"value": "PONG"}}}` out. Any other input
produces `status: "error"`, which the bridge normalises to a `failed` execution.

## The two pins

**`workflow: "ginkgo.rk08b.ping@1"`** is baked into the `Set` node and is not sent by
the transport. `N8nHttpTransport` compares what came back to
`WorkflowPin.declared_identity`; a mismatch is `N8N_WORKFLOW_MISMATCH` and fails
closed. Because the transport never sends the identity, the check is a real check
rather than a comparison of a value with itself.

**`artifact_sha256`** pins *this file*. The smoke runner refuses to run if the file on
disk does not hash to the digest the runner was given, so "the pinned workflow" names a
specific set of bytes and not a workflow someone edited in the n8n UI afterwards.

Print the digest with:

    python -m ginkgo_runtime.smoke pin

## Importing it

Import through the n8n UI (*Workflows -> Import from File*), or with the n8n CLI inside
the container:

    n8n import:workflow --input=ginkgo-rk08b-ping.workflow.json

Then **activate** it. Deactivate or delete it when the smoke is done: it is disposable
and nothing else in this repository depends on it existing.

n8n assigns its own database id on import and that id is deliberately not pinned —
re-importing it would change, and the identity that matters is the one inside the
document.
