#!/usr/bin/env bash
# Build the portable app image once, gate it on a vulnerability scan, and save
# it as an OCI layout so it can be copied to any registry without changing its
# digest (skopeo copy --preserve-digests).
#
# Outputs (git-ignored):
#   codetalk_deployment/.build/oci        OCI layout of the image
#   codetalk_deployment/.build/sbom.json  SPDX SBOM (syft)
#   codetalk_deployment/.build/scan.json  grype findings
#   codetalk_deployment/.image_digest     IMAGE_TAG and IMAGE_DIGEST exports
#
# Scan gate: the build fails on any High or Critical finding. Override only
# with an explicit, reviewed exception: ALLOW_FINDINGS="CVE-1 GHSA-2".
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${HERE}/../.." && pwd)"
BUILD_DIR="${REPO_ROOT}/codetalk_deployment/.build"
STATE_FILE="${REPO_ROOT}/codetalk_deployment/.image_digest"

# shellcheck source=base-image.env
source "${HERE}/base-image.env"
BASE_IMAGE="${BASE_IMAGE:-${DEFAULT_BASE_IMAGE}}"
PLATFORM="${PLATFORM:-linux/arm64}"
IMAGE_TAG="${IMAGE_TAG:-demo-$(date -u +%Y%m%d%H%M)}"
LOCAL_REF="localhost/portable-app:${IMAGE_TAG}"
ALLOW_FINDINGS="${ALLOW_FINDINGS:-}"

case "${BASE_IMAGE}" in
  *@sha256:*) ;;
  *) echo "BASE_IMAGE must be pinned by digest: ${BASE_IMAGE}" >&2; exit 1 ;;
esac

for tool in podman skopeo grype syft jq; do
  command -v "${tool}" >/dev/null || { echo "missing tool: ${tool}" >&2; exit 1; }
done

mkdir -p "${BUILD_DIR}"
echo "==> build ${LOCAL_REF} (${PLATFORM}) from ${BASE_IMAGE}"
podman build \
  --platform "${PLATFORM}" \
  --build-arg "BASE_IMAGE=${BASE_IMAGE}" \
  --file "${REPO_ROOT}/codetalk_deployment/Dockerfile" \
  --tag "${LOCAL_REF}" \
  "${REPO_ROOT}"

echo "==> save as OCI layout"
rm -rf "${BUILD_DIR}/oci" "${BUILD_DIR}/image.tar"
# podman on macOS talks to a VM and only exports archives, so export an
# oci-archive and let skopeo write a gzip-compressed OCI layout. That layout is
# the canonical artifact: every later copy preserves its digest.
podman save --quiet --format oci-archive -o "${BUILD_DIR}/image.tar" "${LOCAL_REF}"
skopeo copy --quiet --dest-compress-format gzip \
  "oci-archive:${BUILD_DIR}/image.tar" "oci:${BUILD_DIR}/oci:${IMAGE_TAG}"
rm -f "${BUILD_DIR}/image.tar"
IMAGE_DIGEST="$(skopeo inspect --format '{{.Digest}}' "oci:${BUILD_DIR}/oci:${IMAGE_TAG}")"

echo "==> SBOM and scan gate"
syft -q "oci-dir:${BUILD_DIR}/oci" -o spdx-json > "${BUILD_DIR}/sbom.json"
grype -q "sbom:${BUILD_DIR}/sbom.json" -o json > "${BUILD_DIR}/scan.json"
BLOCKING="$(jq -r --arg allow " ${ALLOW_FINDINGS} " '
  [.matches[]
   | select(.vulnerability.severity == "High" or .vulnerability.severity == "Critical")
   | select(($allow | contains(" " + .vulnerability.id + " ")) | not)
   | "\(.vulnerability.severity) \(.vulnerability.id) \(.artifact.name) \(.artifact.version)"]
  | unique | .[]' "${BUILD_DIR}/scan.json")"
TOTAL="$(jq '.matches | length' "${BUILD_DIR}/scan.json")"
if [ -n "${BLOCKING}" ]; then
  echo "scan gate FAILED: High/Critical findings:" >&2
  echo "${BLOCKING}" >&2
  exit 2
fi
echo "scan gate passed: 0 High/Critical (${TOTAL} lower-severity findings, see ${BUILD_DIR}/scan.json)"

cat > "${STATE_FILE}" <<EOF
export IMAGE_TAG=${IMAGE_TAG}
export IMAGE_DIGEST=${IMAGE_DIGEST}
EOF
echo "==> ${LOCAL_REF}"
echo "    digest ${IMAGE_DIGEST}"
echo "    state  ${STATE_FILE}"
