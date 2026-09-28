#!/usr/bin/env bash
# In-Region target: Amazon EKS Auto Mode + DynamoDB + Aurora PostgreSQL.
#
# Idempotent. Creates what is missing, then deploys the overlay
# codetalk_deployment/k8s/overlays/region with the image digest produced by
# codetalk_deployment/image/build-image.sh and pushed with push-image.sh ecr.
#
# Required:
#   AWS_REGION      target Region
#   ALLOWED_CIDR    client CIDR allowed to reach the load balancer, for
#                   example the presenter's egress IP as a.b.c.d/32
# Optional:
#   CLUSTER_NAME    default portable-app-eks (created by eks/create-cluster.sh)
#   SKIP_AURORA=1   do not create Aurora (DynamoDB only)
#   WAIT_AURORA=1   block until Aurora is available (default 1)
#
# Creates (all tagged project=portable-app):
#   DynamoDB tables portable-app-users, portable-app-orders (on-demand, PITR)
#   IAM roles portable-app-pod (tables) and portable-app-eso (one secret),
#     each bound to one ServiceAccount through EKS Pod Identity
#   Aurora PostgreSQL 18.6 Serverless v2 cluster portable-app-aurora in the
#     cluster's private subnets, encrypted, master password managed in
#     Secrets Manager, reachable only from the cluster security group,
#     deletion protection on
#   External Secrets Operator (Helm, pinned) and the arm64 node pool
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${HERE}/../.." && pwd)"
OVERLAY="${REPO_ROOT}/codetalk_deployment/k8s/overlays/region"
GEN="${OVERLAY}/.generated"
STATE="${REPO_ROOT}/codetalk_deployment/.region_state"

AWS_REGION="${AWS_REGION:?set AWS_REGION}"
ALLOWED_CIDR="${ALLOWED_CIDR:?set ALLOWED_CIDR, for example 203.0.113.10/32}"
CLUSTER_NAME="${CLUSTER_NAME:-portable-app-eks}"
PREFIX="portable-app"
NAMESPACE="portable-app"
AURORA_ID="${PREFIX}-aurora"
AURORA_VERSION="${AURORA_VERSION:-18.6}"
ESO_CHART_VERSION="${ESO_CHART_VERSION:-2.11.0}"
TAGS=(Key=project,Value=${PREFIX})
export AWS_REGION AWS_PAGER=""

case "${ALLOWED_CIDR}" in 0.0.0.0/0|::/0)
  echo "refusing ALLOWED_CIDR=${ALLOWED_CIDR}: restrict the load balancer to known clients" >&2; exit 1;; esac

# shellcheck disable=SC1091
source "${REPO_ROOT}/codetalk_deployment/.image_digest"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
IMAGE_REPO="${ACCOUNT}.dkr.ecr.${AWS_REGION}.amazonaws.com/${PREFIX}"
aws ecr describe-images --repository-name "${PREFIX}" --image-ids imageDigest="${IMAGE_DIGEST}" >/dev/null \
  || { echo "image ${IMAGE_DIGEST} not in ECR; run image/push-image.sh ecr" >&2; exit 1; }

say() { printf '\n==> %s\n' "$*"; }

say "cluster ${CLUSTER_NAME}"
aws eks update-kubeconfig --name "${CLUSTER_NAME}" --alias "${CLUSTER_NAME}" >/dev/null
KCTX=(--context "${CLUSTER_NAME}")
VPC_ID="$(aws eks describe-cluster --name "${CLUSTER_NAME}" --query cluster.resourcesVpcConfig.vpcId --output text)"
CLUSTER_SG="$(aws eks describe-cluster --name "${CLUSTER_NAME}" --query cluster.resourcesVpcConfig.clusterSecurityGroupId --output text)"
PRIVATE_SUBNETS="$(aws ec2 describe-subnets --filters Name=vpc-id,Values="${VPC_ID}" Name=tag:kubernetes.io/role/internal-elb,Values=1 --query 'Subnets[].SubnetId' --output text)"

say "DynamoDB tables"
TABLE_PREFIX="${PREFIX}" AWS_REGION="${AWS_REGION}" "${REPO_ROOT}/codetalk_deployment/dynamodb/create-tables.sh" >/dev/null
for t in users orders; do
  aws dynamodb update-continuous-backups --table-name "${PREFIX}-${t}" \
    --point-in-time-recovery-specification PointInTimeRecoveryEnabled=true >/dev/null
done
USERS_ARN="$(aws dynamodb describe-table --table-name ${PREFIX}-users --query Table.TableArn --output text)"
ORDERS_ARN="$(aws dynamodb describe-table --table-name ${PREFIX}-orders --query Table.TableArn --output text)"

pod_identity_role() {  # role-name namespace serviceaccount policy-json
  local role="$1" ns="$2" sa="$3" policy="$4"
  if ! aws iam get-role --role-name "${role}" >/dev/null 2>&1; then
    aws iam create-role --role-name "${role}" --tags "${TAGS[@]}" \
      --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"pods.eks.amazonaws.com"},"Action":["sts:AssumeRole","sts:TagSession"]}]}' >/dev/null
  fi
  aws iam put-role-policy --role-name "${role}" --policy-name "${role}" --policy-document "${policy}"
  local arn; arn="$(aws iam get-role --role-name "${role}" --query Role.Arn --output text)"
  if [ "$(aws eks list-pod-identity-associations --cluster-name "${CLUSTER_NAME}" --namespace "${ns}" --service-account "${sa}" --query 'length(associations)' --output text)" = "0" ]; then
    aws eks create-pod-identity-association --cluster-name "${CLUSTER_NAME}" --namespace "${ns}" \
      --service-account "${sa}" --role-arn "${arn}" --tags project=${PREFIX} >/dev/null
  fi
  echo "${role} -> ${ns}/${sa}"
}

say "Pod Identity: app role (two tables, four actions)"
# The four data actions plus DescribeTable for the /api/ready probe are
# scoped to the two table ARNs.
pod_identity_role "${PREFIX}-pod" "${NAMESPACE}" "${PREFIX}" \
  "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":[\"dynamodb:GetItem\",\"dynamodb:PutItem\",\"dynamodb:DeleteItem\",\"dynamodb:Scan\",\"dynamodb:DescribeTable\"],\"Resource\":[\"${USERS_ARN}\",\"${ORDERS_ARN}\"]}]}"

AURORA_ENDPOINT=""
SECRET_ARN=""
if [ "${SKIP_AURORA:-0}" != "1" ]; then
  say "Aurora PostgreSQL ${AURORA_VERSION} Serverless v2"
  if ! aws rds describe-db-subnet-groups --db-subnet-group-name "${PREFIX}-db" >/dev/null 2>&1; then
    # shellcheck disable=SC2086
    aws rds create-db-subnet-group --db-subnet-group-name "${PREFIX}-db" \
      --db-subnet-group-description "portable-app private subnets" \
      --subnet-ids ${PRIVATE_SUBNETS} --tags "${TAGS[@]}" >/dev/null
  fi
  DB_SG="$(aws ec2 describe-security-groups --filters Name=vpc-id,Values="${VPC_ID}" Name=group-name,Values="${PREFIX}-db" --query 'SecurityGroups[0].GroupId' --output text)"
  if [ "${DB_SG}" = "None" ]; then
    DB_SG="$(aws ec2 create-security-group --vpc-id "${VPC_ID}" --group-name "${PREFIX}-db" \
      --description "PostgreSQL from the EKS cluster only" \
      --tag-specifications "ResourceType=security-group,Tags=[{Key=project,Value=${PREFIX}}]" --query GroupId --output text)"
    aws ec2 authorize-security-group-ingress --group-id "${DB_SG}" --protocol tcp --port 5432 \
      --source-group "${CLUSTER_SG}" >/dev/null
  fi
  if ! aws rds describe-db-clusters --db-cluster-identifier "${AURORA_ID}" >/dev/null 2>&1; then
    aws rds create-db-cluster --db-cluster-identifier "${AURORA_ID}" \
      --engine aurora-postgresql --engine-version "${AURORA_VERSION}" \
      --database-name app --master-username app_admin --manage-master-user-password \
      --db-subnet-group-name "${PREFIX}-db" --vpc-security-group-ids "${DB_SG}" \
      --storage-encrypted --deletion-protection --backup-retention-period 7 \
      --serverless-v2-scaling-configuration MinCapacity=0.5,MaxCapacity=4 \
      --copy-tags-to-snapshot --tags "${TAGS[@]}" >/dev/null
    aws rds create-db-instance --db-instance-identifier "${AURORA_ID}-1" \
      --db-cluster-identifier "${AURORA_ID}" --engine aurora-postgresql \
      --db-instance-class db.serverless --no-publicly-accessible --tags "${TAGS[@]}" >/dev/null
    echo "creating ${AURORA_ID} (about 10 minutes)"
  fi
  if [ "${WAIT_AURORA:-1}" = "1" ]; then
    aws rds wait db-instance-available --db-instance-identifier "${AURORA_ID}-1"
  fi
  AURORA_ENDPOINT="$(aws rds describe-db-clusters --db-cluster-identifier "${AURORA_ID}" --query 'DBClusters[0].Endpoint' --output text)"
  SECRET_ARN="$(aws rds describe-db-clusters --db-cluster-identifier "${AURORA_ID}" --query 'DBClusters[0].MasterUserSecret.SecretArn' --output text)"

  say "Pod Identity: External Secrets role (one secret, read only)"
  pod_identity_role "${PREFIX}-eso" external-secrets external-secrets \
    "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":[\"secretsmanager:GetSecretValue\",\"secretsmanager:DescribeSecret\"],\"Resource\":\"${SECRET_ARN}\"}]}"

  say "External Secrets Operator ${ESO_CHART_VERSION}"
  helm repo add external-secrets https://charts.external-secrets.io >/dev/null 2>&1 || true
  helm upgrade --install external-secrets external-secrets/external-secrets \
    --kube-context "${CLUSTER_NAME}" --namespace external-secrets --create-namespace \
    --version "${ESO_CHART_VERSION}" --set installCRDs=true --wait >/dev/null
fi

say "arm64 node pool"
kubectl "${KCTX[@]}" apply -f "${HERE}/nodepool-arm64.yaml"

say "generated overlay inputs (git-ignored)"
mkdir -p "${GEN}"
cat > "${GEN}/region.env" <<EOF
DYNAMODB_REGION=${AWS_REGION}
POSTGRES_HOST=${AURORA_ENDPOINT}
POSTGRES_DB=app
APP_IMAGE=${PREFIX}@${IMAGE_DIGEST}
EOF
{
  echo "apiVersion: kustomize.config.k8s.io/v1alpha1"
  echo "kind: Component"
  echo "images:"
  echo "  - name: portable-app"
  echo "    newName: ${IMAGE_REPO}"
  echo "    digest: ${IMAGE_DIGEST}"
  if [ -n "${SECRET_ARN}" ]; then
    echo "resources:"
    echo "  - external-secret.yaml"
  fi
  echo "patches:"
  echo "  - patch: |-"
  echo "      apiVersion: v1"
  echo "      kind: Service"
  echo "      metadata:"
  echo "        name: portable-app"
  echo "      spec:"
  echo "        loadBalancerSourceRanges: [\"${ALLOWED_CIDR}\"]"
} > "${GEN}/kustomization.yaml"
if [ -n "${SECRET_ARN}" ]; then
  cat > "${GEN}/external-secret.yaml" <<EOF
apiVersion: external-secrets.io/v1
kind: SecretStore
metadata:
  name: aws-secrets-manager
spec:
  provider:
    aws:
      service: SecretsManager
      region: ${AWS_REGION}
---
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: app-db
spec:
  refreshInterval: 15m
  secretStoreRef:
    name: aws-secrets-manager
    kind: SecretStore
  target:
    name: app-db
    template:
      data:
        POSTGRES_USER: "{{ .username }}"
        POSTGRES_PASSWORD: "{{ .password }}"
  dataFrom:
    - extract:
        key: ${SECRET_ARN}
EOF
fi

say "deploy overlay"
kubectl "${KCTX[@]}" -n "${NAMESPACE}" delete job portable-app-migrate --ignore-not-found >/dev/null 2>&1 || true
kubectl "${KCTX[@]}" apply -k "${OVERLAY}"
kubectl "${KCTX[@]}" -n "${NAMESPACE}" rollout status deployment/portable-app --timeout=600s
kubectl "${KCTX[@]}" -n "${NAMESPACE}" wait --for=condition=complete job/portable-app-migrate --timeout=300s

say "load balancer"
for _ in $(seq 1 60); do
  HOSTNAME="$(kubectl "${KCTX[@]}" -n "${NAMESPACE}" get svc portable-app -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')"
  [ -n "${HOSTNAME}" ] && break; sleep 5
done
cat > "${STATE}" <<EOF
export EKS_CONTEXT=${CLUSTER_NAME}
export EKS_URL=http://${HOSTNAME}
export AURORA_ID=${AURORA_ID}
EOF
echo "EKS_URL=http://${HOSTNAME}  (state in ${STATE})"
echo "NLB DNS can take a few minutes to resolve after creation."
