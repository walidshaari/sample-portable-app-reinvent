#!/bin/bash

# Configuration variables
AWS_REGION="${AWS_REGION:?set AWS_REGION}"
KUBERNETES_VERSION="${KUBERNETES_VERSION:-1.36}"
CLUSTER_NAME="${CLUSTER_NAME:-portable-app-eks}"

# Get AWS account number dynamically
AWS_ACCOUNT_NUMBER=$(aws sts get-caller-identity --query 'Account' --output text 2>/dev/null)
if [ -z "$AWS_ACCOUNT_NUMBER" ]; then
    echo "❌ Unable to get AWS account number. Please check your AWS credentials."
    exit 1
fi

echo "🚀 Creating EKS Auto Mode Cluster with eksctl..."
echo "📍 Account: ${AWS_ACCOUNT_NUMBER}"
echo "📍 Region: ${AWS_REGION}"
echo "📍 Cluster: ${CLUSTER_NAME}"
echo "📍 Kubernetes Version: ${KUBERNETES_VERSION}"
echo "📍 Mode: Auto Mode (serverless compute)"
echo ""

# Check if eksctl is installed
if ! command -v eksctl &> /dev/null; then
    echo "❌ eksctl is not installed. Please install it first."
    echo "💡 Install with: brew install weaveworks/tap/eksctl"
    exit 1
fi

# Check if cluster already exists
echo "📦 Checking if EKS cluster exists..."
CLUSTER_STATUS=$(aws eks describe-cluster --name ${CLUSTER_NAME} --region ${AWS_REGION} --query 'cluster.status' --output text 2>/dev/null)

if [ "$CLUSTER_STATUS" == "ACTIVE" ]; then
    echo "✅ Cluster ${CLUSTER_NAME} already exists and is active"
    exit 0
elif [ "$CLUSTER_STATUS" == "CREATING" ]; then
    echo "⏳ Cluster ${CLUSTER_NAME} is already being created."
    echo "💡 Check progress with: eksctl get cluster --name ${CLUSTER_NAME} --region ${AWS_REGION}"
    exit 0
fi

# Create EKS Auto Mode cluster with eksctl
echo "🆕 Creating EKS Auto Mode cluster..."
echo "⚠️  This will take approximately 15-20 minutes. Please be patient..."
echo "💡 Auto Mode will automatically provision compute resources as needed"

eksctl create cluster \
    --name ${CLUSTER_NAME} \
    --region ${AWS_REGION} \
    --version ${KUBERNETES_VERSION} \
    --enable-auto-mode

if [ $? -ne 0 ]; then
    echo "❌ Failed to create EKS Auto Mode cluster"
    exit 1
fi

echo ""
echo "✅ EKS Auto Mode cluster created successfully!"
echo "📍 Cluster Name: ${CLUSTER_NAME}"
echo "📍 Region: ${AWS_REGION}"
echo "📍 Kubernetes Version: ${KUBERNETES_VERSION}"
echo "📍 Mode: Auto Mode (serverless compute)"
echo ""
echo "🔧 Cluster is ready! You can now:"
echo "1. Check cluster info: eksctl get cluster --name ${CLUSTER_NAME} --region ${AWS_REGION}"
echo "2. Deploy the application using: codetalk_deployment/region/up.sh"
echo "3. Auto Mode will provision nodes automatically when pods are scheduled"
echo ""
echo "💡 Useful commands:"
echo "   kubectl get nodes -o wide (nodes will appear when pods are scheduled)"
echo "   kubectl get pods -A"
echo "   aws eks describe-cluster --name ${CLUSTER_NAME} --region ${AWS_REGION}"
