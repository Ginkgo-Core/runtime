# Media Provider Runtime v1

This repository implements runtime mechanics only. Architecture authority remains in `Ginkgo-Core/ai-stack`.

## Boundary

Media providers are internal implementation adapters behind the Capability Bus and Media Domain Router.

```text
CapabilityRequest
  -> Capability Bus
  -> Domain Router
  -> internal binding
  -> MediaProvider adapter
  -> provider/local engine
```

A `MediaProvider` must not decide which public capability should be executed. It only reports what it can do and executes an already-authorized internal request.

## Core interface

`src/ginkgo_runtime/media/provider.py` defines the minimal provider protocol:

- `health()`
- `discover()`
- `capabilities()`
- `models()`
- `submit()`
- `status()`
- `cancel()`
- `upload()`

Adapters may be synchronous internally, but long-running generation is normalized to a job handle.

## Candidate adapters

Planned implementations, each behind the same protocol:

- ComfyUIProvider
- Wan2GPProvider
- StableDiffusionCppProvider
- FalProvider
- GoogleMediaProvider
- OpenAIMediaProvider
- MuAPIProvider
- RemoteGPUProvider

No candidate is routing eligible until the lifecycle/promotion authority admits it.

## Discovery

Discovery may compensate for unstable upstream runtimes. The Wan2GP pattern is explicitly supported: probe the server, read exposed API/function names, resolve exact names first, then known aliases, then safe family matching. Discovery results are observations only; they never grant routing authority.

## Security

Remote/local runtime installers are out of scope for this interface, but any future executable/model installer must verify provenance and expected digest before execution. A URL from an upstream release is not sufficient trust evidence.

Provider adapters must never return raw credentials, secrets or unrestricted provider payloads to callers. Errors and outputs are normalized before crossing the capability boundary.

## Runtime catalog

The existing RK-07 catalog remains immutable and source-pinned. New media records enter runtime only through the existing GitHub -> pin -> validate -> project process. This feature branch intentionally does not mutate `catalog.snapshot.json` or source pins.

## Promotion

Recommended order:

1. land architecture/contracts in ai-stack;
2. land canonical capability/skill records in source registries;
3. implement provider adapters here;
4. add contract tests and offline fixtures;
5. validate real local/cloud E2E;
6. re-pin the runtime catalog in a dedicated reviewable PR;
7. only then consider routing eligibility.
