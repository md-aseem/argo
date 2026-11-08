# Argo in production

Argo is live at **https://muhammadaseem.com/argo/**. It runs on Google Kubernetes Engine (GKE), behind a Google login, with conversations in Cloud SQL (PostgreSQL) and files in a GCS bucket.

This page describes what is running and how it fits together.

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

`bump2version` raises the version in `pyproject.toml`, `uv.lock`, `k8s/kustomization.yaml` and `k8s/sandbox/kustomization.yaml`, commits "Release X.Y.Z" and tags `vX.Y.Z`. Both images receive that version tag. `scripts/deploy.sh` deploys exactly the app version in your checkout, so to roll back, check out an older tag and run it again. Plain `kubectl apply -k k8s/` won't work, because the `${VARS}` would be left unfilled.

To let someone else sign in, add them to `IAP_MEMBERS` in `.env` and run `scripts/setup.sh`.

## Agent Sandbox: phase one

The sandbox infrastructure lives separately in [k8s/sandbox/](../k8s/sandbox/).
It is not included in `scripts/deploy.sh`. The app's code execution tools are
phase three and are not wired up yet; phase two covers sandbox backends and local testing.

The existing `agent-sandbox-controller` in `agent-sandbox-system` was verified
healthy on November 8, 2026, running **v1.0.10**, with all four CRDs installed.
The cluster already had `secure-sandbox-policy` and `secure-sandbox-binding`;
this deployment leaves them untouched. Neither the sandbox manifests nor the
deployment script installs or updates an admission policy or binding. See
[Google's installation guide](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/how-install-agent-sandbox)
for controller installation and verification.

**Deployment status (November 8, 2026):** the sandbox image was built and pushed,
and all phase-one manifests were applied. The warm sandbox is still Pending:
Compute Engine audit logs confirm `SSD_TOTAL_GB` quota exhaustion in `us-east4`
(200 GB used of a 250 GB limit), preventing Autopilot from creating the gVisor
node's disk. Increase the regional SSD quota or free existing quota before
expecting the warm pool to become ready. The desired pool size remains one;
Autopilot will retry provisioning. This is node disk quota, separate from the
sandbox's 2 GiB ephemeral-storage limit.

Validation passed for rendered manifests, server-side admission of both the
Sandbox and its Pod, the app's allowed/denied RBAC operations, and a local image
smoke test with a read-only root, UID 1000 and no network. The smoke test covered
library imports, chart and spreadsheet generation, shell execution, command
timeout, download and file listing. Cluster runtime/connectivity checks remain
pending until the node can be provisioned.

| Resource | Configuration |
| --- | --- |
| Namespace | `argo-sandbox`, Pod Security `restricted` |
| Template / warm pool | Both named `argo-python`; one warm sandbox |
| Runtime image | `us-east4-docker.pkg.dev/aseems-cloud/argo/sandbox:0.1.1` |
| Runtime | Python 3.14, upstream v1.0.10 HTTP API on port 8888, source SHA-256 verified at build time |
| Isolation | gVisor, UID/GID 1000, read-only root, no service account token, all capabilities dropped |
| Resources per sandbox | Requests = limits: 500m CPU, 1 GiB memory, 2 GiB ephemeral storage |
| Writable storage | `/workspace` (1 GiB emptyDir, includes `output/`) and `/tmp` (512 MiB emptyDir); deleted with the pod |
| Network | Only `app: argo` pods in namespace `argo` can initiate connections to TCP 8888. No egress, including DNS, internet, metadata and private services |
| App permissions | `argo/argo` can create/get/list/watch/patch/delete claims and get/list/watch sandboxes in `argo-sandbox` only |

Network rules are explicitly set in the template because the controller's
default allows public internet egress. A namespace-wide deny-all NetworkPolicy
also covers pods without the template's labels. The Python client's in-cluster
connection mode can connect directly to the sandbox, so no Sandbox Router is
deployed. Sandbox pods use the namespace's unannotated default service account,
with token automount disabled; they do not use the app's Google identity.

Builds use [sandbox/Dockerfile](../sandbox/Dockerfile) with its own `sandbox/`
context. The release workflow builds both images; `scripts/build-push.sh` is
the manual alternative. To build only the sandbox from a local checkout:

```sh
gcloud auth configure-docker us-east4-docker.pkg.dev --quiet
docker buildx build --platform linux/amd64 \
  --tag us-east4-docker.pkg.dev/aseems-cloud/argo/sandbox:0.1.1 --push sandbox/
```

Review and apply the manifests separately after the image is available:

```sh
scripts/deploy-sandbox.sh --print
scripts/deploy-sandbox.sh
kubectl get sandboxtemplate,sandboxwarmpool,sandbox,pod,networkpolicy -n argo-sandbox
```

The script selects the `argo` cluster in the `.env` project/region and waits for
one ready warm sandbox. Autopilot may take several minutes to provision a gVisor
node. The first sandbox image can be built manually before the next tagged
release; later releases use the normal workflow.

To update the dependency lock (hashes are enforced by the image build):

```sh
uv pip compile sandbox/requirements.in --python-version 3.14 \
  --python-platform x86_64-unknown-linux-gnu --only-binary :all: \
  --generate-hashes --output-file sandbox/requirements.txt
```

Updating the upstream runtime also requires updating its pinned URL and SHA-256
in the Dockerfile. Template changes affect newly created sandboxes; the default
warm-pool update strategy is `OnReplenish`, so existing warm or claimed pods keep
their old image until replaced. Deleting a sandbox also deletes its workspace.
Scaling the warm pool to zero saves idle capacity while retaining the pool for
future claims:

```sh
kubectl scale sandboxwarmpool/argo-python -n argo-sandbox --replicas=0
```

Reapplying the manifests restores one warm sandbox. Each active claim adds its
own sandbox beyond the pool's ready capacity.

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
- **Pods are interchangeable.** Conversations are in Cloud SQL, so updates roll out without downtime and `replicas` in `k8s/deployment.yaml` can go above 1.
- **Local development is separate.** Without `DATABASE_URL` in `.env`, a local run uses a SQLite file (`src/.adk/argo.db`), so local and production chats don't mix. To point a local run at Cloud SQL, run the [Cloud SQL Auth Proxy](https://cloud.google.com/sql/docs/postgres/sql-proxy) on your machine with `--auto-iam-authn`, add yourself as an IAM database user, and set `DATABASE_URL=postgresql+asyncpg://<your email, @ as %40>@127.0.0.1:5432/argo`.
- **Cost** (check current pricing): the pod's requests are roughly $20–30/month on Autopilot, the load balancer and static IP roughly $18–20/month, and the `db-f1-micro` Cloud SQL instance roughly $10/month. Autopilot also charges ~$73/month per cluster, which Google's free tier waives for one cluster per billing account.
- **bump2version is no longer maintained** (its successor is `bump-my-version`). It works fine here; switching later only means renaming the config.
