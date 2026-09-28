#!/usr/bin/env bash
# Shared helpers for the live demo scripts. Source it; do not run it.
set -euo pipefail

DEMO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ONPREM="${DEMO_ROOT}/codetalk_deployment/k8s/overlays/onprem"
REGION_OVERLAY="${DEMO_ROOT}/codetalk_deployment/k8s/overlays/region"
APP_DIR="${DEMO_ROOT}/base_python/clean"
PY="${DEMO_ROOT}/.venv/bin/python"

# shellcheck disable=SC1091
[ -f "${DEMO_ROOT}/codetalk_deployment/.local_state" ] && source "${DEMO_ROOT}/codetalk_deployment/.local_state"
# shellcheck disable=SC1091
[ -f "${DEMO_ROOT}/codetalk_deployment/.region_state" ] && source "${DEMO_ROOT}/codetalk_deployment/.region_state"
export AWS_REGION="${AWS_REGION:-us-east-1}" AWS_PAGER=""
LOCAL_URL="${LOCAL_URL:-http://localhost:8080}"

KIND_CONTEXT="${KIND_CONTEXT:-kind-portable-onprem}"
EKS_CONTEXT="${EKS_CONTEXT:-portable-app-eks}"

step() { printf '\n\033[1m$ %s\033[0m\n' "$*"; }
run()  { step "$*"; eval "$@"; }

# health <url>: the identity fields the talk points at. Retries briefly and
# never aborts the demo: a load balancer re-registering targets can drop one
# request.
health() {
  local body=""
  for _ in 1 2 3 4 5; do
    body="$(curl -fsS --max-time 10 "$1/api/health" 2>/dev/null || true)"
    [ -n "${body}" ] && break
    sleep 2
  done
  # Warn and return 0 on purpose: callers run under set -e, and one slow
  # target must not end the rest of the beat.
  if [ -z "${body}" ]; then echo "WARN: no response from $1" >&2; return 0; fi
  echo "${body}" | jq -ec '
    select(.status == "healthy" and .runtime != null and .backend != null and .core_sha256 != null)
    | {runtime, backend, store, endpoint, node, core_sha256: (.core_sha256[0:12])}' \
    || { echo "WARN: unexpected health body from $1: ${body}" >&2; return 0; }
}

# wait_backend <url> <repository_backend>: until every answer reports it
wait_backend() {
  local url="$1" want="$2" ok=0
  for _ in $(seq 1 60); do
    if [ "$(curl -s --max-time 3 "${url}/api/health" | jq -r .repository_backend 2>/dev/null)" = "${want}" ]; then
      ok=$((ok + 1)); [ "${ok}" -ge 4 ] && return 0
    else
      ok=0
    fi
    sleep 1
  done
  echo "error: ${url} did not settle on ${want}" >&2
  return 1
}

# apply_persistence <overlay-dir> <context> <name> [url]: config-only switch
apply_persistence() {
  local overlay="$1" ctx="$2" name="$3" url="${4:-}"
  cp "${overlay}/persistence.${name}.env" "${overlay}/persistence.env"
  kubectl --context "${ctx}" -n portable-app delete job portable-app-migrate --ignore-not-found >/dev/null
  kubectl --context "${ctx}" apply -k "${overlay}" >/dev/null
  kubectl --context "${ctx}" -n portable-app wait --for=condition=complete job/portable-app-migrate --timeout=180s >/dev/null
  kubectl --context "${ctx}" -n portable-app rollout status deployment/portable-app --timeout=180s >/dev/null
  if [ -n "${url}" ]; then
    wait_backend "${url}" "$(sed -n 's/^REPOSITORY_BACKEND=//p' "${overlay}/persistence.env")"
  fi
}
