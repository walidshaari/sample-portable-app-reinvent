#!/usr/bin/env bash
# Run before the session with time to recover. Puts the targets in their starting state
# and checks every dependency of the live beats. Prints PASS/FAIL per check.
#
#   ALLOWED_CIDR=a.b.c.d/32 demo/00-preflight.sh
#   checks kind and EKS, and re-points the EKS NLB at the venue IP.
source "$(dirname "$0")/lib.sh"
case "${1:-two-targets}" in
  two-targets) ;;
  *) echo "usage: $0 [two-targets]" >&2; exit 2 ;;
esac
fails=0
check() { local name="$1"; shift; if "$@" >/dev/null 2>&1; then echo "PASS  ${name}"; else echo "FAIL  ${name}"; fails=$((fails+1)); fi; }

cd "${DEMO_ROOT}"

if [ -n "${ALLOWED_CIDR:-}" ]; then
  kubectl --context "${EKS_CONTEXT}" -n portable-app patch svc portable-app --type merge \
    -p "{\"spec\":{\"loadBalancerSourceRanges\":[\"${ALLOWED_CIDR}\"]}}" >/dev/null
  echo "NLB source range set to ${ALLOWED_CIDR}"
fi

# Starting state: Region on DynamoDB, on-premises on PostgreSQL, and
# PostgreSQL technology patch applied. Refuse an unfamiliar local state.
tech_patch="${DEMO_ROOT}/demo/patches/tech-axis-postgres.patch"
if git apply -R --check "${tech_patch}" 2>/dev/null; then
  :
elif git apply --check "${tech_patch}" 2>/dev/null; then
  git apply "${tech_patch}" || { echo "could not restore technology patch" >&2; exit 1; }
else
  echo "technology files differ from both demo states; resolve them before preflight" >&2
  exit 1
fi
cmp -s "${REGION_OVERLAY}/persistence.env" "${REGION_OVERLAY}/persistence.dynamodb.env" \
  || apply_persistence "${REGION_OVERLAY}" "${EKS_CONTEXT}" dynamodb "${EKS_URL}" \
  || { echo "could not restore the Region backend" >&2; exit 1; }
cmp -s "${ONPREM}/persistence.env" "${ONPREM}/persistence.postgres.env" \
  || apply_persistence "${ONPREM}" "${KIND_CONTEXT}" postgres "${LOCAL_URL}" \
  || { echo "could not restore the on-premises backend" >&2; exit 1; }

check "podman machine running"            podman info
check "test PostgreSQL container"         podman exec portable-pg-test pg_isready -U app
check "kind registry"                     curl -sf http://localhost:5001/v2/
check "EKS reachable from this IP"        curl -sf --max-time 5 "${EKS_URL}/api/health"
check "EKS on DynamoDB"                   test "$(curl -s --max-time 5 "${EKS_URL}/api/health" | jq -r .repository_backend)" = dynamodb
check "kind on PostgreSQL"                test "$(curl -s --max-time 5 "${LOCAL_URL}/api/health" | jq -r .repository_backend)" = postgres
check "CloudNativePG 2 instances ready"   test "$(kubectl --context "${KIND_CONTEXT}" -n portable-app get cluster pg -o jsonpath='{.status.readyInstances}')" = 2
check "DynamoDB Local ready"              kubectl --context "${KIND_CONTEXT}" -n dev-tools rollout status deploy/dynamodb-local --timeout=5s
check "Aurora available"                  test "$(aws rds describe-db-instances --db-instance-identifier portable-app-aurora-1 --query 'DBInstances[0].DBInstanceStatus' --output text)" = available
check "same image digest on both"         test "$(for c in "${EKS_CONTEXT}" "${KIND_CONTEXT}"; do kubectl --context "$c" -n portable-app get pods -l app=portable-app -o jsonpath='{range .items[*]}{.status.containerStatuses[0].imageID}{"\n"}{end}' | sed 's/.*@//'; done | grep . | sort -u | wc -l | tr -d ' ')" = 1
check "two-target proof"                   "${DEMO_ROOT}/demo/09-two-targets.sh"
check "core unchanged vs HEAD"            git diff --quiet HEAD -- base_python/clean/domain base_python/clean/application
check "archguard clean"                   python3 kiro/archguard/archguard.py check
check "Kiro write hook installed"         test -f .kiro/hooks/clean-guard-write.json
check "core tests pass offline"           env -i PATH="${PATH}" HOME="${HOME}" bash -c "cd '${APP_DIR}' && '${PY}' -m pytest tests/core -q -p no:cacheprovider"
check "tech patch is applied"             git apply -R --check demo/patches/tech-axis-postgres.patch

echo
[ "${fails}" -eq 0 ] && echo "preflight: all checks passed" || echo "preflight: ${fails} check(s) failed"
exit "${fails}"
