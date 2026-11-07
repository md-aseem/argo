# Shared by the scripts in this folder: loads .env and checks it.
# Not meant to be run on its own.

ENV_FILE=${ENV_FILE:-.env}

if [[ ! -f $ENV_FILE ]]; then
  echo "No $ENV_FILE found. Copy .env.example to .env and fill it in." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

for var in GOOGLE_CLOUD_PROJECT GOOGLE_CLOUD_LOCATION GOOGLE_CLOUD_BUCKET \
  REGION DB_INSTANCE DB_NAME DOMAIN; do
  if [[ -z ${!var:-} ]]; then
    echo "$ENV_FILE: $var is empty. Fill it in (see .env.example)." >&2
    exit 1
  fi
done

# Short names used by the scripts and the ${VARS} in k8s/.
PROJECT_ID=$GOOGLE_CLOUD_PROJECT
VERTEX_LOCATION=$GOOGLE_CLOUD_LOCATION
BUCKET=${GOOGLE_CLOUD_BUCKET#gs://}

# Derived values, the same everywhere.
DB_CONNECTION_NAME="$PROJECT_ID:$REGION:$DB_INSTANCE"
APP_SA="argo-app@$PROJECT_ID.iam.gserviceaccount.com"  # the pod's Google identity
DB_USER="argo-app@$PROJECT_ID.iam"                    # how Cloud SQL names that identity
IMAGE="$REGION-docker.pkg.dev/$PROJECT_ID/argo/argo"

# Every gcloud call in these scripts targets PROJECT_ID, without changing your
# global gcloud config.
export CLOUDSDK_CORE_PROJECT=$PROJECT_ID
