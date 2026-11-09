# Argo in production

Argo is live at **https://muhammadaseem.com/argo/**. It runs on Google Kubernetes Engine (GKE), behind a Google login, with conversations in Cloud SQL (PostgreSQL) and files in a GCS bucket.

This page records deployed infrastructure, implemented changes awaiting rollout, and how the architecture fits together.

## Architecture

```
browser ──HTTPS──▶ Google load balancer (static IP argo-ip, managed certificate)
                        │  Identity-Aware Proxy: Google login, only IAP_MEMBERS get in
                        │  /argo/*
                        ▼
                   Ingress ▶ Service ▶ Pod ┬ argo (the app image) ──▶ Vertex AI (Gemini), GCS bucket
                                           └ cloud-sql-proxy ──▶ Cloud SQL (Postgres): conversations
```

A request goes like this:

1. The browser hits `muhammadaseem.com/argo/`. DNS points the domain at the static IP `argo-ip`, which belongs to a Google load balancer. HTTP is redirected to HTTPS, using a free Google-managed certificate that renews itself.
2. Identity-Aware Proxy (IAP) on the load balancer shows the Google login and only lets through accounts listed in `IAP_MEMBERS`.
3. The load balancer sends `/argo/*` to the `argo` Service, which forwards to the app's pod on port 8000. The load balancer's timeout is raised to one hour so long streamed chat replies aren't cut off.
4. The app (the same Docker image you can run locally) serves the chat UI and the API. It calls Gemini through Vertex AI, stores files in the bucket, and reads and writes conversations through the Cloud SQL proxy beside it.

No passwords or key files exist anywhere. The pod runs as the Google service account `argo-app` (via Workload Identity), and that one identity is used for Vertex AI, the bucket, and the database login. GitHub Actions pushes images with a short-lived identity.

## What's running

| Thing | Name / value |
| --- | --- |
| Google Cloud project | `aseems-cloud` |
| Region (cluster, database, image registry) | `us-east4` |
| Vertex AI location | `global` |
| GKE cluster | `argo` (Autopilot), namespace `argo` |
| Image registry | `us-east4-docker.pkg.dev/aseems-cloud/argo/argo`, one tag per release (`0.1.1`, ...) |
| Static IP | `argo-ip` (global), the domain's DNS A record points at it |
| Cloud SQL | instance `argo-db` (Postgres, `db-f1-micro`), database `argo` |
| Bucket | `gs://aseems-argo-artifacts` |
| App identity | Google service account `argo-app`, linked to the Kubernetes service account `argo` |
| Google login | IAP, with an OAuth client created in the console (stored in the cluster as the Secret `iap-oauth`) |
| Image builds | `.github/workflows/build-image.yml` in `md-aseem/argo`, app and sandbox images on every `vX.Y.Z` tag |

## Words you'll see

| Term | Meaning |
| --- | --- |
| **Cluster** | The Kubernetes environment. It's **Autopilot**: Google manages the servers, and billing is for the CPU/memory the pods request. |
| **Pod** | One running copy of the app. Ours has two containers: the app and the Cloud SQL proxy. |
| **Sidecar** | A helper container that runs beside the app in the same pod. The Cloud SQL proxy is one: the app talks to `127.0.0.1:5432` and the proxy securely forwards to the database, logging in as `argo-app`. |
| **Deployment** | "Keep this many pods of this image running", and replace them on updates. |
| **Service** | A stable internal address in front of the pods. |
| **Ingress** | Builds the Google load balancer that sends `<domain>/argo/*` to the Service. |
| **ConfigMap** | The app's settings (the in-cluster version of the app section of `.env`). |
| **Workload Identity** | Lets the pod act as a Google service account (`argo-app`) with no key files. |
| **IAP** | Identity-Aware Proxy: the Google login screen in front of the app. |
| **Artifact Registry** | Google's private store for Docker images. |

## What's in the repo

| Path | What it is |
| --- | --- |
| [k8s/](../k8s/) | The Kubernetes manifests: namespace, service account, ConfigMap, Deployment (app + proxy sidecar), Service, Ingress, BackendConfig (timeout, IAP), FrontendConfig (HTTPS redirect), ManagedCertificate. `${VARS}` in them are filled from `.env`. |
| [k8s/kustomization.yaml](../k8s/kustomization.yaml) | Ties the manifests together and pins the image version that's deployed. |
| [Dockerfile](../Dockerfile) | Builds the UI and packages it with the API into one image. |
| [.github/workflows/build-image.yml](../.github/workflows/build-image.yml) | Builds and pushes the app and sandbox images when a version tag is pushed. It doesn't deploy. |
| [scripts/deploy.sh](../scripts/deploy.sh) | Fills in `k8s/` from `.env`, applies it, and waits for the rollout. `--print` shows the filled-in manifests without applying. |
| [scripts/setup.sh](../scripts/setup.sh) | Creates the Google Cloud side (APIs, registry, IP, bucket, cluster, database, service accounts, permissions, GitHub access, IAP members). It skips anything that already exists, so it's safe to re-run to repair permissions or add sign-in members. |
| [scripts/build-push.sh](../scripts/build-push.sh) | Builds and pushes both images from your laptop, if GitHub Actions isn't available. |
| [scripts/deploy-sandbox.sh](../scripts/deploy-sandbox.sh) | Applies the sandbox manifests separately and waits for a ready warm sandbox. `--print` shows the rendered manifests. |
| `.env` | The real values (git-ignored). The scripts read it; [.env.example](../.env.example) explains each one. |

## Shipping a change

```sh
uvx bump2version patch        # or minor / major
git push --follow-tags        # GitHub Actions builds and pushes the image
scripts/deploy.sh             # once the Actions run is green
```

`bump2version` raises the version in `pyproject.toml`, `uv.lock`, `k8s/kustomization.yaml` and `k8s/sandbox/overlays/gke/kustomization.yaml`, commits "Release X.Y.Z" and tags `vX.Y.Z`. Both images receive that version tag. `scripts/deploy.sh` deploys exactly the app version in your checkout, so to roll back, check out an older tag and run it again. Plain `kubectl apply -k k8s/` won't work, because the `${VARS}` would be left unfilled.

To let someone else sign in, add them to `IAP_MEMBERS` in `.env` and run `scripts/setup.sh`.

## Agent Sandbox: architecture and implementation status

Phases two and three are implemented in this checkout: the sandbox module,
local Kubernetes setup, ADK's standard environment and skill tools bound to the
calling conversation, attachment staging and saving outputs. The new app and
sandbox images still need a release and production rollout.

| Phase / environment | Status as of November 8, 2026 |
| --- | --- |
| Phase 0: controller, extensions and admission policy | Deployed on GKE, controller v1.0.10 |
| Phase 1: image, template, warm pool, network rules and RBAC | Deployed; GKE pool observed Ready 1/1 and runtime kernel confirmed as `4.19.0-gvisor` |
| Phases 2 and 3: sandbox module, local cluster and ADK tools | Implemented, then [simplified](planning/agent-sandbox.md#why-agent-sandbox); all 10 unit and kind integration tests pass on arm64 Docker Desktop with Kubernetes v1.35.0 |
| New-code GKE chat tests | Pending an app release and rollout; the deployed sandbox image already works with it |
| Phase 4: additional hardening and UI | Remains separate work in the [plan](planning/agent-sandbox.md#phase-4-hardening-and-ui) |

Earlier on November 8, Autopilot could not create the sandbox node because of
regional `SSD_TOTAL_GB` quota exhaustion (200 GB used of 250 GB). A later read
confirmed that the node and warm sandbox are now running. That quota issue is no
longer blocking the observed deployment; the exact quota/provisioning change was
not made by this implementation. This does not establish that the new service or
chat integration has been deployed.

A read-only health request from the existing `argo` app pod to the sandbox's
pod IP on port 8888 also succeeded. This verifies the current direct network
path; the new SDK-backed service and chat flow still need their rollout tests.


### Connections and ownership

```text
Authenticated chat -> ADK Runner -> root_agent
  before_tool_callback (bind_sandbox): the call's ToolContext -> trusted app + user + session
  |-- ADK EnvironmentToolset: Execute / ReadFile / EditFile / WriteFile
  |-- ADK SkillToolset: skill scripts
  |-- stage_artifact: artifact service -> /workspace/inputs
  |-- save_output: sandbox file -> artifact service -> GCS -> chat downloads
  v
one shared SandboxEnvironment -> AsyncSandboxClient
  | lifecycle: Kubernetes API             | execution/files: HTTP :8888
  v                                       v
deterministic SandboxClaim -> allocated sandbox pod

GKE:   argo pod --------------------- pod IP :8888 ----------------> sandbox (gVisor)
Local: Argo on Mac -> 127.0.0.1:18080 -> kubectl port-forward -> router -> sandbox
```

Inside a pod (`KUBERNETES_SERVICE_HOST` is set), the app uses in-cluster
credentials and `SandboxInClusterConnectionConfig`. GKE has no router, Gateway
or external sandbox endpoint. Anywhere else, it loads only the `kind-argo-dev`
context by name, never the current context, and refuses it unless its API
server is loopback. The SDK then reaches sandboxes through the router at
`http://127.0.0.1:18080`. A missing local context stops app startup; a closed
tunnel produces a tool error. There is no host execution fallback.

The local cluster has a control-plane node and a separate worker. This is
required for API isolation: NetworkPolicy permits traffic to a pod's own node,
so a single-node cluster allowed sandbox traffic to its API server in testing.
The control-plane taint keeps sandbox workloads on the worker. Local sandboxes
use the default container runtime. gVisor, its node selector and toleration, and
the admission policy that requires them apply on GKE only.

### Source layout

| Path | Responsibility |
| --- | --- |
| [src/argo/tools/sandbox.py](../src/argo/tools/sandbox.py) | Credentials, the shared `SandboxEnvironment`, claim naming and deadlines, the tool callbacks, `stage_artifact` and `save_output` |
| [src/argo/agent.py](../src/argo/agent.py) | Gives the environment to both stock toolsets and registers the callbacks |
| [src/argo/main.py](../src/argo/main.py) | FastAPI lifespan opens and closes the sandbox client |
| [src/argo/routes.py](../src/argo/routes.py) | Deleting a conversation also deletes its sandbox |
| [k8s/sandbox/base/](../k8s/sandbox/base/) | Shared isolation, resource/storage limits, warm pool, template and client RBAC |
| [k8s/sandbox/overlays/gke/](../k8s/sandbox/overlays/gke/) | Registry version, gVisor and amd64 scheduling and app-only ingress |
| [k8s/sandbox/overlays/local/](../k8s/sandbox/overlays/local/) | Dev image, router-only ingress, router resources and two-node kind configuration |
| [scripts/local-sandbox.sh](../scripts/local-sandbox.sh) | `up`, loopback-only restarting `tunnel`, and `down`; every cluster operation names `kind-argo-dev` |

The app locks `k8s-agent-sandbox[async]==1.0.10` alongside controller/runtime
v1.0.10, and `uv.lock` selects ADK 2.14.0 (project minimum `>=2.14.0`). ADK owns
the standard tool declarations and instructions; Argo registers its toolsets
unchanged. Upgrade these interfaces together and rerun both unit and kind
integration suites.

### Identity, persistence and lifecycle

A claim is `argo-` plus the first 20 SHA-256 hex digits of a JSON-encoded trusted
`[app_name, user_id, session_id]` from the tool call's session. No model
argument selects the claim or user. A tool call's first sandbox operation runs
`create_sandbox(adopt_existing=True)`, which creates the claim from the
`argo-python` warm pool or reattaches to it by name. Parallel first calls
converge on one claim. No app environment variables are injected into it.

Adoption doesn't extend a claim's deadline, so each tool call also patches
`spec.lifecycle.shutdownTime` to now plus 30 minutes, with
`shutdownPolicy: Delete`. The controller deletes a claim and its workspace
after 30 minutes without a tool call. The patch returns the claim's UID, kept in
session state as `argo_sandbox_uid`. A different UID means the workspace was
replaced, and the tool result gets a `workspace_reset` notice. Attachments can
then be staged again; skill scripts copy their files back in when missing.
Deleting a conversation deletes its claim.

Tool calls in one conversation are not serialized, as with ADK's own local
environment. Claims live in Kubernetes, so app replicas are interchangeable.

### Runtime, storage and limits

| Setting | Value / behavior |
| --- | --- |
| Namespace / template / warm pool | `argo-sandbox` / `argo-python` / `argo-python`; one warm sandbox |
| Runtime | Python 3.14, upstream v1.0.10 HTTP runtime on port 8888, source SHA-256 checked at build time |
| Isolation | gVisor on GKE (default runtime locally), UID/GID 1000, read-only root, token automount off, all capabilities dropped, restricted Pod Security |
| Resources per sandbox | Requests = limits: 500m CPU, 1 GiB memory, 2 GiB ephemeral storage; same locally and on GKE |
| Writable storage | `/workspace`: 1 GiB emptyDir; `/tmp`: 512 MiB emptyDir; both deleted with the sandbox |
| Network | GKE ingress only from `argo/app: argo`; local ingress only from router pods; TCP 8888. Sandbox egress denied, including DNS |
| Router | Local only, `sandbox-router-go:v1.0.10`, cache off, allow-all authz behind loopback port-forward; no API token or RBAC; egress only sandbox TCP 8888 and cluster DNS |
| Readiness | 120-second SDK readiness bound |
| Command time | Standard Execute: 30 seconds; skill scripts: 60 seconds, the runtime's own limit. On timeout the runtime kills the command's whole process group and returns its output so far |
| Process behavior | `bash -c`, through the runtime's `/bin/sh -c`, in `/workspace`; files persist, shell/Python variables and `cd` do not; a background job that keeps output open holds the call until the timeout |
| Captured output | Not capped: the runtime returns all of stdout and stderr, and the app holds them in memory. ADK's tools show the model at most 16,000 characters and say when they truncate. Output in the hundreds of MB could exhaust the app pod's 1 GiB (larger output kills the sandbox runtime first, a tool error). Output that isn't UTF-8 makes the command fail with a decoding error |
| File transfers | 15 MiB per download, enforced by the SDK during transfer |

File tools accept relative paths or `/workspace/...`, strip that prefix and
reject other absolute paths and `..`. They never resolve paths on the host.
`stage_artifact` loads only the current ADK user's/session's artifact and
preserves binary bytes under `/workspace/inputs/`. The sandbox receives no GCS
credentials or app service-account identity.

`save_output(path)` reads one sandbox file and saves it as an artifact named
after the file; saving the same name again adds a version, and the existing SSE
artifact events and downloads apply. Saved GCS artifacts are independent of the
disposable workspace. A failing tool call, including an infrastructure failure,
returns `{"status": "error"}` to the model instead of ending the turn. A command
whose outcome is unknown is never replayed automatically.

### Local operation and verification

Install Docker Desktop, kind (at least 0.31.0) and kubectl.
No cloud credentials are needed for these sandbox tests. The full chat app still
needs its normal Vertex AI and GCS settings.

```sh
scripts/local-sandbox.sh up
scripts/local-sandbox.sh tunnel        # keep in a separate terminal
.venv/bin/python -m unittest discover -s tests -v
ARGO_SANDBOX_INTEGRATION=1 .venv/bin/python -m unittest tests/test_local_sandbox.py -v
```

The unit tests run a real ADK Runner with scripted model responses against a
fake cluster. They check that parallel calls in one model response reach their
own conversation's sandbox, plus staging, skill scripts, saving outputs, reset
notices and release. The kind tests cover real Bash and Python, file
persistence, timeouts with partial output, the download limit, parallel first
calls, reattaching after a client restart, controller expiry and deadline extension, and isolation
from the internet, TCP and UDP DNS, the API server, metadata and other
sandboxes. One of them drives the real HTTP/SSE chat API: staging, the four file
tools, a skill that makes a chart and a spreadsheet, saving both, an app
restart, and deleting the conversation. Sessions and artifacts are in memory, so
no model or GCS requests are made.

All 10 tests passed on November 8, 2026. The kind tests ran on the existing
`argo-dev` cluster after it was updated in place to the new template and image
(admission policy removed, warm sandbox recreated). `local-sandbox.sh up` was
not rerun from scratch after its gVisor steps were removed.

`down` deletes only the development cluster and its disposable workspaces. Image
and template changes affect new sandboxes. For a clean local runtime rebuild,
run `down` then `up`; existing workspaces otherwise retain their runtime.

### First production rollout of phases two and three

The sandbox image is unchanged from 0.1.1, and the rendered GKE overlay
differs from the deployed manifests only by `imagePullPolicy: IfNotPresent` and,
after a version bump, the image tag. The new app works with the warm sandbox
already running, so no sandbox needs replacing. To keep the cluster in step with
the repo, apply the overlay once the bumped images are built:

```sh
scripts/deploy-sandbox.sh --print
scripts/deploy-sandbox.sh
```

The warm pool uses `OnReplenish`, so this affects only sandboxes created
afterwards. Never delete claimed sandboxes for an upgrade; that deletes their
workspaces.

Then run `scripts/deploy.sh`. After rollout, verify chat execution, staged
input, a chart or spreadsheet saved with `save_output`, reconnect and
conversation deletion through IAP. Record those results here; local success and
the existing GKE kernel check do not establish new-code production
connectivity. Recheck production egress denial to the API, metadata, Cloud SQL,
DNS, internet and another sandbox. These rollout-specific checks are pending.
Infrastructure is still deployed separately from the app, and the
controller/admission policies are not changed by `scripts/deploy-sandbox.sh`.

To refresh image dependencies (hashes are enforced at build time):

```sh
uv pip compile sandbox/requirements.in --python-version 3.14 \
  --python-platform x86_64-unknown-linux-gnu --only-binary :all: \
  --generate-hashes --output-file sandbox/requirements.txt
```

Updating the upstream runtime requires changing its pinned URL and checksum in
`sandbox/Dockerfile`. To reduce idle cost, scale the warm pool to zero (the pool
must still exist for claims); reapplying the overlay restores one warm sandbox:

```sh
kubectl scale sandboxwarmpool/argo-python -n argo-sandbox --replicas=0
```

For manual runtime health checks or a shell, use `scripts/sandbox.sh --check`
or `scripts/sandbox.sh --context NAME`. The script prints and pins its context;
`--pod NAME` chooses a sandbox when several exist. Changes in that shell affect
only that sandbox's disposable workspace.

## When something is wrong

| Symptom | Likely cause |
| --- | --- |
| 502 from the load balancer | Health check failing. Check `kubectl get pods -n argo`, then `kubectl port-forward deploy/argo 8000:8000 -n argo` and open `localhost:8000/argo/api/health`. |
| Pod `CrashLoopBackOff` | `kubectl logs deploy/argo -n argo -c argo` for the app, `-c cloud-sql-proxy` for the database connection. |
| App log: `password authentication failed` / `role ... does not exist` | The database user is missing. Re-run `scripts/setup.sh`. |
| Proxy log: `403` / `not authorized` | `argo-app` lacks `cloudsql.client` / `cloudsql.instanceUser`, or Workload Identity isn't linked. Re-run `scripts/setup.sh`. |
| App log: `permission denied for schema public` | The database user lacks `cloudsqlsuperuser`. Re-run `scripts/setup.sh`. |
| Pod `Pending` for a while | Autopilot is creating a node; wait a couple of minutes. |
| `ImagePullBackOff` | That version's image doesn't exist yet. Check the Actions run finished. |
| Chat replies fail with 403 | `argo-app` lacks Vertex AI or bucket access. Re-run `scripts/setup.sh`. |
| `You don't have access` after logging in | That account isn't in `IAP_MEMBERS`. Add it and re-run `scripts/setup.sh`. |
| IAP "OAuth client not found" / redirect mismatch | The OAuth client (APIs & Services → Credentials) is missing the redirect URI `https://iap.googleapis.com/v1/oauth/clientIds/<CLIENT_ID>:handleRedirect`. |

## Things to know

- **Each person you let in has their own conversations.** The app verifies the signed JWT IAP adds to every request (`x-goog-iap-jwt-assertion`) and uses the signed-in Google email as the user id, so people in `IAP_MEMBERS` only see their own chats and files. Requests without a valid token get `401`. Everyone's chats still run on your Vertex AI bill. Optionally set `IAP_AUDIENCE` (`/projects/<PROJECT_NUMBER>/global/backendServices/<SERVICE_ID>`) in the ConfigMap so tokens IAP issued for other apps are rejected too. Locally there is no IAP, so every request is `LOCAL_USER_ID` (default `aseem`).
- **Conversations survive pod replacement.** They live in Cloud SQL and sandbox claims are deterministic, so a replacement app reconnects to them. Keep one serving replica while sandbox calls use process-local conversation locks; add cross-replica serialization before scaling concurrent writers.
- **Local development is separate.** Without `DATABASE_URL` in `.env`, a local run uses a SQLite file (`src/.adk/argo.db`), so local and production chats don't mix. To point a local run at Cloud SQL, run the [Cloud SQL Auth Proxy](https://cloud.google.com/sql/docs/postgres/sql-proxy) on your machine with `--auto-iam-authn`, add yourself as an IAM database user, and set `DATABASE_URL=postgresql+asyncpg://<your email, @ as %40>@127.0.0.1:5432/argo`.
- **Cost** (check current pricing): the pod's requests are roughly $20–30/month on Autopilot, the load balancer and static IP roughly $18–20/month, and the `db-f1-micro` Cloud SQL instance roughly $10/month. Autopilot also charges ~$73/month per cluster, which Google's free tier waives for one cluster per billing account.
- **bump2version is no longer maintained** (its successor is `bump-my-version`). It works fine here; switching later only means renaming the config.
