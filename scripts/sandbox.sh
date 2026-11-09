#!/usr/bin/env bash
# Check the sandbox and open a shell, or use --check for a health check only.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/sandbox.sh [options] [-- command [args...]]

Checks the pod, its node, and the runtime HTTP health endpoint, then opens
/bin/sh in the sandbox's runtime container. Requires existing kubectl access.

Options:
  --check             Check health and exit without opening a shell.
  --context NAME      Kubernetes context (default: current context).
  --namespace NAME    Sandbox namespace (default: argo-sandbox).
  --pod NAME          Select a pod explicitly; required if several exist.
  -h, --help          Show this help.

Examples:
  scripts/sandbox.sh --check
  scripts/sandbox.sh
  scripts/sandbox.sh --pod argo-python-abcde
  scripts/sandbox.sh -- python --version

The script does not provision resources or change your kubectl context.
Changes made inside the shell affect the selected sandbox's workspace.
EOF
}

fail() { echo "error: $*" >&2; exit 1; }

context=""
namespace=argo-sandbox
pod=""
check_only=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --check) check_only=true; shift ;;
    --context|--namespace|--pod)
      [[ $# -ge 2 && -n $2 && $2 != -* ]] || fail "$1 requires a value."
      case "$1" in
        --context) context=$2 ;;
        --namespace) namespace=$2 ;;
        --pod) pod=$2 ;;
      esac
      shift 2
      ;;
    -h|--help) usage; exit 0 ;;
    --) shift; [[ $# -gt 0 ]] || fail "Expected a command after --."; break ;;
    *) fail "Unknown option: $1 (use --help)." ;;
  esac
done
if $check_only && [[ $# -gt 0 ]]; then
  fail "Use either --check or a command after --."
fi

command -v kubectl >/dev/null || fail "kubectl is required."
if [[ -z $context ]]; then
  context=$(kubectl config current-context) || fail "Select a kubectl context first, or pass --context."
fi
# Pin every request to the selected context without modifying kubeconfig.
kube=(kubectl --context="$context" --request-timeout=20s)
echo "Context: $context"
echo "Namespace: $namespace"

if [[ -z $pod ]]; then
  pod_names=$("${kube[@]}" get pods -n "$namespace" -l app=argo-sandbox \
    -o custom-columns=NAME:.metadata.name --no-headers)
  pods=()
  while IFS= read -r name; do
    [[ -z $name ]] || pods+=("$name")
  done <<< "$pod_names"
  case ${#pods[@]} in
    0) fail "No sandbox pods found in $namespace. Check scripts/deploy-sandbox.sh." ;;
    1) pod=${pods[0]} ;;
    *) printf 'Available sandbox pods:\n%s\n' "$pod_names" >&2
       fail "Several sandboxes exist; choose one with --pod NAME." ;;
  esac
fi
[[ $pod != -* ]] || fail "Invalid pod name."
label=$("${kube[@]}" get pod "$pod" -n "$namespace" -o jsonpath='{.metadata.labels.app}')
[[ $label == argo-sandbox ]] || fail "$pod is not labelled app=argo-sandbox."

"${kube[@]}" get pod "$pod" -n "$namespace" -o wide
status=$("${kube[@]}" get pod "$pod" -n "$namespace" \
  -o jsonpath='{.status.phase}{" "}{.status.conditions[?(@.type=="Ready")].status}{" "}{.spec.nodeName}{" "}{.metadata.deletionTimestamp}')
read -r phase ready node deleting <<< "$status"
if [[ $phase != Running || $ready != True || -z $node || -n $deleting ]]; then
  "${kube[@]}" get events -n "$namespace" \
    --field-selector "involvedObject.kind=Pod,involvedObject.name=$pod" \
    --sort-by=.lastTimestamp >&2 || true
  fail "$pod is not running and ready. Use kubectl --context=\"$context\" describe pod \"$pod\" -n \"$namespace\" for details."
fi

"${kube[@]}" get node "$node" -o wide
node_ready=$("${kube[@]}" get node "$node" \
  -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}')
[[ $node_ready == True ]] || fail "Node $node is not Ready."

# Probe from inside the container: no port-forward, curl dependency, or changes
# to the sandbox's deny-all egress policy are needed.
"${kube[@]}" exec -n "$namespace" "$pod" -c runtime -- python -c '
import json
import urllib.request

with urllib.request.urlopen("http://127.0.0.1:8888/", timeout=5) as response:
    health = json.load(response)
if health.get("status") != "ok":
    raise SystemExit("Runtime health check failed: " + repr(health))
print("Runtime health: ok")
'
echo "Sandbox is running and healthy."
$check_only && exit 0

if [[ $# -gt 0 ]]; then
  exec "${kube[@]}" exec -n "$namespace" "$pod" -c runtime -- "$@"
fi
[[ -t 0 && -t 1 ]] || fail "An interactive terminal is required for a shell; use --check or -- command."
echo "Opening shell in $pod (/workspace). Type exit to leave."
# A shell has no fixed duration; keep the health/API requests bounded above.
exec kubectl --context="$context" exec -it -n "$namespace" "$pod" -c runtime -- /bin/sh
