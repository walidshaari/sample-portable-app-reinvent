# Deployment paths

The clean architecture application runs on EKS Auto Mode in a Region and kind on a laptop. The build creates one arm64 image, scans it, copies the same digest to Amazon ECR and the local registry, and checks the image started by each cluster. The clusters have separate configuration, identities, and stores. This proves code placement; it does not prove failover or data residency.

Run the commands below from the repository root.

The `region/up.sh` and `local/up.sh` scripts write ignored `.generated/`
overlay inputs before applying Kustomize. A direct overlay render from a fresh
checkout needs those inputs first.

| Step | Command | Result |
|---|---|---|
| Build and scan | `codetalk_deployment/image/build-image.sh` | OCI layout, SBOM, scan report, and `.image_digest`; the build stops on unapproved High or Critical findings |
| Push to the Region | `AWS_REGION=us-east-1 codetalk_deployment/image/push-image.sh ecr` | The image digest in private ECR matches the build |
| Prepare EKS | `AWS_REGION=us-east-1 codetalk_deployment/eks/create-cluster.sh` | EKS Auto Mode cluster, when one does not already exist |
| Deploy in the Region | `AWS_REGION=us-east-1 ALLOWED_CIDR=a.b.c.d/32 codetalk_deployment/region/up.sh` | EKS application, DynamoDB tables, Aurora PostgreSQL, workload identity, and a restricted load balancer |
| Deploy on the laptop | `codetalk_deployment/local/up.sh` | kind, local registry, CloudNativePG PostgreSQL, and DynamoDB Local for development |
| Check both targets | `bash demo/09-two-targets.sh` | Runtime labels, store readiness, core fingerprints, and running pod image digests |

The Region overlay initially selects DynamoDB; the kind overlay selects PostgreSQL. `demo/03-config-axis.sh` shows a configuration change between available stores. `demo/04-tech-axis.sh` replays the adapter change required when the data model changes. Use a separate worktree for those state-changing demos.

## Endpoints and limits

`GET /api/health` reports the runtime, selected repository, and core fingerprint. `GET /api/ready` checks the selected store. Users and orders use `/api/users` and `/api/orders`; malformed entity input returns HTTP 400. The same code-level rules apply to every runtime.

Treat the local kind setup as an on-premises stand-in. Recovery requires a data and traffic plan with measured RTO and RPO. Residency requires controls and evidence for stored data, backups, logs, access, and transfers.
