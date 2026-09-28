#!/usr/bin/env bash
# Tear down what region/up.sh created. Destructive and not reversible for the
# data in DynamoDB and Aurora. Asks for confirmation unless CONFIRM=yes.
#
# Order: Kubernetes resources (so the NLB is released), Pod Identity
# associations and roles, Aurora (deletion protection is turned off first,
# no final snapshot unless FINAL_SNAPSHOT=1), DynamoDB tables, ECR repository.
# The EKS cluster itself is deleted only with DELETE_CLUSTER=1.
set -euo pipefail
AWS_REGION="${AWS_REGION:?set AWS_REGION}"
CLUSTER_NAME="${CLUSTER_NAME:-portable-app-eks}"
PREFIX="portable-app"
export AWS_REGION AWS_PAGER=""

echo "This deletes in ${AWS_REGION}: namespace ${PREFIX} on ${CLUSTER_NAME}, the NLB, IAM roles"
echo "${PREFIX}-pod and ${PREFIX}-eso, Aurora ${PREFIX}-aurora, DynamoDB ${PREFIX}-users/orders,"
echo "ECR ${PREFIX}$([ "${DELETE_CLUSTER:-0}" = 1 ] && echo ", and the EKS cluster ${CLUSTER_NAME}")."
if [ "${CONFIRM:-}" != "yes" ]; then
  read -r -p "Type the Region to confirm: " answer
  [ "${answer}" = "${AWS_REGION}" ] || { echo "aborted"; exit 1; }
fi

# Every security group lookup is scoped to the cluster VPC, so a same-named
# group elsewhere is never hit.
VPC_ID="$(aws eks describe-cluster --name "${CLUSTER_NAME}" --query cluster.resourcesVpcConfig.vpcId --output text 2>/dev/null || true)"
[ "${VPC_ID}" = "None" ] && VPC_ID=""
sg_id() {  # sg_id <group-name>: prints the group ID in VPC_ID, or nothing
  [ -n "${VPC_ID}" ] || return 0
  local id
  id="$(aws ec2 describe-security-groups --filters Name=vpc-id,Values="${VPC_ID}" Name=group-name,Values="$1" \
    --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || true)"
  [ "${id}" = "None" ] || echo "${id}"
}
# delete_sg <group-id> <label>: waits up to 5 min for attached ENIs to go
# while RDS releases them, then deletes. Warns, never aborts.
delete_sg() {
  local sg="$1" label="$2" n
  [ -n "${sg}" ] || return 0
  for _ in $(seq 1 30); do
    n="$(aws ec2 describe-network-interfaces --filters Name=group-id,Values="${sg}" --query 'length(NetworkInterfaces)' --output text 2>/dev/null || echo 0)"
    [ "${n}" = 0 ] && break
    sleep 10
  done
  aws ec2 delete-security-group --group-id "${sg}" 2>/dev/null \
    && echo "deleted ${label} security group ${sg}" \
    || echo "WARN: ${label} security group ${sg} not deleted; retry once its ENIs are gone" >&2
}

kubectl --context "${CLUSTER_NAME}" delete namespace "${PREFIX}" --ignore-not-found --wait=true || true
helm uninstall external-secrets --kube-context "${CLUSTER_NAME}" -n external-secrets 2>/dev/null || true
kubectl --context "${CLUSTER_NAME}" delete nodepool "${PREFIX}-arm64" --ignore-not-found || true

for assoc in $(aws eks list-pod-identity-associations --cluster-name "${CLUSTER_NAME}" \
    --query "associations[?namespace=='${PREFIX}' || namespace=='external-secrets'].associationId" --output text); do
  aws eks delete-pod-identity-association --cluster-name "${CLUSTER_NAME}" --association-id "${assoc}" >/dev/null
done
for role in "${PREFIX}-pod" "${PREFIX}-eso"; do
  aws iam delete-role-policy --role-name "${role}" --policy-name "${role}" 2>/dev/null || true
  aws iam delete-role --role-name "${role}" 2>/dev/null || true
done

# Rerun-safe: a cluster already in "deleting" rejects modify/delete with
# InvalidDBClusterStateFault, which would abort before DynamoDB and ECR.
AURORA_STATUS="$(aws rds describe-db-clusters --db-cluster-identifier "${PREFIX}-aurora" \
  --query 'DBClusters[0].Status' --output text 2>/dev/null || echo absent)"
if [ "${AURORA_STATUS}" = deleting ]; then
  echo "Aurora ${PREFIX}-aurora is already deleting; skipping"
elif [ "${AURORA_STATUS}" != absent ]; then
  aws rds modify-db-cluster --db-cluster-identifier "${PREFIX}-aurora" --no-deletion-protection --apply-immediately >/dev/null
  aws rds delete-db-instance --db-instance-identifier "${PREFIX}-aurora-1" >/dev/null 2>&1 || true
  aws rds wait db-instance-deleted --db-instance-identifier "${PREFIX}-aurora-1" || true
  if [ "${FINAL_SNAPSHOT:-0}" = 1 ]; then
    aws rds delete-db-cluster --db-cluster-identifier "${PREFIX}-aurora" \
      --final-db-snapshot-identifier "${PREFIX}-aurora-final-$(date +%Y%m%d%H%M)" >/dev/null
  else
    aws rds delete-db-cluster --db-cluster-identifier "${PREFIX}-aurora" --skip-final-snapshot >/dev/null
  fi
  echo "Aurora deletion started"
fi
if [ "${DELETE_CLUSTER:-0}" != 1 ] && [ "${AURORA_STATUS}" != absent ]; then
  echo "After Aurora finishes deleting, remove its subnet group and security group:"
  echo "  aws rds wait db-cluster-deleted --db-cluster-identifier ${PREFIX}-aurora"
  echo "  aws rds delete-db-subnet-group --db-subnet-group-name ${PREFIX}-db"
  echo "  aws ec2 delete-security-group --group-id $(sg_id "${PREFIX}-db")"
fi

for t in users orders; do aws dynamodb delete-table --table-name "${PREFIX}-${t}" >/dev/null 2>&1 || true; done
aws ecr delete-repository --repository-name "${PREFIX}" --force >/dev/null 2>&1 || true

if [ "${DELETE_CLUSTER:-0}" = 1 ]; then
  # Everything below lives in the eksctl-managed VPC. Left behind, it makes the
  # VPC stack deletion fail with DependencyViolation.
  if [ "${AURORA_STATUS}" != absent ]; then
    echo "waiting for Aurora deletion (can take 10+ minutes)"
    aws rds wait db-cluster-deleted --db-cluster-identifier "${PREFIX}-aurora" \
      || echo "WARN: Aurora still present; the VPC stack may fail to delete" >&2
  fi
  aws rds delete-db-subnet-group --db-subnet-group-name "${PREFIX}-db" 2>/dev/null || true
  delete_sg "$(sg_id "${PREFIX}-db")" DB
  eksctl delete cluster --name "${CLUSTER_NAME}" --region "${AWS_REGION}" --wait
fi
echo "done"
