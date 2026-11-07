#!/usr/bin/env bash
# Google Cloud setup for Argo, driven by .env. Re-run it to repair permissions
# or to add people to IAP_MEMBERS.
#
#   scripts/setup.sh
#
# Safe to re-run: every step checks whether its resource already exists, so it
# skips what's done and only creates what's missing. Needs gcloud (logged in as
# a project owner) and kubectl. See docs/production.md for what it set up.
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/lib.sh

step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
note() { printf '    %s\n' "$*"; }
exists() { "$@" >/dev/null 2>&1; }
# add-iam-policy-binding is idempotent but prints the whole policy; keep it quiet.
grant() { "$@" --quiet >/dev/null; }

PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')

step "Enabling Google Cloud APIs (the first run takes a minute)"
gcloud services enable \
  compute.googleapis.com container.googleapis.com artifactregistry.googleapis.com \
  sqladmin.googleapis.com aiplatform.googleapis.com storage.googleapis.com \
  iap.googleapis.com iam.googleapis.com iamcredentials.googleapis.com

step "Image registry: $REGION-docker.pkg.dev/$PROJECT_ID/argo"
exists gcloud artifacts repositories describe argo --location="$REGION" ||
  gcloud artifacts repositories create argo --repository-format=docker --location="$REGION"

step "Static IP for the load balancer: argo-ip"
exists gcloud compute addresses describe argo-ip --global ||
  gcloud compute addresses create argo-ip --global

step "Bucket: gs://$BUCKET"
exists gcloud storage buckets describe "gs://$BUCKET" ||
  gcloud storage buckets create "gs://$BUCKET" --location="$REGION" --uniform-bucket-level-access

step "Kubernetes cluster: argo (Autopilot; the first run takes ~10 minutes)"
exists gcloud container clusters describe argo --region="$REGION" ||
  gcloud container clusters create-auto argo --region="$REGION"
gcloud container clusters get-credentials argo --region="$REGION" # points kubectl at it

step "Database: Cloud SQL instance $DB_INSTANCE"
if ! exists gcloud sql instances describe "$DB_INSTANCE"; then
  # db-f1-micro is the smallest (shared-core) tier, which needs the Enterprise
  # edition. The flag turns on passwordless login with Google identities.
  gcloud sql instances create "$DB_INSTANCE" \
    --database-version=POSTGRES_18 --edition=ENTERPRISE --tier=db-f1-micro \
    --region="$REGION" --database-flags=cloudsql.iam_authentication=on
fi
until [[ $(gcloud sql instances describe "$DB_INSTANCE" --format='value(state)') == RUNNABLE ]]; do
  note "waiting for $DB_INSTANCE to finish starting..."
  sleep 20
done
flags=$(gcloud sql instances describe "$DB_INSTANCE" --format='value(settings.databaseFlags)')
if [[ $flags != *"cloudsql.iam_authentication"*"on"* ]]; then
  echo "error: $DB_INSTANCE needs the database flag cloudsql.iam_authentication=on." >&2
  echo "       Add it in the console (Cloud SQL > $DB_INSTANCE > Edit > Flags), then re-run." >&2
  exit 1
fi
db_region=$(gcloud sql instances describe "$DB_INSTANCE" --format='value(region)')
if [[ $db_region != "$REGION" ]]; then
  echo "error: $DB_INSTANCE is in $db_region but REGION is $REGION. Set REGION=$db_region in .env." >&2
  exit 1
fi
exists gcloud sql databases describe "$DB_NAME" --instance="$DB_INSTANCE" ||
  gcloud sql databases create "$DB_NAME" --instance="$DB_INSTANCE"

step "The app's Google identity: $APP_SA"
exists gcloud iam service-accounts describe "$APP_SA" ||
  gcloud iam service-accounts create argo-app --display-name="Argo app (runs in GKE)"
for role in roles/aiplatform.user roles/cloudsql.client roles/cloudsql.instanceUser; do
  note "$role"
  grant gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:$APP_SA" --role="$role" --condition=None
done
note "roles/storage.objectAdmin on gs://$BUCKET"
grant gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" \
  --member="serviceAccount:$APP_SA" --role=roles/storage.objectAdmin
# Lets the Kubernetes ServiceAccount argo/argo act as this Google service
# account (Workload Identity). The cluster must exist first.
note "Kubernetes ServiceAccount argo/argo may act as it"
grant gcloud iam service-accounts add-iam-policy-binding "$APP_SA" \
  --role=roles/iam.workloadIdentityUser \
  --member="serviceAccount:$PROJECT_ID.svc.id.goog[argo/argo]"

step "Database login for the app: $DB_USER"
# cloudsqlsuperuser lets the app create its tables on first start. The instance
# only holds Argo's data, so that's an acceptable scope.
if exists gcloud sql users describe "$DB_USER" --instance="$DB_INSTANCE"; then
  gcloud sql users assign-roles "$DB_USER" --instance="$DB_INSTANCE" \
    --type=cloud_iam_service_account --database-roles=cloudsqlsuperuser --quiet >/dev/null
else
  gcloud sql users create "$DB_USER" --instance="$DB_INSTANCE" \
    --type=cloud_iam_service_account --database-roles=cloudsqlsuperuser
fi

if [[ -n ${GITHUB_REPO:-} ]]; then
  step "GitHub Actions may push images (repo $GITHUB_REPO, no stored keys)"
  BUILDER_SA="gh-argo-builder@$PROJECT_ID.iam.gserviceaccount.com"
  PROVIDER="projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/github/providers/argo-repo"
  exists gcloud iam workload-identity-pools describe github --location=global ||
    gcloud iam workload-identity-pools create github --location=global --display-name=GitHub
  exists gcloud iam workload-identity-pools providers describe argo-repo \
    --location=global --workload-identity-pool=github ||
    gcloud iam workload-identity-pools providers create-oidc argo-repo \
      --location=global --workload-identity-pool=github \
      --issuer-uri=https://token.actions.githubusercontent.com \
      --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
      --attribute-condition="assertion.repository=='$GITHUB_REPO'"
  exists gcloud iam service-accounts describe "$BUILDER_SA" ||
    gcloud iam service-accounts create gh-argo-builder --display-name="GitHub image builder"
  grant gcloud artifacts repositories add-iam-policy-binding argo --location="$REGION" \
    --member="serviceAccount:$BUILDER_SA" --role=roles/artifactregistry.writer
  grant gcloud iam service-accounts add-iam-policy-binding "$BUILDER_SA" \
    --role=roles/iam.workloadIdentityUser \
    --member="principalSet://iam.googleapis.com/projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/github/attribute.repository/$GITHUB_REPO"

  if command -v gh >/dev/null && gh auth status >/dev/null 2>&1; then
    note "setting the workflow's repository variables with gh"
    gh variable set GCP_PROJECT_ID --repo "$GITHUB_REPO" --body "$PROJECT_ID"
    gh variable set GCP_REGION --repo "$GITHUB_REPO" --body "$REGION"
    gh variable set GCP_SERVICE_ACCOUNT --repo "$GITHUB_REPO" --body "$BUILDER_SA"
    gh variable set GCP_WORKLOAD_IDENTITY_PROVIDER --repo "$GITHUB_REPO" --body "$PROVIDER"
  else
    note "gh isn't logged in, so add these yourself in GitHub:"
    note "Settings > Secrets and variables > Actions > Variables"
    note "  GCP_PROJECT_ID                  $PROJECT_ID"
    note "  GCP_REGION                      $REGION"
    note "  GCP_SERVICE_ACCOUNT             $BUILDER_SA"
    note "  GCP_WORKLOAD_IDENTITY_PROVIDER  $PROVIDER"
  fi
fi

step "Google login (IAP)"
kubectl create namespace argo --dry-run=client -o yaml | kubectl apply -f - >/dev/null
iap_ready=yes
if [[ -n ${IAP_CLIENT_ID:-} && -n ${IAP_CLIENT_SECRET:-} ]]; then
  note "storing the OAuth client in the cluster (Secret argo/iap-oauth)"
  kubectl create secret generic iap-oauth -n argo \
    --from-literal=client_id="$IAP_CLIENT_ID" --from-literal=client_secret="$IAP_CLIENT_SECRET" \
    --dry-run=client -o yaml | kubectl apply -f - >/dev/null
else
  iap_ready=no
  note "IAP_CLIENT_ID / IAP_CLIENT_SECRET are empty in .env. Create the OAuth"
  note "client (APIs & Services > Credentials), fill them in, and re-run."
fi
IFS=',' read -r -a members <<<"${IAP_MEMBERS:-}"
for member in ${members[@]+"${members[@]}"}; do # (the odd form survives an empty list on macOS bash 3.2)
  member=$(echo "$member" | tr -d '[:space:]')
  [[ -z $member ]] && continue
  note "allowing $member"
  grant gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="$member" --role=roles/iap.httpsResourceAccessor --condition=None
done

ip=$(gcloud compute addresses describe argo-ip --global --format='value(address)')
step "Done"
note "1. DNS: at your domain registrar, add an A record for $DOMAIN pointing to $ip"
if [[ $iap_ready == no ]]; then
  note "2. Fill in IAP_CLIENT_ID / IAP_CLIENT_SECRET and run scripts/setup.sh again."
fi
note "Then release an image (uvx bump2version patch && git push --follow-tags)"
note "and deploy it with scripts/deploy.sh."
