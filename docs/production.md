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
| Image builds | `.github/workflows/build-image.yml` in `md-aseem/argo`, on every `vX.Y.Z` tag |

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
| [.github/workflows/build-image.yml](../.github/workflows/build-image.yml) | Builds and pushes the image when a version tag is pushed. It doesn't deploy. |
| [scripts/deploy.sh](../scripts/deploy.sh) | Fills in `k8s/` from `.env`, applies it, and waits for the rollout. `--print` shows the filled-in manifests without applying. |
| [scripts/setup.sh](../scripts/setup.sh) | Creates the Google Cloud side (APIs, registry, IP, bucket, cluster, database, service accounts, permissions, GitHub access, IAP members). It skips anything that already exists, so it's safe to re-run to repair permissions or add sign-in members. |
| [scripts/build-push.sh](../scripts/build-push.sh) | Builds and pushes the image from your laptop, if GitHub Actions isn't available. |
| `.env` | The real values (git-ignored). The scripts read it; [.env.example](../.env.example) explains each one. |

## Shipping a change

```sh
uvx bump2version patch        # or minor / major
git push --follow-tags        # GitHub Actions builds and pushes the image
scripts/deploy.sh             # once the Actions run is green
```

`bump2version` raises the version in `pyproject.toml`, `uv.lock` and `k8s/kustomization.yaml`, commits "Release X.Y.Z" and tags `vX.Y.Z`. `scripts/deploy.sh` deploys exactly the version in your checkout, so to roll back, check out an older tag and run it again. Plain `kubectl apply -k k8s/` won't work, because the `${VARS}` would be left unfilled.

To let someone else sign in, add them to `IAP_MEMBERS` in `.env` and run `scripts/setup.sh`.

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

- **Everyone you let in shares one set of conversations.** The app uses a fixed user id (`aseem`), so anyone in `IAP_MEMBERS` sees, continues and deletes the same chats and files, and their chats run on your Vertex AI bill. Per-user isolation needs a small code change (use the email IAP puts in the `X-Goog-Authenticated-User-Email` header as the user id).
- **Pods are interchangeable.** Conversations are in Cloud SQL, so updates roll out without downtime and `replicas` in `k8s/deployment.yaml` can go above 1.
- **Local development is separate.** Without `DATABASE_URL` in `.env`, a local run uses a SQLite file (`src/.adk/argo.db`), so local and production chats don't mix. To point a local run at Cloud SQL, run the [Cloud SQL Auth Proxy](https://cloud.google.com/sql/docs/postgres/sql-proxy) on your machine with `--auto-iam-authn`, add yourself as an IAM database user, and set `DATABASE_URL=postgresql+asyncpg://<your email, @ as %40>@127.0.0.1:5432/argo`.
- **Cost** (check current pricing): the pod's requests are roughly $20–30/month on Autopilot, the load balancer and static IP roughly $18–20/month, and the `db-f1-micro` Cloud SQL instance roughly $10/month. Autopilot also charges ~$73/month per cluster, which Google's free tier waives for one cluster per billing account.
- **bump2version is no longer maintained** (its successor is `bump-my-version`). It works fine here; switching later only means renaming the config.
