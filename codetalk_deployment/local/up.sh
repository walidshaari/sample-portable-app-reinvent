#!/usr/bin/env bash
# On-premises stand-in: kind on Podman, a local registry, CloudNativePG,
# DynamoDB Local (development only) and the onprem overlay.
#
# Idempotent. Runs the same image digest that was pushed to the Region:
# push-image.sh local copies the same OCI layout with --preserve-digests.
#
# Network is needed only for the first run (to pull the kind node image,
# the operator and the PostgreSQL image). After that the cluster runs with
# no cloud credentials and an egress policy that keeps the app in-cluster.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${HERE}/../.." && pwd)"
OVERLAY="${REPO_ROOT}/codetalk_deployment/k8s/overlays/onprem"
GEN="${OVERLAY}/.generated"
CACHE="${REPO_ROOT}/codetalk_deployment/.build"
STATE="${REPO_ROOT}/codetalk_deployment/.local_state"

export KIND_EXPERIMENTAL_PROVIDER=podman
CLUSTER="portable-onprem"
CONTEXT="kind-${CLUSTER}"
REGISTRY_NAME="kind-registry"
REGISTRY_PORT="5001"
REGISTRY_IMAGE="public.ecr.aws/docker/library/registry:3@sha256:325b4b29b041e82803abeb703e201655e4e23ab83264ec1a7c9ddb0a5b14a6e0"
CNPG_VERSION="1.30.1"
CNPG_MANIFEST_URL="https://raw.githubusercontent.com/cloudnative-pg/cloudnative-pg/release-1.30/releases/cnpg-${CNPG_VERSION}.yaml"
NAMESPACE="portable-app"
KCTX=(--context "${CONTEXT}")

say() { printf '\n==> %s\n' "$*"; }
# shellcheck disable=SC1091
source "${REPO_ROOT}/codetalk_deployment/.image_digest"

say "local registry ${REGISTRY_NAME} on 127.0.0.1:${REGISTRY_PORT}"
if [ "$(podman inspect -f '{{.State.Running}}' "${REGISTRY_NAME}" 2>/dev/null || true)" != "true" ]; then
  podman rm -f "${REGISTRY_NAME}" >/dev/null 2>&1 || true
  podman run -d --restart=always --name "${REGISTRY_NAME}" \
    -p "127.0.0.1:${REGISTRY_PORT}:5000" "${REGISTRY_IMAGE}" >/dev/null
fi

say "kind cluster ${CLUSTER}"
if ! kind get clusters 2>/dev/null | grep -qx "${CLUSTER}"; then
  kind create cluster --config "${HERE}/kind-config.yaml" --wait 120s
fi
podman network connect kind "${REGISTRY_NAME}" >/dev/null 2>&1 || true
for node in $(kind get nodes --name "${CLUSTER}"); do
  podman exec "${node}" mkdir -p "/etc/containerd/certs.d/localhost:${REGISTRY_PORT}"
  printf '[host."http://%s:5000"]\n' "${REGISTRY_NAME}" | \
    podman exec -i "${node}" cp /dev/stdin "/etc/containerd/certs.d/localhost:${REGISTRY_PORT}/hosts.toml"
done

say "image ${IMAGE_TAG} into the local registry (digest preserved)"
LOCAL_REGISTRY="localhost:${REGISTRY_PORT}" "${REPO_ROOT}/codetalk_deployment/image/push-image.sh" local

say "CloudNativePG operator ${CNPG_VERSION}"
mkdir -p "${CACHE}"
[ -f "${CACHE}/cnpg-${CNPG_VERSION}.yaml" ] || curl -fsSL -o "${CACHE}/cnpg-${CNPG_VERSION}.yaml" "${CNPG_MANIFEST_URL}"
kubectl "${KCTX[@]}" apply --server-side --force-conflicts -f "${CACHE}/cnpg-${CNPG_VERSION}.yaml" >/dev/null
kubectl "${KCTX[@]}" -n cnpg-system rollout status deployment/cnpg-controller-manager --timeout=300s

say "DynamoDB Local (development and testing only)"
kubectl "${KCTX[@]}" apply -f "${HERE}/dynamodb-local.yaml" >/dev/null
kubectl "${KCTX[@]}" -n dev-tools rollout status deployment/dynamodb-local --timeout=300s

say "generated overlay input (git-ignored)"
mkdir -p "${GEN}"
cat > "${GEN}/kustomization.yaml" <<EOF
apiVersion: kustomize.config.k8s.io/v1alpha1
kind: Component
images:
  - name: portable-app
    newName: localhost:${REGISTRY_PORT}/portable-app
    digest: ${IMAGE_DIGEST}
EOF

say "deploy overlay"
kubectl "${KCTX[@]}" -n "${NAMESPACE}" delete job portable-app-migrate --ignore-not-found >/dev/null 2>&1 || true
kubectl "${KCTX[@]}" apply -k "${OVERLAY}"
if grep -q '^REPOSITORY_BACKEND=postgres' "${OVERLAY}/persistence.env"; then
  kubectl "${KCTX[@]}" -n "${NAMESPACE}" wait --for=condition=Ready cluster.postgresql.cnpg.io/pg --timeout=600s
fi
kubectl "${KCTX[@]}" -n "${NAMESPACE}" wait --for=condition=complete job/portable-app-migrate --timeout=300s
kubectl "${KCTX[@]}" -n "${NAMESPACE}" rollout restart deployment/portable-app >/dev/null
kubectl "${KCTX[@]}" -n "${NAMESPACE}" rollout status deployment/portable-app --timeout=300s

cat > "${STATE}" <<EOF
export KIND_CONTEXT=${CONTEXT}
export LOCAL_URL=http://localhost:8080
EOF
echo "LOCAL_URL=http://localhost:8080  (state in ${STATE})"
