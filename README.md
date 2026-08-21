# ginkgo-runtime

**Implementation of the Ginkgo Capability Runtime Kernel.**

> Status: **RK-07 Runtime Catalog implemented.** This repository was designated as the kernel
> implementation host on 2026-08-20 (BLK-16). The architecture it implements merged first:
> `ai-stack#45` (Capability Runtime Kernel v1) and `ai-stack#47` (static P0 conformance).
> Nothing else in the kernel exists yet.

Renamed from `ginkgo-infra`, which was an unused scaffold. History, MIT licence and private
visibility are unchanged.

## Boundary

Architecture and contracts are **not** owned here.

| Repository | Owns |
|---|---|
| `Ginkgo-Core/ai-stack` | architecture · P0 contracts · runtime topology · Docker composition · conformance authority |
| **`Ginkgo-Core/ginkgo-runtime`** | **the Capability Runtime Kernel implementation** |
| `Ginkgo-Core/ginkgo-agents` | Skill definitions · bindings · evals · Agent Foundry |
| `Ginkgo-Core/ginkgo-library` | Arsenal · Registry · provenance |
| `Ginkgo-Seeds/ginkgo-connect` | concrete adapters and connectors |

## Implemented

| Component | Status | Where |
|---|---|---|
| **RK-07 Runtime Catalog** | **implemented** | [`catalog/`](catalog/README.md) · [`src/ginkgo_runtime/catalog/`](src/ginkgo_runtime/catalog) |
| RK-10 runtime conformance | not started | `ai-stack#38` |
| RK-01 Capability Bus | not started | `ai-stack#39` |
| RK-02 Domain Router | not started | `ai-stack#40` |
| RK-04 Policy + Lifecycle Gate | not started | `ai-stack#41` |
| RK-08A Executor Contract | not started | `ai-stack#32` |
| RK-09 `system.echo.execute` | not started | `ai-stack#42` |
| RK-06 Evidence / Audit Writer | not started | `ai-stack#43` |

### Quick start

```bash
pip install -r requirements-dev.txt
PYTHONPATH=src python -m ginkgo_runtime.catalog build
PYTHONPATH=src python -m ginkgo_runtime.catalog reject     # the migration backlog
python -m pytest tests -q
```

The catalog builds offline from sources pinned by commit SHA. It opens no socket, reads no
credential and writes to no upstream registry.

### Stack

Python ≥ 3.11, standard library plus **PyYAML** — one runtime dependency, no framework, no build
step. Rationale and the options rejected: [`ADR-RT-001`](docs/adr/ADR-RT-001-implementation-stack.md).

## Will contain

Capability Bus runtime · Domain Router runtime · skill/binding resolution · policy and lifecycle
admission · normalized execution boundary · Evidence runtime · executor interfaces · conformance
implementation.

## Must never contain

```text
channel implementations (WhatsApp · Telegram · Discord · Email · Web)   UI
provider-specific business logic          Amazon / Shopee implementations
n8n workflows as architectural authority  ComfyUI workflows as a public capability
Ginkgo Library canonical metadata         secrets
runtime state embedded in files           a second TaskOps
a second Capability Bus                   a second registry
a second routing authority
```

## Dependency direction

```text
ginkgo-runtime  ->  ai-stack/contracts/core + contracts/runtime   (pinned by commit SHA)
ginkgo-runtime  ->  @connect/core                                  FORBIDDEN
ginkgo-connect  ->  a second routing authority                     FORBIDDEN
```

`ai-stack` depends on nothing, so the graph is acyclic by construction.

## Canonical references

Everything authoritative lives in `Ginkgo-Core/ai-stack`:

- `ARCHITECTURE-INVARIANTS.md` — the 18 frozen P0 invariants
- `contracts/core/` — `CapabilityRequest v1`, `CapabilityResult v1`, error, event, evidence, approval, external-gate
- `contracts/runtime/` — the internal runtime contracts this repository implements against
- `architecture/runtime/ownership-and-boundaries.md` — this repository's charter
- `architecture/runtime/channel-boundary.md` — `CHANNEL != AGENT != EXECUTOR != CAPABILITY != KNOWLEDGE`
- `docs/adr/ADR-005-capability-runtime-kernel-boundary.md`

Tracking: `Ginkgo-Core/ai-stack#35` (EPIC) · `Ginkgo-Core/ai-stack#37` (RK-07) ·
`Ginkgo-Core/ai-stack#45` (architecture, merged `1a580965`) ·
`Ginkgo-Core/ai-stack#47` (static P0 conformance, merged `b74ee58a`).

## Licence

MIT. See `LICENSE`.
