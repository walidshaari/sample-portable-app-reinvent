#!/usr/bin/env bash
# One core and one container digest on EKS and kind.
# Reads live endpoints and pod status. Does not change either cluster.
source "$(dirname "$0")/lib.sh"

cd "${DEMO_ROOT}"
local_core="$(cd "${APP_DIR}" && "${PY}" -c 'from infrastructure.composition import core_fingerprint; print(core_fingerprint())')"

fetch_health() {
  local url="$1" expected_runtime="$2" body
  body="$(curl -fsS --max-time 10 "${url}/api/health")"
  echo "${body}" | jq -e --arg runtime "${expected_runtime}" \
    'select(.status == "healthy" and .runtime == $runtime and (.core_sha256 | length) == 64)' >/dev/null ||
    return 1
  echo "${body}"
}

check_ready() {
  curl -fsS --max-time 10 "$1/api/ready" |
    jq -e 'select(.status == "ready")' >/dev/null
}

pod_digest() {
  local ctx="$1"
  kubectl --context "${ctx}" -n portable-app get pods -l app=portable-app -o json |
    jq -re '[.items[].status.containerStatuses[]?.imageID
      | capture("(?<digest>sha256:[0-9a-f]{64})$").digest]
      | unique | select(length == 1) | .[0]'
}

step "EKS in the Region"
eks_health="$(fetch_health "${EKS_URL:?EKS_URL is required}" eks-auto-mode)"
check_ready "${EKS_URL}"
echo "${eks_health}" | jq -c '{runtime, repository_backend, store, core_sha256: .core_sha256[:12]}'

step "kind on this laptop"
kind_health="$(fetch_health "${LOCAL_URL}" kind-onprem)"
check_ready "${LOCAL_URL}"
echo "${kind_health}" | jq -c '{runtime, repository_backend, store, core_sha256: .core_sha256[:12]}'
echo "both stores are ready"

eks_core="$(echo "${eks_health}" | jq -r .core_sha256)"
kind_core="$(echo "${kind_health}" | jq -r .core_sha256)"
[ "${eks_core}" = "${kind_core}" ] && [ "${eks_core}" = "${local_core}" ] ||
  { echo "core fingerprints differ between EKS, kind, and this checkout" >&2; exit 1; }
echo "core fingerprint matches this checkout: ${local_core:0:12}"

step "container image digest in each cluster"
eks_digest="$(pod_digest "${EKS_CONTEXT}")"
kind_digest="$(pod_digest "${KIND_CONTEXT}")"
printf '%-18s %s\n' "EKS" "${eks_digest}" "kind" "${kind_digest}"
[ "${eks_digest}" = "${kind_digest}" ] ||
  { echo "container digests differ between EKS and kind" >&2; exit 1; }
echo "one container digest on both clusters"

image_state="${DEMO_ROOT}/codetalk_deployment/.image_digest"
[ -f "${image_state}" ] ||
  { echo "built image digest state is missing: ${image_state}" >&2; exit 1; }
# shellcheck disable=SC1090
source "${image_state}"
[[ "${IMAGE_DIGEST:-}" =~ ^sha256:[0-9a-f]{64}$ ]] ||
  { echo "built image digest state is invalid" >&2; exit 1; }
[ "${eks_digest}" = "${IMAGE_DIGEST}" ] ||
  { echo "deployed container digest differs from the image built for this checkout" >&2; exit 1; }
echo "container digest matches the locally built image"
