# Plan: code execution with GKE Agent Sandbox

> Later, optionally: Agent Substrate (Phase 5).

## Goal

Give Argo a `run_code` / shell tool so it can run Python, process uploaded files, and make charts or files, **without that code running in the `argo` pod**.

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
| [3](#phase-3-the-adk-tools) | The ADK tools (defined separately) | 1–2 days |
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
| `up` | Create the kind cluster (kind 0.24 or later, whose default network plugin enforces NetworkPolicy); install the v1.0.10 controller and extensions; optionally install gVisor; build the sandbox image and `kind load` it; apply `overlays/local`; wait for a ready warm sandbox |
| `tunnel` | Run `kubectl port-forward svc/sandbox-router-svc 18080:8080 -n argo-sandbox-router`, restarting it if it exits |
| `down` | Delete the `argo-dev` kind cluster, and nothing else |

The cluster needs Docker, kind and `kubectl`, but no Google credentials or GKE
quota. It should fit in Docker Desktop's 7.7 GiB VM: the control plane,
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

*Effort: 1–2 days*

Add thin ADK tools on top of the phase-two sandbox service. Both local chat and
production chat use the same tools, session lifecycle, and file handling. Tools
receive the shared service; they do not run `kubectl`, manage Kubernetes objects,
or choose a connection mode.

### Tools (`src/argo/tools/sandbox.py`)

| Tool | Behaviour / result |
| --- | --- |
| `run_python(code)` | Execute a Python script in the conversation workspace; return stdout, stderr, exit code, timeout status, and new/updated output files |
| `run_shell(command)` | Execute through the runtime's shell API; return stdout, stderr, exit code, timeout status, and new/updated output files |
| `write_file` / `read_file` | Write/read workspace-relative files through the SDK file API, with explicit encoding and size limits |

`run_python` can upload a script and execute it with Python through the same SDK
command API. Do not introduce another Python server or promise a persistent
notebook kernel: workspace files persist, but each execution starts a process.

### Conversation lifecycle and ownership

- Derive the internal conversation key from the authenticated user, app, and
  session. These values come from trusted ADK context, never model arguments.
- Acquire lazily on the first sandbox tool call through the service's
  acquire-or-reconnect call. The claim name comes from the conversation key, so
  later calls and app restarts reach the same claim; keep only the claim UID in
  ADK session state.
- If the UID changed (the claim expired or was deleted), tell the model the
  workspace was reset. Restore attachments/artifacts on demand; do not claim
  unsaved workspace files survived.
- Serialize workspace-changing calls per conversation while allowing different
  conversations to run independently. Do not replay a command automatically
  after an execution request with an uncertain outcome.
- Release a claim when its conversation is deleted. Idle expiry belongs to the
  controller (phase two); app shutdown only closes the client.

### Attachments, output, and limits

- Copy requested attachments from the current user's artifact service into the
  workspace through the SDK, preserving binary data and safe relative paths.
  Uploaded sandbox files receive no GCS credentials or app environment values.
- After Python and shell calls, collect new/changed regular files under
  `/workspace/output/`, download through the SDK, and save through the existing
  ADK artifact service. The current artifact UI should display those files.
  Enforce path/symlink and byte/count limits; avoid republishing unchanged files.
- Keep workspace persistence and artifact persistence distinct: deleting a claim
  deletes workspace files, while saved GCS artifacts retain existing versioning.
  Report artifact-copy failures without automatically rerunning executed code.
- Use the phase-two command timeout (initially 60 seconds) and output cap; the
  local router's 180-second proxy timeout leaves room for the timeout result.
  Bound file transfers, and shorten output further for the model if needed.
- Give distinct, actionable results for a command exiting nonzero, a command
  timing out, provisioning/auth/connectivity failure, and artifact-transfer
  failure. Never execute elsewhere when the sandbox is unavailable.

### Wiring and acceptance criteria

Register the tools in `agent.py` and explain their use in `instruction.md`.
Create/close the shared service through the app lifecycle (FastAPI lifespan);
keep ADK/session/artifact code out of the phase-two package. The service is
async (`AsyncSandboxClient`), so tools await it directly on the event loop.

Before phase three is complete, test tools through ADK with mocked infrastructure
for schema/context isolation, reuse, deleted-claim recovery, timeout/output
limits, binary attachments, artifact versions, and user/session isolation. Then
run local chat end to end against the kind cluster, including Python and shell
producing downloadable files. Verify reconnect after app restart and cleanup
after conversation deletion. Record the equivalent GKE chat test separately when
the production sandbox is ready.

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
