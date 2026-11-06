#!/usr/bin/env bash
# Deploys k8s/ to the cluster, filling the ${VARS} in the manifests from .env.
# Rolls out the image version in k8s/kustomization.yaml (bump2version sets it).
#
#   scripts/deploy.sh           # apply and wait for the rollout
#   scripts/deploy.sh --print   # only print the filled-in manifests
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/lib.sh

manifests=$(kubectl kustomize k8s/)
for var in PROJECT_ID REGION VERTEX_LOCATION BUCKET DOMAIN DB_NAME DB_CONNECTION_NAME; do
  manifests=$(printf '%s\n' "$manifests" | sed "s|\${$var}|${!var}|g")
done

if leftover=$(printf '%s\n' "$manifests" | grep -o '\${[A-Z_]*}' | sort -u) && [[ -n $leftover ]]; then
  echo "error: no value for: $leftover (add it to .env and scripts/deploy.sh)" >&2
  exit 1
fi

if [[ ${1:-} == --print ]]; then
  printf '%s\n' "$manifests"
  exit 0
fi

gcloud container clusters get-credentials argo --region="$REGION" >/dev/null 2>&1
printf '%s\n' "$manifests" | kubectl apply -f -
kubectl rollout status deploy/argo -n argo --timeout=5m
