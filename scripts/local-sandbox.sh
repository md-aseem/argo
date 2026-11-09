#!/usr/bin/env bash
# Owns only kind cluster argo-dev. No .env, Google credentials or current-context.
set -euo pipefail
cd "$(dirname "$0")/.."
KIND=${KIND:-kind}
CLUSTER=argo-dev
CONTEXT=kind-argo-dev
VERSION=v1.0.10
RELEASE="https://github.com/kubernetes-sigs/agent-sandbox/releases/download/$VERSION/sandbox-with-extensions.yaml"

kube() { kubectl --context "$CONTEXT" "$@"; }
assert_local() {
  local server
  server=$(kube config view --minify -o jsonpath='{.clusters[0].cluster.server}')
  case "$server" in
    https://127.0.0.1:*|http://127.0.0.1:*|https://localhost:*|http://localhost:*|https://\[::1\]:*|http://\[::1\]:*) ;;
    *) echo "Refusing non-local API server for $CONTEXT: $server" >&2; exit 1 ;;
  esac
}

case ${1:-} in
  up)
    command -v "$KIND" >/dev/null
    command -v kubectl >/dev/null
    docker info >/dev/null
    # Pin a tested kindnet with NetworkPolicy enforcement and the node version.
    "$KIND" version | awk '{split(substr($2,2),v,"."); if(v[1]==0 && v[2]<31) exit 1}'
    if ! "$KIND" get clusters | grep -qx "$CLUSTER"; then
      previous=$(kubectl config current-context 2>/dev/null || true)
      "$KIND" create cluster --name "$CLUSTER" --image kindest/node:v1.35.0 \
        --config k8s/sandbox/overlays/local/kind.yaml --wait 120s
      if [[ -n $previous ]]; then kubectl --context "$CONTEXT" config use-context "$previous" >/dev/null; fi
    fi
    assert_local
    if [[ $("$KIND" get nodes --name "$CLUSTER" | wc -l | tr -d ' ') -lt 2 ]]; then
      echo "argo-dev needs a separate worker for API isolation; run down then up." >&2
      exit 1
    fi
    kube apply -f "$RELEASE"
    kube rollout status deployment/agent-sandbox-controller -n agent-sandbox-system --timeout=180s
    docker build --tag argo-sandbox:dev sandbox/
    "$KIND" load docker-image argo-sandbox:dev --name "$CLUSTER"
    kube apply -f k8s/sandbox/base/namespace.yaml -f k8s/sandbox/base/networkpolicy.yaml
    kube apply -k k8s/sandbox/overlays/local
    kube rollout status deployment/sandbox-router -n argo-sandbox-router --timeout=120s
    kube wait sandboxwarmpool/argo-python -n argo-sandbox \
      --for=jsonpath='{.status.readyReplicas}'=1 --timeout=300s
    ;;
  tunnel)
    assert_local
    # Fail early when the named context does not exist. Ctrl-C stops retries.
    kube cluster-info >/dev/null
    trap 'exit 0' INT TERM
    while true; do
      kube port-forward --address=127.0.0.1 svc/sandbox-router-svc 18080:8080 -n argo-sandbox-router || true
      sleep 1
    done
    ;;
  down)
    "$KIND" delete cluster --name "$CLUSTER"
    ;;
  *) echo "Usage: $0 {up|tunnel|down}" >&2; exit 2 ;;
esac
