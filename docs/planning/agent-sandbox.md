# Plan: code execution with GKE Agent Sandbox

> Later, optionally: Agent Substrate (Phase 5).

## Goal

Give Argo ADK's standard environment tools for Bash execution and file operations so it can run Python, process uploaded files, and make charts or files, **without that code running in the `argo` pod**.

Each conversation gets its own sandbox (gVisor in production) that cannot reach:

- Cloud SQL
- the bucket credentials
- the app's service account

## Overview

| Phase | What | Effort |
| --- | --- | --- |
| [0](#phase-0-install-agent-sandbox) | Install Agent Sandbox on the cluster | ½ day |
| [1](#phase-1-sandbox-image-and-kubernetes-resources) | Sandbox image and Kubernetes resources | 1 day |
| [2](#phase-2-sandbox-service-and-local-kubernetes) | Sandbox service and local Kubernetes | 2–3 days |
| [3](#phase-3-the-adk-tools) | ADK EnvironmentToolset backed by Agent Sandbox | 3–4 days |
| [4](#phase-4-hardening-and-ui) | Hardening and UI | 1 day |
| [5](#phase-5-optional-pod-snapshots-then-agent-substrate) | *Optional:* Pod Snapshots, then Agent Substrate | — |

## Why Agent Sandbox

- **Fits the current setup.** Argo already runs on GKE Autopilot (`argo` cluster, `us-east4`), where Agent Sandbox is GA, installs with one `kubectl apply`, and gVisor is already enabled.
- **Safe by default.** The recommended admission policy (applied in Phase 0) enforces `runtimeClassName: gvisor`, so a misconfigured manifest is rejected rather than silently unsafe.
- **Fast.** Warm pools hide Autopilot's node-startup delay (minutes otherwise).
- **One client in both environments.** The official Python SDK's async client, SandboxClaims, the controller and the image built from `sandbox/Dockerfile` are the same locally (a kind cluster) and in production (GKE). Production connects to sandbox pods directly from inside the cluster; local development reaches them through a Sandbox Router and one port-forward.

**Revised direction (November 8, 2026):** phases two and three use a local kind
cluster instead of the earlier Docker-backend proposal. Production keeps phase
one's direct in-cluster connection, so no Sandbox Router or GKE Gateway is
deployed on GKE; the router runs only in the local cluster. Phase one describes
the infrastructure already built.

**Implementation record (November 8, 2026):** phases two and three are now
implemented, in one module, `src/argo/tools/sandbox.py`, with manifests in
`k8s/sandbox/base/` plus `overlays/{gke,local}/`. Local setup requires kind
0.31.0+ and uses two nodes: a control plane and a worker, because NetworkPolicy
permits access to a pod's own node and a single-node cluster exposed its API
server to sandboxes. See [production.md](../production.md#agent-sandbox-architecture-and-implementation-status)
for the architecture, limits, verification record and remaining rollout checks.
The existing GKE warm pool is now Ready; the historical SSD quota issue no
longer blocks that observed deployment. New-code GKE chat verification still
requires release and rollout. Phase four remains separate work.

**Simplified (November 8, 2026).** A first implementation followed phases two
and three below closely and came to about 825 lines of Python. It was rewritten
in about 300, relying on what ADK, the SDK and the runtime already do.
Where the sections below disagree, this list wins:

- **One environment, no per-conversation toolsets.** A single
  `SandboxEnvironment` goes straight into the stock `EnvironmentToolset` and
  `SkillToolset`. The agent's `before_tool_callback` puts the call's
  `ToolContext` in a `ContextVar`. ADK awaits that callback in the tool's own
  coroutine and runs parallel calls as separate tasks. No toolset cache, no
  eviction, no delegating tools.
- **No per-conversation lock.** ADK's own `LocalEnvironment` has none either.
- **One deadline patch per tool call**, when the claim is acquired. It's a plain
  merge patch with no UID or resourceVersion precondition and no retries. Its
  response carries the claim UID used for reset detection.
- **The runtime owns timeouts, and output isn't capped.** v1.0.10 already runs
  `sh -c`, enforces the timeout, kills the process group and reports
  `timed_out`. Commands go through it as `bash -c`, with nothing added to the
  image. Its whole output reaches the app, which risks the app pod's memory on
  output in the hundreds of MB; that was accepted to keep the image stock.
- **Explicit `save_output(path)`** replaces automatic publication of
  `/workspace/output/`, with its in-sandbox inventory and session-state hashes.
- **No settings class or `ARGO_ENV`.** Credentials are in-cluster when
  `KUBERNETES_SERVICE_HOST` is set, and the named `kind-argo-dev` context (whose
  server must be loopback) otherwise. Release is the SDK's `delete_sandbox`. No
  smoke module.
- **No gVisor locally.** The kind cluster uses the default runtime and no
  admission policy; gVisor and its scheduling live in the GKE overlay only.

**Why not Agent Substrate (yet):** it's open source, early-stage, and not an officially supported product. It maps many idle "actors" onto fewer workers with fast suspend/resume, which only pays off with many concurrent, mostly idle sessions. Argo is a personal agent with one replica, so Substrate stays optional in Phase 5.

---

## Phase 0: install Agent Sandbox

*Effort: ½ day*

> **Note:** there is no console toggle or `gcloud` flag. Agent Sandbox is installed with `kubectl` onto the existing cluster. Autopilot enables gVisor automatically, and the install guide states no minimum GKE version.
>
> `kubectl apply` only affects the **current context**. Confirm it with `kubectl config current-context` before installing.

**1. Set environment variables**

Pin the release (the phase-one deployment uses v1.0.10; check [releases](https://github.com/kubernetes-sigs/agent-sandbox/releases) before changing it). Autopilot clusters are regional, so `LOCATION` is the region.

```bash
export CLUSTER_NAME=argo
export LOCATION=us-east4
export AGENT_SANDBOX_VERSION=v1.0.10
```

**2. Point `kubectl` at the cluster**

```bash
gcloud container clusters get-credentials ${CLUSTER_NAME} --location=${LOCATION}
kubectl config current-context   # expect gke_<project>_us-east4_argo
```

**3. Install the controller and CRDs**

```bash
kubectl apply -f https://github.com/kubernetes-sigs/agent-sandbox/releases/download/${AGENT_SANDBOX_VERSION}/sandbox-with-extensions.yaml
```

**4. Wait for the controller**

This can take minutes on Autopilot while a node is created. Rerun it if it times out.

```bash
kubectl rollout status deployment/agent-sandbox-controller -n agent-sandbox-system --timeout=120s
```

**5. Verify**

```bash
kubectl get crd \
  sandboxes.agents.x-k8s.io \
  sandboxtemplates.extensions.agents.x-k8s.io \
  sandboxclaims.extensions.agents.x-k8s.io \
  sandboxwarmpools.extensions.agents.x-k8s.io

kubectl get deployment agent-sandbox-controller -n agent-sandbox-system   # READY 1/1
```

**6. Apply the recommended security policy**

This is not applied by default. Apply these two files from the repo at the same version:

- `examples/policy/vap/secure-sandbox-policy.yaml`
- `examples/policy/vap/secure-sandbox-binding.yaml`

**7. Document it.** Record the steps in `docs/production.md`.

To uninstall: `kubectl delete -f <same manifest URL>`.

---

## Phase 1: sandbox image and Kubernetes resources

*Effort: 1 day*

### Kubernetes resources

New files under `k8s/sandbox/`, with their own `kustomization.yaml`. (They can't go in `k8s/kustomization.yaml`: its `namespace: argo` would move them all into the app's namespace.) Not yet wired into `scripts/deploy.sh`.

| File | Contents |
| --- | --- |
| `namespace.yaml` | `argo-sandbox` namespace, separate from `argo`, Pod Security level `restricted` |
| `template.yaml` | `SandboxTemplate`: runtime image, `runtimeClassName: gvisor`, non-root, read-only root filesystem, no service-account token, CPU/memory requests = limits (Autopilot bills on these), ephemeral storage limit. Satisfies every rule of the Phase 0 admission policy. Also holds the sandbox's network rules (see below) |
| `warmpool.yaml` | `SandboxWarmPool` of size 1 (raise if too slow) |
| `networkpolicy.yaml` | Namespace-wide deny-all backstop |
| `rbac.yaml` | Role for KSA `argo`: create/get/list/watch/patch/delete `SandboxClaim`, get/list/watch `Sandbox`, in `argo-sandbox` only |

**Phase-one implementation notes (v1.0.10):**

- **API version is `v1beta1`** (`extensions.agents.x-k8s.io/v1beta1`).
- **A claim names a warm pool, not a template** (`SandboxClaim.spec.warmPoolRef`; the client's `create_sandbox(warmpool=...)`). So the warm pool is required, even at size 0.
- **Direct ingress, no router in production.** The `argo` app's pods connect directly to sandbox pods on port 8888 (the SDK's in-cluster mode). Phase two keeps this for GKE; only the local overlay admits router pods instead.
- **Network rules live in the template** (`spec.networkPolicy`), because the controller generates the NetworkPolicy from it and its default would allow internet egress. Egress is empty (deny all); `networkpolicy.yaml` is the backstop underneath.

### Runtime image

`sandbox/Dockerfile`, built by the existing GitHub workflow as a second image (`argo/sandbox`, same version tag):

- Python plus common libraries: pandas, numpy, matplotlib, pillow, openpyxl (pinned with hashes in `sandbox/requirements.txt`)
- The runtime server the Python client expects: upstream's `examples/python-runtime-sandbox/main.py` at the same release, fetched at build time with a checksum

---

## Phase 2: sandbox service and local Kubernetes

*Effort: 2–3 days*

Build a standalone sandbox service around the official `k8s-agent-sandbox`
Python SDK, and a local kind cluster to develop and test it against. Both
environments use the same async client, claim lifecycle, controller and sandbox
image; only the connection configuration and a manifest overlay differ. ADK
tools, session binding and artifact publication remain phase-three work.

Pin the SDK to `k8s-agent-sandbox[async]==1.0.10`, matching the controller and
runtime source. When the documentation describes newer features, check the
pinned source rather than `main`.

### Architecture

Each environment has two connections: lifecycle through the Kubernetes API, and
execution and file traffic over HTTP to the sandbox runtime on port 8888.

```text
Production (GKE Autopilot)
  argo pod ── Kubernetes API (in-cluster service account) ──▶ SandboxClaim ──▶ warm-pool pod (gVisor)
  argo pod ── pod IP :8888 (SandboxInClusterConnectionConfig) ──▶ sandbox pod

Local (kind on Docker Desktop)
  Argo on the Mac ── Kubernetes API (context kind-argo-dev) ──▶ SandboxClaim ──▶ warm-pool pod
  Argo on the Mac ── 127.0.0.1:18080 (SandboxDirectConnectionConfig)
                  ──▶ kubectl port-forward (dev script) ──▶ Sandbox Router ──▶ pod IP :8888
```

- **Production connects directly.** Argo runs in the same cluster, so it uses
  the SDK's in-cluster mode, which upstream recommends for clients inside the
  cluster. The SDK reads the pod IP from the Sandbox status and connects on port
  8888. This is the path phase one built and admission-tested: the template's
  NetworkPolicy admits only `app: argo` pods. There is no router, Gateway, load
  balancer or proxy-only subnet to run or pay for.
- **Local goes through one router.** Argo runs on the Mac, outside the
  cluster's pod network. A dev script keeps one `kubectl port-forward` open to
  the Sandbox Router, and Argo points `SandboxDirectConnectionConfig` at it. The
  router forwards each request to the pod IP the SDK sends in
  `X-Sandbox-Pod-IP`. Going through the router also keeps gVisor possible
  locally: a port-forward straight to a gVisor pod can't reach its network
  stack.
- **Not SDK Tunnel or Gateway mode.** Tunnel mode starts a `kubectl` process
  for every sandbox handle and isn't supported by `AsyncSandboxClient`. Gateway
  mode is for clients outside the cluster.

The controller allocates a sandbox from the warm pool, so a claim's name and its
Sandbox's name differ; the SDK resolves one from the other.

### Environment selection

`ARGO_ENV=local` is the default; the production ConfigMap sets
`ARGO_ENV=production`. Reject other values. There is no off option and no
fallback to running code in Argo or on the host.

| `ARGO_ENV` | Kubernetes credentials | Runtime connection |
| --- | --- | --- |
| `local` | kubeconfig context `kind-argo-dev`, named explicitly | `SandboxDirectConnectionConfig(api_url="http://127.0.0.1:18080")` |
| `production` | In-cluster service account | `SandboxInClusterConnectionConfig()` |

The local context name and router URL are settings, with these defaults in
`.env.example`. Never fall back to the kubeconfig's current context: on the
development Mac it is the production cluster, and the SDK uses it when no API
client is passed. Build the local API client from the named context, and refuse
to start unless its server address is local.

### The sandbox service (`src/argo/sandbox/`)

A small internal package, separate from `src/argo/tools/`. One service wraps
`AsyncSandboxClient` and the SDK's `commands` and `files` APIs; one factory
builds the client from the table above. The service exposes execution, file
and lifecycle primitives. It does not define model-facing tool schemas or
results.

**Acquire or reconnect in one call.** Name each claim from a hash of the
caller-supplied conversation key (for example `argo-` plus the first 20 hex
digits of its SHA-256), so names reveal no user data. Then:

```python
sandbox = await client.create_sandbox(
    warmpool="argo-python",
    namespace="argo-sandbox",
    claim_name=claim_name,
    adopt_existing=True,
    labels={"app.kubernetes.io/managed-by": "argo"},
    shutdown_after_seconds=idle_seconds,
)
```

If the claim already exists, the SDK gets a 409 and attaches to it after
checking its warm pool and that it isn't being deleted. So concurrent first
calls for one conversation produce one claim, and reconnecting after an Argo
restart needs no stored reference. Return the claim's UID with the handle: a
different UID on a later call means the workspace was reset. Never pass `env`:
the template disallows it, and it would force a cold start.

**Idle expiry by the controller.** `shutdown_after_seconds` sets the claim's
`spec.lifecycle.shutdownTime` with `shutdownPolicy: Delete` (the default
policy, `Retain`, would keep expired claims). The controller then deletes the
claim and its sandbox once idle. Reattaching doesn't extend that time, so the
service patches `shutdownTime` to now plus the idle period before every
operation. The idle period is far longer than the 60-second command limit, so a
running operation can't expire. Argo needs no cleanup worker and no discovery
after a restart. The local tests confirm that the controller honours a patched
`shutdownTime`.

**Release.** Delete with a UID precondition (`expected_uid`), so a newer claim
with the same name is never removed by mistake. The `argo-sandbox` namespace
holds only Argo's claims; list them with the `managed-by` label.

**Bounded output.** The runtime buffers a command's entire stdout and stderr in
memory, and the SDK limits downloads but not command results. Cap output inside
the sandbox: add a small wrapper to the image that runs a command under its own
timeout, a few seconds below the runtime's 60, writes output to `/tmp`, and
returns at most a fixed number of bytes of each stream plus a truncated flag.
The service sends every command through it. Limit downloads with
`SandboxFileConfig(max_download_size=...)`.

**Errors.** Bound readiness waits, and give actionable errors for a missing or
non-local context, a closed local port-forward, RBAC denial, an unready warm
pool and runtime connectivity. The SDK doesn't retry `POST /execute`; neither
does the service, because a command whose outcome is unknown may already have
run.

**Shutdown.** Close the client on app shutdown. The SDK's context-manager exit
and `atexit` cleanup never delete explicitly named claims, so conversations keep
their sandboxes until they expire or are released.

**Smoke command.** `python -m argo.sandbox.smoke` acquires a sandbox, runs a
command, writes and reads a file, and releases it. Run it locally, and in
production with `kubectl exec deploy/argo -n argo`.

### Image and manifests

Keep `sandbox/Dockerfile`, its pinned upstream Python runtime, dependencies,
`/workspace`, `/workspace/output/` and `/tmp`, and add the output wrapper. Build
for arm64 locally and amd64 for GKE. Stay on this runtime for now (see
[open questions](#open-questions)).

Split `k8s/sandbox/` into a shared base and two overlays:

| Directory | Contents |
| --- | --- |
| `base/` | Namespace (Pod Security `restricted`); template (non-root, read-only root, no service-account token, `runtimeClassName: gvisor`, requests = limits, bounded `/workspace` and `/tmp`, no sandbox egress); warm pool; deny-all backstop; app RBAC |
| `overlays/gke/` | Registry image and version; gVisor and amd64 node selectors and the gVisor toleration; ingress from `app: argo` pods in `argo` (unchanged from phase one) |
| `overlays/local/` | Locally built image `argo-sandbox:dev`; ingress from router pods only; no runtime class unless the gVisor experiment below succeeds; router Deployment, Service and NetworkPolicy in `argo-sandbox-router` |

Keep production resource limits locally, so out-of-memory behaviour matches.
`scripts/deploy-sandbox.sh` renders `overlays/gke`, and `.bumpversion.cfg`
follows the image tag to the GKE overlay's `kustomization.yaml`.

**The local router.** Use the upstream Go router,
`registry.k8s.io/agent-sandbox/sandbox-router-go:v1.0.10` (pin the tag; the
`:latest` tag in upstream's example doesn't exist). Run one replica with
`--cache-enabled=false` and the default `--authz-mode=allow-all`:

- The SDK sends each request's pod IP, so the router needs no Pod cache. With
  neither the cache nor TokenReview, it makes no Kubernetes API calls, so it
  gets no RBAC and no service-account token.
- Only a port-forward reaches it, and that already needs admin credentials for
  the kind cluster, so it needs no authentication of its own.
- Its NetworkPolicy allows egress only to sandbox pods on TCP 8888, plus DNS.
  Port-forwarded traffic arrives inside the pod, so no ingress rule is needed;
  the local tests confirm the port-forward works with the policy in place.
- The default `--proxy-timeout` of 180 seconds leaves room for the runtime's
  60-second timeout result.

### Local cluster

`scripts/local-sandbox.sh` targets only the kind cluster `argo-dev` (context
`kind-argo-dev`) and passes that context to every `kubectl` call:

| Command | What it does |
| --- | --- |
| `up` | Create the two-node kind cluster (implementation requires kind 0.31.0+); install the v1.0.10 controller, extensions, gVisor and admission policy; build the sandbox image and `kind load` it; apply `overlays/local`; wait for a ready warm sandbox |
| `tunnel` | Run `kubectl port-forward svc/sandbox-router-svc 18080:8080 -n argo-sandbox-router`, restarting it if it exits |
| `down` | Delete the `argo-dev` kind cluster, and nothing else |

The cluster needs Docker, kind and `kubectl`, but no Google credentials or GKE
quota. It should fit in Docker Desktop's 7.7 GiB VM: the control plane, worker,
controller, router and two or three 1 GiB sandboxes. Starting the full Argo app
still needs its Vertex AI and GCS configuration.

**gVisor experiment (first step).** Upstream's
[`install-gvisor-kind`](https://github.com/kubernetes-sigs/agent-sandbox/blob/v1.0.10/dev/ci/shared/install-gvisor-kind)
script installs gVisor on kind for upstream's CI. On the Mac it needs two
changes: gVisor's download path uses `aarch64` where the Mac reports `arm64`,
and macOS has `shasum -a 512` instead of `sha512sum`. If its smoke test passes,
the local overlay keeps `runtimeClassName: gvisor` and the local cluster gets
the same admission policy and binding as production. If it doesn't, local
sandboxes use standard containers without that policy, and gVisor is verified
only on GKE. Record the result here.

### Tests

| Level | What it verifies |
| --- | --- |
| Unit (SDK mocked) | Environment and context selection, including refusing a non-local server; claim naming; create/adopt arguments; `shutdownTime` patched before each operation; UID-preconditioned delete; output-cap parsing; error mapping |
| Local (kind) | Claim from the warm pool; reattach from a new client after a restart (same UID); concurrent first calls create one claim; Python and shell execution, timeout, output cap, uploads and downloads, charts and spreadsheets; expiry with a short idle period, and extension by a patched `shutdownTime`; release; the sandbox can't reach the internet, DNS, the Kubernetes API or another sandbox; app RBAC via `kubectl auth can-i --as=system:serviceaccount:argo:argo` |
| GKE (when quota allows) | The smoke command from the `argo` pod against a gVisor warm-pool sandbox; the same isolation checks plus the metadata server and Cloud SQL; gVisor confirmed inside the sandbox; idle expiry |

### Acceptance criteria

- The same service and `AsyncSandboxClient` work in both environments; only the
  connection table above and the overlays differ.
- Local tests pass against kind with no cloud services or Google credentials.
  With `ARGO_ENV=local`, the service refuses a context whose server isn't local.
- One conversation gets one claim, reused across calls and Argo restarts;
  concurrent first calls create one claim; a changed UID is reported as a reset.
- The controller deletes idle claims, and activity extends them. Release deletes
  only the matching UID. App shutdown deletes no claims.
- Command output is capped inside the sandbox, and downloads are bounded.
- Sandboxes can't initiate connections to the internet, DNS, the Kubernetes API
  or other sandboxes; on GKE, also not to the metadata server or Cloud SQL.
- Local and GKE results are recorded separately. The GKE SSD quota blocker stays
  visible until resolved; local success doesn't mark gVisor or production
  checks as passed.
- No ADK tools are added or registered in this phase.

---

## Phase 3: the ADK tools

*Effort: 3–4 days*

Use ADK's stable `EnvironmentToolset` with an Argo `BaseEnvironment` adapter
backed by the phase-two sandbox service. Expose the standard `Execute`,
`ReadFile`, `EditFile` and `WriteFile` tools, and share the conversation's
environment with `SkillToolset` so skill scripts use the same workspace.

```text
EnvironmentToolset + SkillToolset
                 │
conversation-bound Argo BaseEnvironment adapter
                 │
shared phase-two sandbox service
                 │
Agent Sandbox async commands.run() / files APIs
```

Both local and production chat use this integration. ADK owns the standard
model-facing tools; Argo owns session binding, the provider adapter and artifact
integration. Agent Sandbox continues to provide execution, isolation and claim
lifecycle. Phase two is implemented in `src/argo/sandbox/` and supplies this
adapter's infrastructure operations.

### Decision and dependencies

The November 8, 2026 review checked installed `google-adk` **2.13.0** and
`k8s-agent-sandbox` **1.0.10**, then the ADK **2.14.0** release and source.
`BaseEnvironment` and `EnvironmentToolset` became stable in **2.14.0**. Upgrade
the project's ADK minimum to `>=2.14.0` and regenerate `uv.lock` during
implementation; use public package imports. The implementation now declares and
locks `k8s-agent-sandbox[async]==1.0.10` and ADK 2.14.0 in `uv.lock`.

| Option | Fit for Argo |
| --- | --- |
| **EnvironmentToolset + Sandbox-backed BaseEnvironment** | **Selected.** Standard execution/file tools, a reusable provider interface and direct integration with ADK skills. Requires an Argo adapter and explicit session/lifecycle handling. |
| One ADK function calling Sandbox `commands.run()` | Smallest implementation for strictly Bash-only access and easiest custom results. A valid simpler alternative, but leaves more tool behaviour and future skill integration to Argo. |
| ADK `ExecuteBashTool` | Installed 2.13.0 implementation runs local processes and requires per-call confirmation. Its constructor offers no remote execution backend; the workspace only sets the working directory. |
| ADK `GkeCodeExecutor(executor_type="sandbox")` | Python-oriented with per-execution claims. Installed 2.13.0 uses old SDK constructor arguments and methods incompatible with installed Sandbox SDK 1.0.10; still unfixed in 2.14.0 ([discussion #6535](https://github.com/google/adk-python/discussions/6535)). |

The environment interface is the preferred foundation for future multi-step
file processing, editing, tests and skill execution. Stable promotion and
multiple upstream providers are evidence of continued ADK investment, rather
than a guarantee of future compatibility. Keep the provider adapter small,
reuse the standard tools and verify behaviour when upgrading ADK. Streaming
commands, interactive terminals, cancellation and background-job management
remain outside this phase.

Sources: [ADK 2.14.0 release](https://github.com/google/adk-python/releases/tag/v2.14.0),
[pinned EnvironmentToolset](https://github.com/google/adk-python/blob/v2.14.0/src/google/adk/tools/environment/_environment_toolset.py),
[pinned BaseEnvironment](https://github.com/google/adk-python/blob/v2.14.0/src/google/adk/environment/_base_environment.py),
[pinned SkillToolset](https://github.com/google/adk-python/blob/v2.14.0/src/google/adk/tools/skill_toolset.py),
and [Agent Sandbox Python client](https://agent-sandbox.sigs.k8s.io/docs/python-client/).

### Environment adapter (`src/argo/tools/sandbox.py`)

Implement an Argo subclass of `google.adk.environment.BaseEnvironment` using
the shared sandbox service. Keep ADK/session/artifact code out of the phase-two
package. Its methods are async and await the service directly.

| Member | Implementation |
| --- | --- |
| `working_dir` | Return `Path("/workspace")`, identifying the remote workspace without accessing the host filesystem. |
| `execute(command, timeout=...)` | Call the service's bounded-command wrapper through `await sandbox.commands.run(..., command_timeout=min(timeout, cap))`, so the runtime enforces the timeout ADK reports. Map the SDK's `timed_out` onto ADK's `ExecutionResult`. |
| `read_file(path)` | Use SDK file reads via the service; return bytes, enforce a download limit and map missing files to `FileNotFoundError`. |
| `write_file(path, content)` | Use SDK file writes via the service; accept text or bytes and create parent directories within the workspace. |
| `read_file_stream(path)` | Initially use ADK's default implementation over the bounded `read_file`. This buffers the whole allowed file; genuine streaming can be added later. |
| `initialize()` / `close()` | Idempotent setup/release of adapter resources. `initialize()` acquires nothing but sets `is_initialized = True`; `SkillToolset` calls it on every model request while that flag is false. Claim acquisition remains lazy; closing an adapter does not delete the conversation's claim or close the shared client. |

- Invoke `/bin/bash --noprofile --norc -c` explicitly inside the sandbox, and
  verify `/bin/bash` exists in the built image. Quote the complete command as
  one argument when constructing the SDK command, so a skill script's own
  `cd <skill dir> && ...` prefix stays intact. Commands already start in
  `/workspace`: the image sets `SANDBOX_BASE_DIR=/workspace`, which the runtime
  uses as every command's working directory. (The current Python runtime does
  not support SDK per-command `cwd` or `env`.)
- Map file paths explicitly. The SDK and the runtime strip a leading `/` and
  resolve the rest under `/workspace`, so an unmapped `/workspace/a.py` is
  silently written to `/workspace/workspace/a.py`, and `/tmp/x` to
  `/workspace/tmp/x`. ADK's environment instruction and `SkillToolset` both use
  absolute `/workspace/...` paths, so this is the normal case, not an edge case:
  - strip the `/workspace/` prefix from absolute paths under it;
  - pass relative paths unchanged;
  - reject any other absolute path with a clear error.

  The runtime already rejects paths whose real path leaves `/workspace`. Never
  use host `Path.resolve` or host file I/O for remote files. Enforce byte limits
  on reads and writes.
- Keep the standard four tools and their usage instructions. Do not add separate
  `bash`, `run_python`, `run_shell` or custom text-file tool schemas. Python can
  run through `Execute`; ordinary text edits use the standard file tools. The
  only Argo-defined sandbox tool is `stage_artifact` (see below), because no
  stock tool moves an artifact into the workspace.
- Each execution starts a fresh process. Workspace files persist; shell variables,
  `cd` and Python variables do not persist between calls.

### Conversation binding and lifecycle

`BaseEnvironment` methods receive no `ToolContext`, and stock
`EnvironmentToolset` holds one environment object. Argo's module-level agent
must not hold a single sandbox environment shared across conversations.

- Add a small Argo toolset/context wrapper that selects a conversation-bound
  environment and delegates to the standard ADK toolsets. Bind from trusted
  app, authenticated user and session context; never accept these identifiers
  from model arguments or switch a global environment's active conversation.
- Give the environment and skill toolsets the same bound environment for the
  conversation. Handle both toolset initialization and close idempotently;
  neither toolset's close may invalidate the other's active operations.
- Keep `initialize()` lightweight: stock toolsets can call it during tool
  discovery or request preparation. Acquire/reconnect on the first actual
  environment operation, including skill materialization, through the phase-two
  service. Only that service handles Kubernetes objects and connection modes.
- Derive the claim name from the trusted conversation key through the service.
  Later calls and app restarts reach the same claim; keep the claim UID in ADK
  session state. Do not put live environment/client objects in session state.
- Report a changed UID as a workspace reset; the model can stage attachments
  again with `stage_artifact`. Do not claim unsaved files survived. Skill files
  need no handling: before every script run, `SkillToolset` checks that the
  script exists in the environment and copies the skill's files in again if it
  doesn't. That check relies on the adapter's `read_file` raising
  `FileNotFoundError` for a missing file.
- Serialize tool calls per conversation with one lock, held by the tool-call
  wrapper (see below) around the whole call and its output collection.
  `EditFile` reads and writes through two separate adapter calls, so a lock in
  the adapter could not make an edit atomic. Wrap `run_skill_script` and
  `stage_artifact` too. Never also lock in the adapter: `asyncio.Lock` is not
  re-entrant. Different conversations remain independent. Never automatically
  replay an execution whose outcome is unknown.
- Release the matching claim when its conversation is deleted and drop its bound
  adapter resources. Idle expiry remains controller-owned. App shutdown closes
  adapters and the shared client without deleting conversation claims.
- Bound the per-conversation cache of toolsets and adapters (LRU or idle
  eviction). Eviction drops only local objects: it never releases the claim or
  closes the shared client, and the next call re-adopts the claim by name. Do
  not evict an entry while a call holds its lock, or a new entry could run a
  second call alongside it.

### Attachments, output, and limits

- Add one small Argo function tool, `stage_artifact(filename)`, that copies an
  artifact from the current user's artifact service into `/workspace/inputs/`
  through SDK file APIs, preserving binary data, and returns its workspace path.
  Scope lookups to the trusted user and session, as the existing artifact tools
  do. Pass no GCS credentials or app environment values into the sandbox.
  Standard text-file tools do not replace binary artifact transfers.
- Wrap each stock tool in a small delegating tool that keeps ADK's declaration
  unchanged and holds the conversation lock. After calls that can change files
  (everything except `ReadFile`), it publishes new/changed regular files under
  `/workspace/output/` through the existing ADK artifact service, including
  files created by skill scripts. Enforce path/symlink, byte/count limits and
  avoid republishing unchanged files.
- Enrich tool results with workspace-reset notices, published artifact references
  and service truncation metadata. ADK's `ExecutionResult` contains only stdout,
  stderr, exit code and timeout status; preserve metadata in call-local state
  through the wrapper, without modifying ADK internals or a shared last-result
  field. If that state is a `contextvar`, store one mutable object per call and
  update it in place: `SkillToolset` writes skill files in concurrent tasks,
  which copy the context, so values set inside them would be lost. Keep the
  execution result when artifact publication fails.
- ADK 2.14.0's standard `Execute` passes a fixed **30-second** timeout. Accept
  that initially. Construct `SkillToolset` with `script_timeout` at or below the
  phase-two command cap (initially 60 seconds; ADK's default is 300), so the
  timeout ADK reports is the one the sandbox enforces. The service still clamps
  any longer request to its cap as a backstop. A later configurable Execute
  timeout needs a deliberate tool extension, not a private constant patch.
- Set `max_output_chars` on the toolset for model-visible truncation. This is
  additional to phase-two stream byte caps inside the sandbox; trimming a tool
  response does not bound runtime buffering. Keep all file transfers bounded.
- `SkillToolset(save_output_artifacts=True)` is unsupported with `environment`
  in ADK 2.14.0. Use Argo's publication wrapper for both toolsets.
- Distinguish nonzero exit, timeout, provisioning/auth/connectivity failure and
  artifact-transfer failure. Preserve saved artifact versions independently of
  disposable workspace files. Never execute on the host when a sandbox fails.

### Wiring and acceptance criteria

Register the conversation-aware environment and skill toolsets and
`stage_artifact` in `agent.py` alongside existing artifact tools. Update
`instruction.md` for the standard tool names, workspace, fresh-process
behaviour, `stage_artifact` and the output directory. Share one phase-two
service through the FastAPI lifespan. On shutdown, close the `Runner` first,
which closes the toolsets and their adapters, then the shared service. Make
adapter cleanup compatible with both toolsets' close calls.

Before phase three is complete:

- Test through ADK with mocked infrastructure: standard schemas, trusted context
  binding, reuse, concurrent user/session isolation, claim-reset recovery,
  idempotent initialization/close, atomic `EditFile` sequencing, path mapping
  (`/workspace/a.py` maps to `a.py`; other absolute paths are rejected), byte
  limits, nonzero exits, 30-second Execute timeout, service caps, truncation,
  binary transfers, `stage_artifact` scoping, cache eviction without claim
  release, artifact versions and publication failures.
- Verify that listing tools does not provision a claim, every actual operation
  uses the sandbox, and environment/skill toolsets share only the intended
  conversation workspace. Verify a skill script runs again after a workspace
  reset. Do not close a shared client from one adapter.
- Run local chat against kind: Bash pipelines, redirection, a Python heredoc,
  standard read/write/edit operations, a file written with `WriteFile` at
  `/workspace/a.py` and then run with `Execute`, a staged attachment, and an ADK
  skill script using the same workspace, including downloadable output files.
  Verify file persistence, reconnect after app restart and cleanup after
  conversation deletion.
- Record the equivalent GKE chat test separately when the production sandbox is
  ready. Local success does not establish gVisor or production connectivity.

---

## Phase 4: hardening and UI

*Effort: 1 day*

- [ ] **Tests** in `tests/` with the client mocked: claim reuse, timeout, output truncation, file round trip.
- [ ] **Escape checks** from inside a sandbox. Each of these must fail:
  - metadata server (`169.254.169.254`)
  - Cloud SQL proxy
  - internet
  - Kubernetes API
- [ ] **UI:** the chat UI already shows full tool input/output; check that long code and output render well.
- [ ] **Cost:** watch warm pool size × requests; consider scaling the pool to 0 overnight.

---

## Phase 5 (optional): Pod Snapshots, then Agent Substrate

1. **Pod Snapshots** first: suspend an idle conversation's sandbox and resume it with files and Python state intact, instead of deleting it.
2. **Agent Substrate** only if Argo opens to more users or long-lived per-conversation state becomes important. Trial it in a non-production namespace: one actor per conversation on a small worker pool, with its routing replacing the direct in-cluster connection. It's early-stage and allowlisted for production, so keep it behind a setting.

---

## Open questions

1. **Future internet access:** phases two and three deny sandbox egress. Revisit an allowlist (e.g. PyPI) through an egress proxy only as a separate change.
2. **State lifetime:** keep sandbox state for minutes, or until the conversation is deleted (which needs snapshots)?
3. **Warm pool cost vs. cold start:**
   - Claiming from a warm pool takes under a second.
   - With no warm sandbox, Autopilot may need to create a node and pull the image, which can take minutes.
   - One warm sandbox at about 0.5 vCPU / 1 GiB costs very roughly $20/month (check current Autopilot pricing).
   - Options: a smaller pool, scaling to 0 overnight, or Pod Snapshots later.
4. **Version compatibility:** verify the pinned SDK, router and controller together before implementation; do not assume examples on `main` match v1.0.10.
5. **Limits:** choose the idle period, output cap and download limit in phase two, and maximum artifact bytes/count in phase three.
6. **Runtime:** the SDK now calls the Python runtime "legacy"; sandboxd is the newer runtime (streaming output, environment and working directory per command, file deletion, server-side limits). The SDK's `commands` and `files` calls are the same for both, so switching is mostly an image and connection change. Its connections differ, though: in the pinned Python SDK, sandboxd isn't reachable through the router, and a port-forward straight to a gVisor pod doesn't work. Revisit after phase three.

## References

- [Python client documentation](https://agent-sandbox.sigs.k8s.io/docs/python-client/)
- [Pinned Python client README](https://github.com/kubernetes-sigs/agent-sandbox/blob/v1.0.10/clients/python/agentic-sandbox-client/README.md): connection modes, deterministic claims, targeting a context
- [Pinned Go Sandbox Router](https://github.com/kubernetes-sigs/agent-sandbox/blob/v1.0.10/sandbox-router/README.md)
- [Pinned gVisor-on-kind script](https://github.com/kubernetes-sigs/agent-sandbox/blob/v1.0.10/dev/ci/shared/install-gvisor-kind)
- [kind v0.24.0: built-in NetworkPolicy support](https://github.com/kubernetes-sigs/kind/releases/tag/v0.24.0)
- [Install Agent Sandbox on GKE](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/how-install-agent-sandbox)
- [Isolate AI code execution with Agent Sandbox](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/agent-sandbox)
- [Announcement: Agent Sandbox GA + Agent Substrate](https://cloud.google.com/blog/products/containers-kubernetes/bringing-you-agent-sandbox-on-gke-and-agent-substrate)
- [agent-sandbox releases](https://github.com/kubernetes-sigs/agent-sandbox/releases)
- [Agent Substrate repo](https://github.com/agent-substrate/substrate)
