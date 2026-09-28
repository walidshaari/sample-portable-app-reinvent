#!/usr/bin/env bash
# Configuration axis: same wire API, adapter bytes unchanged. Only one
# deployment file changes.
#
#   03-config-axis.sh onprem    PostgreSQL (CloudNativePG) <-> DynamoDB Local (dev only)
#   03-config-axis.sh region    Amazon DynamoDB <-> Amazon Aurora PostgreSQL
#   03-config-axis.sh adapter   the same postgres_repositories.py bytes in
#                               Aurora-backed and CloudNativePG-backed pods
#   03-config-axis.sh reset onprem|region
#                               force the starting state (onprem: postgres,
#                               region: dynamodb); safe to run repeatedly
source "$(dirname "$0")/lib.sh"
TARGET="${1:-onprem}"

code_hashes() {
  (cd "${APP_DIR}" && find domain application infrastructure -name '*.py' -print0 \
    | sort -z | xargs -0 shasum -a 256 | shasum -a 256 | cut -c1-12)
}

flip() {  # overlay ctx url
  local overlay="$1" ctx="$2" url="$3" current next
  current="$(sed -n 's/^REPOSITORY_BACKEND=//p' "${overlay}/persistence.env")"
  case "${overlay##*/}:${current}" in
    onprem:postgres) next=dynamodb-local ;;
    onprem:dynamodb) next=postgres ;;
    region:dynamodb) next=postgres ;;
    region:postgres) next=dynamodb ;;
    *) echo "unexpected REPOSITORY_BACKEND='${current}' in ${overlay}/persistence.env" >&2; return 1 ;;
  esac
  step "health before"; health "${url}"
  local before; before="$(code_hashes)"
  step "diff persistence.env persistence.${next}.env      # the entire change"
  diff -u "${overlay}/persistence.env" "${overlay}/persistence.${next}.env" \
    | sed -e "s#${DEMO_ROOT}/##g" || true
  step "kubectl apply -k ${overlay#${DEMO_ROOT}/}"
  local t0; t0=$(date +%s)
  apply_persistence "${overlay}" "${ctx}" "${next}" "${url}"
  echo "rolled in $(( $(date +%s) - t0 ))s"
  step "health after"; health "${url}"
  step "hash of every .py file in domain/ application/ infrastructure/ (before, after)"
  local after; after="$(code_hashes)"
  [ "${before}" = "${after}" ] ||
    { echo "application code changed during a configuration-only flip" >&2; return 1; }
  echo "before ${before}  after ${after}  identical"
}

# reset <onprem|region>: force the talk's starting state, whatever the current
# one is. Use this to restore after a failed configuration change.
reset() {  # overlay ctx url baseline
  local overlay="$1" ctx="$2" url="$3" baseline="$4"
  step "reset ${overlay##*/} to persistence.${baseline}.env"
  apply_persistence "${overlay}" "${ctx}" "${baseline}" "${url}"
  echo "${overlay##*/} is on ${baseline}"
}
case "${TARGET}" in
  onprem) flip "${ONPREM}" "${KIND_CONTEXT}" "${LOCAL_URL}" ;;
  region) flip "${REGION_OVERLAY}" "${EKS_CONTEXT}" "${EKS_URL}" ;;
  reset)
    case "${2:-}" in
      onprem) reset "${ONPREM}" "${KIND_CONTEXT}" "${LOCAL_URL}" postgres ;;
      region) reset "${REGION_OVERLAY}" "${EKS_CONTEXT}" "${EKS_URL:?EKS_URL is required}" dynamodb ;;
      *) echo "usage: $0 reset onprem|region" >&2; exit 1 ;;
    esac
    ;;
  adapter)
    step "sha256 of the PostgreSQL adapter inside a running pod on each cluster"
    hashes=()
    for ctx in "${EKS_CONTEXT}" "${KIND_CONTEXT}"; do
      pod="$(kubectl --context "${ctx}" -n portable-app get pods -l app=portable-app -o jsonpath='{.items[0].metadata.name}')"
      hash="$(kubectl --context "${ctx}" -n portable-app exec "${pod}" -- python3.11 -c \
        'import hashlib;print(hashlib.sha256(open("/app/infrastructure/repositories/postgres_repositories.py","rb").read()).hexdigest()[:16])'
      )"
      hashes+=("${hash}")
      printf '%-24s %s\n' "${ctx}" "${hash}"
    done
    repo_hash="$(shasum -a 256 "${APP_DIR}/infrastructure/repositories/postgres_repositories.py" | cut -c1-16)"
    printf '%-24s %s\n' "repository" "${repo_hash}"
    [ "${hashes[0]}" = "${repo_hash}" ] && [ "${hashes[1]}" = "${repo_hash}" ] ||
      { echo "PostgreSQL adapter hashes differ" >&2; exit 1; }
    step "what differs: where the connection settings come from"
    echo "region  POSTGRES_HOST=<Aurora cluster endpoint>  credentials: Secrets Manager -> External Secrets -> Secret app-db"
    echo "onprem  POSTGRES_HOST=pg-rw (CloudNativePG)      credentials: Secret pg-app generated in-cluster"
    ;;
  *) echo "usage: $0 onprem|region|adapter" >&2; exit 1 ;;
esac
