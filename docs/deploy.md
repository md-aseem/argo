# Deploying Argo to GKE

Argo runs on Google Kubernetes Engine at `https://<your domain>/argo/`, behind a Google login, with conversations in Cloud SQL (PostgreSQL).

All the setup is driven by one file, `.env`, and two scripts:

| | |
| --- | --- |
| `.env` | Your values: project, region, bucket, domain, database, who can sign in. Copied from [.env.example](../.env.example); git-ignored. |
| `scripts/setup.sh` | One-time Google Cloud setup. Creates whatever is missing, skips what exists, so it's safe to re-run. |
| `scripts/deploy.sh` | Deploys `k8s/` with your values filled in. Run after every release. |

```
browser ──HTTPS──▶ Google load balancer (Google login via IAP)
                        │  /argo/*
                        ▼
                   Ingress ▶ Service ▶ Pod ┬ argo (this image) ──▶ Vertex AI, GCS bucket
                                           └ cloud-sql-proxy ──▶ Cloud SQL (Postgres): conversations
```

## Words you'll see

| Term | Meaning |
| --- | --- |
| **Cluster** | The Kubernetes environment. We use **Autopilot**: Google manages the servers, you pay for the CPU/memory the pods request. |
| **Pod** | One running copy of the app. Ours has two containers: the app and the Cloud SQL proxy. |
| **Sidecar** | A helper container that runs beside the app in the same pod. The Cloud SQL proxy is one: the app talks to `127.0.0.1:5432` and the proxy securely forwards to the database. |
| **Deployment** | "Keep this many pods of this image running" and replace them on updates. |
| **Service** | A stable internal address in front of the pods. |
| **Ingress** | Builds a Google load balancer that sends `<domain>/argo/*` to the Service. |
| **ConfigMap** | The app's settings (the in-cluster version of the app section of `.env`). |
| **Workload Identity** | Lets the pod act as a Google service account (`argo-app`) with no key files. That one identity is used for Vertex AI, the bucket and the database login. |
| **IAP** | Identity-Aware Proxy: the Google login screen in front of the app. |
| **Artifact Registry** | Google's private store for Docker images. |

## You'll need

- A Google Cloud project with billing, and to be an owner of it.
- `gcloud` (logged in: `gcloud auth login`) and `kubectl` (`gcloud components install kubectl`, or Docker Desktop's).
- A domain whose DNS you control.
- Optional: the `gh` CLI, logged in, so `setup.sh` can set GitHub's variables for you.

## One-time setup

### 1. Fill in .env

```sh
cp .env.example .env
```

(Already have a `.env` from running Argo locally? Keep it and fill in its deploy section.) Open `.env` and fill in the blanks. Each value is explained in the file. Leave `IAP_CLIENT_ID` and `IAP_CLIENT_SECRET` empty for now.

If you already made some of these things by hand (a bucket, a Cloud SQL instance), put their names in and `setup.sh` will use them. An existing Cloud SQL instance must be in `REGION` and have the flag `cloudsql.iam_authentication=on`; the script checks both.

### 2. Run the setup script

```sh
scripts/setup.sh
```

The first run takes 10–20 minutes, mostly creating the cluster and (if needed) the database. It:

1. Enables the Google Cloud APIs.
2. Creates the image registry, a static IP (`argo-ip`), and the bucket if it doesn't exist.
3. Creates the Autopilot cluster `argo` and points `kubectl` at it.
4. Creates the Cloud SQL instance if it doesn't exist, and the database `DB_NAME` inside it.
5. Creates the `argo-app` service account and gives it Vertex AI, the bucket, and Cloud SQL access, and lets the pod act as it.
6. Adds `argo-app` as a database user that logs in with its Google identity (no password).
7. Lets GitHub Actions in `GITHUB_REPO` push images, and sets the workflow's repository variables if `gh` is logged in. Otherwise it prints them for you to add under **Settings → Secrets and variables → Actions → Variables**.
8. Grants sign-in to everyone in `IAP_MEMBERS`.
9. Prints the IP for DNS and anything still missing.

### 3. Google login (the one manual step)

IAP needs an OAuth client, which Google only lets you create in the console:

1. **APIs & Services → OAuth consent screen.** Choose **External**, fill in the app name and your email, then **Publish app** (set it to "In production"). In "Testing" mode only listed test users can sign in and logins expire after 7 days. The basic scopes need no Google verification.
2. **APIs & Services → Credentials → Create credentials → OAuth client ID**, type **Web application**. Save it and copy the Client ID and secret.
3. Edit the client and add the authorized redirect URI `https://iap.googleapis.com/v1/oauth/clientIds/<CLIENT_ID>:handleRedirect`.
4. Put both values in `.env` (`IAP_CLIENT_ID`, `IAP_CLIENT_SECRET`) and run `scripts/setup.sh` again. It skips everything already done and stores them in the cluster.

### 4. Point DNS

At your domain registrar, create an **A record** for your domain (`@`) pointing to the IP `setup.sh` printed. (To see it again: `gcloud compute addresses describe argo-ip --global --format='value(address)'`.)

## Releasing (every time)

```sh
uvx bump2version patch        # or minor / major
git push --follow-tags        # GitHub Actions builds and pushes the image
scripts/deploy.sh             # once the Actions run is green
```

`bump2version` raises the version in `pyproject.toml`, `uv.lock` and `k8s/kustomization.yaml`, commits "Release X.Y.Z" and tags `vX.Y.Z`. Pushing the tag starts the **Build and push image** workflow. It does **not** deploy. `scripts/deploy.sh` deploys exactly the version in your checkout and waits for it to be running. To roll back, check out an older tag and run it again.

No GitHub Actions? `scripts/build-push.sh` builds and pushes the image from your laptop.

`scripts/deploy.sh --print` shows the manifests with your values filled in, without applying anything. Plain `kubectl apply -k k8s/` won't work, because the `${VARS}` in `k8s/` would be left unfilled.

> Note: bump2version is no longer maintained (its successor is `bump-my-version`). It works fine here; switching later only means renaming the config.

## First deploy checklist

1. Release an image as above, and watch the Actions tab until it's green.
2. `scripts/deploy.sh`. It finishes when the pod is ready.
3. Wait for the HTTPS certificate (10–60 min after DNS points at the IP): `kubectl describe managedcertificate argo -n argo` until it says `Active`. The load balancer itself takes ~5 min: `kubectl get ingress -n argo`.
4. Open `https://<domain>/argo/`. You should get a Google login, then the chat. Send a message that makes the agent save a file, which proves Vertex AI, the bucket and the database all work.

## When something is wrong

| Symptom | Likely cause |
| --- | --- |
| 502 from the load balancer | Health check failing, or just created (can take ~10 min). Check `kubectl get pods -n argo`, then `kubectl port-forward deploy/argo 8000:8000 -n argo` and open `localhost:8000/argo/api/health`. |
| Pod `CrashLoopBackOff` | `kubectl logs deploy/argo -n argo -c argo` for the app, `-c cloud-sql-proxy` for the database connection. |
| App log: `password authentication failed` / `role ... does not exist` | The database user is missing. Re-run `scripts/setup.sh`. |
| Proxy log: `403` / `not authorized` | `argo-app` lacks `cloudsql.client` / `cloudsql.instanceUser`, or Workload Identity isn't linked. Re-run `scripts/setup.sh`. |
| App log: `permission denied for schema public` | The database user lacks `cloudsqlsuperuser`. Re-run `scripts/setup.sh`. |
| Pod `Pending` for a while | Autopilot is creating a node; wait a couple of minutes. |
| `ImagePullBackOff` | That version's image doesn't exist yet. Check the Actions run finished. |
| Chat replies fail with 403 | `argo-app` lacks Vertex AI or bucket access. Re-run `scripts/setup.sh`. |
| Certificate stuck on `Provisioning` | DNS doesn't point at the `argo-ip` address yet. |
| IAP "OAuth client not found" / redirect mismatch | The redirect URI in step 3.3 doesn't match the client ID. |
| `You don't have access` after logging in | That account isn't in `IAP_MEMBERS`. Add it and re-run `scripts/setup.sh`. |

## Things to know

- **Everyone you let in shares one set of conversations.** The app uses a fixed user id (`aseem`), so anyone in `IAP_MEMBERS` sees, continues and deletes the same chats and files, and their chats run on your Vertex AI bill. Per-user isolation needs a small code change (use the email IAP puts in the `X-Goog-Authenticated-User-Email` header as the user id).
- **Pods are interchangeable.** Conversations are in Cloud SQL, not on the pod, so updates roll out without downtime and `replicas` in `k8s/deployment.yaml` can go above 1.
- **Local development is unchanged.** Without `DATABASE_URL` in `.env`, the app uses a local SQLite file. To point a local run at Cloud SQL, run the [Cloud SQL Auth Proxy](https://cloud.google.com/sql/docs/postgres/sql-proxy) on your machine with `--auto-iam-authn`, add yourself as an IAM database user, and set `DATABASE_URL=postgresql+asyncpg://<your email, @ as %40>@127.0.0.1:5432/<DB_NAME>`.
- **Old local chats aren't moved.** Chats in `src/.adk/argo.db` stay local; the Cloud SQL database starts empty.
- **Cost** (check current pricing): the pod's requests are roughly $20–30/month on Autopilot, the load balancer and static IP roughly $18–20/month, and a `db-f1-micro` Cloud SQL instance roughly $10/month. Autopilot also charges ~$73/month per cluster, which Google's free tier waives for one cluster per billing account.
