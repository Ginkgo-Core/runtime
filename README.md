# ginkgo-runtime

**Implementation of the Ginkgo Capability Runtime Kernel.**

> Status: **EMPTY — no implementation exists yet.** This repository was designated as the kernel
> implementation host on 2026-08-20 (BLK-16). No code has been written, and none may be added
> without the architecture in `Ginkgo-Core/ai-stack` being merged first.

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

## Will contain

Capability Bus runtime · Domain Router runtime · skill/binding resolution · policy and lifecycle
admission · Runtime Catalog consumer · normalized execution boundary · Evidence runtime · executor
interfaces · conformance implementation.

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

Tracking: `Ginkgo-Core/ai-stack#35` (EPIC) · `Ginkgo-Core/ai-stack#45` (architecture PR).

## Licence

MIT. See `LICENSE`.
