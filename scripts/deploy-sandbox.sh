#!/usr/bin/env bash
# Deploy phase-one sandbox resources separately from the app.
#   scripts/deploy-sandbox.sh --print
#   scripts/deploy-sandbox.sh
# Does not install/update the controller or the admission policy/binding.
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/lib.sh

manifests=$(kubectl kustomize k8s/sandbox/)
for var in PROJECT_ID REGION; do
  manifests=$(printf '%s\n' "$manifests" | sed "s|\${$var}|${!var}|g")
done

if leftover=$(printf '%s\n' "$manifests" | grep -o '\${[A-Z_]*}' | sort -u) && [[ -n $leftover ]]; then
  echo "error: no value for: $leftover" >&2
  exit 1
fi

if [[ ${1:-} == --print ]]; then
  printf '%s\n' "$manifests"
  exit 0
fi

gcloud container clusters get-credentials argo --region="$REGION" >/dev/null 2>&1
# Establish isolation before the warm pool can create any pods. Kustomize's
# kind ordering otherwise puts NetworkPolicy after SandboxWarmPool.
kubectl apply -f k8s/sandbox/namespace.yaml -f k8s/sandbox/networkpolicy.yaml
printf '%s\n' "$manifests" | kubectl apply -f -
kubectl wait sandboxwarmpool/argo-python -n argo-sandbox \
  --for=jsonpath='{.status.readyReplicas}'=1 --timeout=600s
