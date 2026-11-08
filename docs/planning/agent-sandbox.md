# Plan: code execution with GKE Agent Sandbox

> Later, optionally: Agent Substrate (Phase 5).

## Goal

Give Argo a `run_code` / shell tool so it can run Python, process uploaded files, and make charts or files, **without that code running in the `argo` pod**.

Each conversation gets its own isolated sandbox (gVisor) that cannot reach:

- Cloud SQL
- the bucket credentials
- the app's service account

## Overview

| Phase | What | Effort |
| --- | --- | --- |
| [0](#phase-0-install-agent-sandbox) | Install Agent Sandbox on the cluster | ½ day |
| [1](#phase-1-sandbox-image-and-kubernetes-resources) | Sandbox image and Kubernetes resources | 1 day |
| [2](#phase-2-sandbox-backends-and-local-testing) | Sandbox backends and local testing | 1–2 days |
| [3](#phase-3-the-adk-tools) | The ADK tools (defined separately) | 1–2 days |
| [4](#phase-4-hardening-and-ui) | Hardening and UI | 1 day |
| [5](#phase-5-optional-pod-snapshots-then-agent-substrate) | *Optional:* Pod Snapshots, then Agent Substrate | — |

## Why Agent Sandbox

- **Fits the current setup.** Argo already runs on GKE Autopilot (`argo` cluster, `us-east4`), where Agent Sandbox is GA, installs with one `kubectl apply`, and gVisor is already enabled.
- **Safe by default.** The recommended admission policy (applied in Phase 0) enforces `runtimeClassName: gvisor`, so a misconfigured manifest is rejected rather than silently unsafe.
- **Fast.** Warm pools hide Autopilot's node-startup delay (minutes otherwise).
- **Simple to call.** The GKE backend wraps the official Python client and uses its in-cluster connection mode, so application code doesn't manage Kubernetes resources by hand or require a Sandbox Router.

**Why not Agent Substrate (yet):** it's open source, early-stage, and not an officially supported product. It maps many idle "actors" onto fewer workers with fast suspend/resume, which only pays off with many concurrent, mostly idle sessions. Argo is a personal agent with one replica, so Substrate stays optional in Phase 5.

---

## Phase 0: install Agent Sandbox

*Effort: ½ day*

> **Note:** there is no console toggle or `gcloud` flag. Agent Sandbox is installed with `kubectl` onto the existing cluster. Autopilot enables gVisor automatically, and the install guide states no minimum GKE version.
>
> `kubectl apply` only affects the **current context**. Confirm it with `kubectl config current-context` before installing.

**1. Set environment variables**

Pin the release (v1.0.6 at time of writing; check [releases](https://github.com/kubernetes-sigs/agent-sandbox/releases)). Autopilot clusters are regional, so `LOCATION` is the region.

```bash
export CLUSTER_NAME=argo
export LOCATION=us-east4
export AGENT_SANDBOX_VERSION=v1.0.6
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

**Differences from the first draft, found in the v1.0.6 CRDs and client:**

- **API version is `v1beta1`** (`extensions.agents.x-k8s.io/v1beta1`).
- **A claim names a warm pool, not a template** (`SandboxClaim.spec.warmPoolRef`; the client's `create_sandbox(warmpool=...)`). So the warm pool is required, even at size 0.
- **No Sandbox Router needed.** The client's in-cluster mode connects straight to the sandbox pod, so ingress is allowed from the `argo` app's pods on port 8888 instead of from a router. This skips deploying the router (and a Gateway).
- **Network rules live in the template** (`spec.networkPolicy`), because the controller generates the NetworkPolicy from it and its default would allow internet egress. Egress is empty (deny all); `networkpolicy.yaml` is the backstop underneath.

### Runtime image

`sandbox/Dockerfile`, built by the existing GitHub workflow as a second image (`argo/sandbox`, same version tag):

- Python plus common libraries: pandas, numpy, matplotlib, pillow, openpyxl (pinned with hashes in `sandbox/requirements.txt`)
- The runtime server the Python client expects: upstream's `examples/python-runtime-sandbox/main.py` at the same release, fetched at build time with a checksum

---

## Phase 2: sandbox backends and local testing

*Effort: 1–2 days*

Make the execution environment usable and testable independently of the ADK tools.
Build the Docker backend first so development can continue without GKE capacity
or quota approval. This phase defines infrastructure and internal integration;
tool names, arguments, model-facing results, and artifact behaviour belong to
Phase 3. The environment configuration and supporting files below are planned,
not implemented yet.

### Environment-based backend selection

Choose the backend automatically from the application's environment. Use one
app-level setting, `ARGO_ENV`, with `local` as the development default and
`production` explicitly configured in the production deployment. There is no
separate sandbox mode, backend override, or off setting.

| Application environment | Automatically selected backend | Use |
| --- | --- | --- |
| `local` | Separate local Docker containers | Everyday development and local integration tests; no Kubernetes cluster, kubeconfig, or Google credentials needed for sandbox execution |
| `production` | Agent Sandbox resources on GKE | Production and cluster integration tests; use the Phase 1 template, warm pool, and namespace |

Sandbox execution is available in both environments. If Docker is unavailable
locally or GKE sandbox provisioning fails in production, report an actionable
error; do not silently disable execution, switch backends, or run code in the
Argo process or on the host. Reject unrecognized application environment values.
Starting the full Argo app still requires its existing Vertex AI and
artifact-storage configuration. The standalone Docker runtime tests must not
require that app configuration.

### Shared runtime and internal interface

- Use the **same sandbox image version and upstream HTTP runtime API** in Docker and GKE. Avoid a separate local execution server or a second implementation of code execution and file operations.
- Add a small internal sandbox package, separate from `src/argo/tools/`, for backend selection, lifecycle operations, and access to that runtime API. Keep the Kubernetes SDK inside the GKE implementation; the Docker implementation must not require it to operate.
- The interface should support acquiring an environment for a caller-supplied conversation key, reconnecting to it, and releasing it. Expose the runtime's execution and file operations without defining ADK tools or their model-facing return values in this phase.
- Track the backend and its resource identifier together: a Docker container ID locally, or a SandboxClaim reference on GKE. Reuse an existing environment for the same conversation, and keep different conversations separate. Binding this reference to ADK session state is Phase 3 work.
- Provide explicit cleanup and idle-expiry support for both backends, including recovery after an app restart. Test ownership checks so cleanup only removes resources created by the configured backend for Argo. ADK/background-task wiring remains in Phase 3.

### Docker behaviour

- Create **one container per conversation**, on demand. A local warm pool is unnecessary initially; GKE continues to use SandboxClaim and SandboxWarmPool resources.
- Run the image as its non-root user, with a read-only root filesystem, dropped capabilities, no privilege escalation, CPU/memory limits, and bounded writable storage at `/workspace` and `/tmp`. Initialize `/workspace/output/` as the image does in production.
- Keep workspace data for the container's lifetime, including across calls; remove it when the conversation's container is deleted. Do not bind-mount the repository, host home directory, Docker socket, or Google credentials into sandbox containers, or pass Argo's `.env` into them.
- Allow the local app to reach the runtime while blocking sandbox internet egress. Use an isolated Docker network; any published runtime port must bind only to localhost. Standalone runtime tests can use `--network=none` and make HTTP requests from inside the container.
- The trusted local backend may use the Docker engine to manage containers. Code executing inside a sandbox never receives Docker engine access.
- Keep resource identity, port allocation, startup/readiness waits, and teardown inside the backend rather than in the future ADK tools. Failed startup and cleanup should produce actionable errors without executing code elsewhere.

### Local testing workflow

Provide documented commands or scripts to build the sandbox image, run runtime
smoke tests, and exercise backend lifecycle operations without starting Argo or
installing Kubernetes. After Phase 3, the local app should use this same backend
to exercise the tools through the chat UI.

| Test level | What it verifies | Infrastructure |
| --- | --- | --- |
| Unit | Environment-based backend selection, conversation reuse/separation, resource ownership, expiry, and cleanup/error paths | Fake runtime responses and mocked Docker/Kubernetes clients |
| Local integration | Real runtime HTTP execution, shell commands, timeouts, uploads/downloads, chart and spreadsheet generation, plus Docker create/reconnect/delete behaviour | Docker and the sandbox image; no cloud resources |
| GKE integration | Claims, warm-pool allocation, readiness, app RBAC/connectivity, and the deployed runtime API | A ready GKE sandbox; run when quota is available |

Local Docker tests verify application behaviour and container configuration.
They do **not** establish gVisor isolation or Kubernetes NetworkPolicy/RBAC
enforcement; those require GKE tests. The SDK's local tunnel mode still requires
a Kubernetes cluster and router, so it is not the cluster-free Docker workflow.

### Acceptance criteria

- A developer can build the image and pass standalone runtime and backend integration tests using Docker alone, without GKE quota or credentials.
- The local environment automatically uses Docker and production automatically uses GKE, without a separate sandbox setting or off option. Tests verify environment selection and clear errors for missing dependencies or invalid configuration, with no automatic fallback.
- Repeated requests for one conversation reuse its container; another conversation gets a separate workspace. Reconnecting and deleting work, and expiry/startup-failure tests leave no owned resources behind.
- The sandbox receives no app/cloud credentials or host mounts, the root filesystem is read-only, and the selected Docker network blocks internet egress.
- Docker and GKE target the same runtime API and image version. Backend lifecycle tests pass locally; live GKE checks are recorded separately if quota still blocks provisioning.
- No ADK tools are added or registered, and no tool behaviour is redefined as part of this phase.

---

## Phase 3: the ADK tools

*Effort: 1–2 days*

Define and implement these tools separately, after the Phase 2 backend interface
is available. The existing tool proposal below is retained unchanged for that
discussion; Phase 2 does not settle or expand its behaviour.

### Tools (`src/argo/tools/sandbox.py`)

| Tool | Returns |
| --- | --- |
| `run_python(code)` | stdout, stderr, exit code, list of new files |
| `run_shell(command)` | stdout, stderr, exit code |
| `write_file` / `read_file` | Read and write inside the sandbox workspace |

### Behaviour

- **Lifecycle:** one sandbox per conversation. Claim it on the first tool call, keep the claim name in ADK session state, and reuse it on later calls.
- **Files out:** anything written to `/workspace/output/` is copied to the GCS bucket and registered through the existing `artifacts.py`, so the chat UI shows it like any other file.
- **Files in:** attachments from `attachments.py` are copied into the sandbox on demand.
- **Limits:** per-call timeout (e.g. 60 s); output is truncated before it goes back to the model.
- **Cleanup:** delete the claim after N idle minutes (background task in `main.py`), with a TTL on the claim as a backstop.

### Wiring

- Register the tools in `agent.py`, and describe when to use them in `instruction.md`.
- **Environment:** use the backend selected automatically in Phase 2: Docker locally and GKE in production. The tools do not expose a separate backend selector or off option.

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
2. **Agent Substrate** only if Argo opens to more users or long-lived per-conversation state becomes important. Trial it in a non-production namespace: one actor per conversation on a small worker pool, with its routing replacing the Sandbox Router. It's early-stage and allowlisted for production, so keep it behind a setting.

---

## Open questions

1. **Internet access from sandboxes:** none (safest), or an allowlist (e.g. PyPI) through an egress proxy?
2. **State lifetime:** keep sandbox state for minutes, or until the conversation is deleted (which needs snapshots)?
3. **Warm pool cost vs. cold start:**
   - Claiming from a warm pool takes under a second.
   - With no warm sandbox, Autopilot may need to create a node and pull the image, which can take minutes.
   - One warm sandbox at about 0.5 vCPU / 1 GiB costs very roughly $20/month (check current Autopilot pricing).
   - Options: a smaller pool, scaling to 0 overnight, or Pod Snapshots later.
4. **Python client API:** verify the exact method names against the official docs before writing the tool; third-party write-ups disagree.

## References

- [Install Agent Sandbox on GKE](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/how-install-agent-sandbox)
- [Isolate AI code execution with Agent Sandbox](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/agent-sandbox)
- [Announcement: Agent Sandbox GA + Agent Substrate](https://cloud.google.com/blog/products/containers-kubernetes/bringing-you-agent-sandbox-on-gke-and-agent-substrate)
- [agent-sandbox releases](https://github.com/kubernetes-sigs/agent-sandbox/releases)
- [Agent Substrate repo](https://github.com/agent-substrate/substrate)
