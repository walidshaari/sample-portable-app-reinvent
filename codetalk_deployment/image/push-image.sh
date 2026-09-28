#!/usr/bin/env bash
# Copy the built OCI layout to a registry with its digest preserved.
#
#   push-image.sh ecr     private Amazon ECR repository (created if absent,
#                         immutable tags, scan on push)
#   push-image.sh local   the kind cluster's local registry (localhost:5001)
#
# Both destinations receive the same manifest bytes, so the same
# sha256 digest runs in the Region and on the laptop.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${HERE}/../.." && pwd)"
BUILD_DIR="${REPO_ROOT}/codetalk_deployment/.build"
# shellcheck disable=SC1091
source "${REPO_ROOT}/codetalk_deployment/.image_digest"

TARGET="${1:?usage: push-image.sh ecr|local}"
REPOSITORY="${REPOSITORY:-portable-app}"
SOURCE="oci:${BUILD_DIR}/oci:${IMAGE_TAG}"

case "${TARGET}" in
  ecr)
    AWS_REGION="${AWS_REGION:?set AWS_REGION}"
    ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
    REGISTRY="${ACCOUNT}.dkr.ecr.${AWS_REGION}.amazonaws.com"
    if ! aws ecr describe-repositories --repository-names "${REPOSITORY}" --region "${AWS_REGION}" >/dev/null 2>&1; then
      aws ecr create-repository --repository-name "${REPOSITORY}" --region "${AWS_REGION}" \
        --image-tag-mutability IMMUTABLE \
        --image-scanning-configuration scanOnPush=true \
        --tags Key=project,Value=portable-app >/dev/null
      echo "created ECR repository ${REPOSITORY}"
    fi
    skopeo copy --preserve-digests --quiet \
      --dest-creds "AWS:$(aws ecr get-login-password --region "${AWS_REGION}")" \
      "${SOURCE}" "docker://${REGISTRY}/${REPOSITORY}:${IMAGE_TAG}"
    ;;
  local)
    REGISTRY="${LOCAL_REGISTRY:-localhost:5001}"
    skopeo copy --preserve-digests --quiet --dest-tls-verify=false \
      "${SOURCE}" "docker://${REGISTRY}/${REPOSITORY}:${IMAGE_TAG}"
    ;;
  *) echo "unknown target ${TARGET}" >&2; exit 1 ;;
esac

PUSHED="$(skopeo inspect --tls-verify=$([ "${TARGET}" = local ] && echo false || echo true) \
  $([ "${TARGET}" = ecr ] && echo --creds "AWS:$(aws ecr get-login-password --region "${AWS_REGION}")") \
  --format '{{.Digest}}' "docker://${REGISTRY}/${REPOSITORY}:${IMAGE_TAG}")"
if [ "${PUSHED}" != "${IMAGE_DIGEST}" ]; then
  echo "digest changed in transit: built ${IMAGE_DIGEST}, registry ${PUSHED}" >&2
  exit 3
fi
echo "${TARGET}: ${REGISTRY}/${REPOSITORY}@${PUSHED}"
