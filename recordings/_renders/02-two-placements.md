# One image, two placements

Automated, captioned evidence replay. No voiceover.

## 00s · Same core. Different placement.

Status: CODE TOUR. Source: `talk/arc401-run-of-show.md`.

EKS in the Region  /  kind on the laptop

The demo treats kind as an on-premises stand-in.

## 04s · The captured pods matched

Status: CAPTURED · 2026-09-28. Source: `docs/evidence/captures/09-two-targets-2026-09-28-rebuild.txt`.

EKS runtime: eks-auto-mode
kind runtime: kind-onprem
core fingerprint: 1fb824b53b38
pod image sha256: 185f46d5fb5e…

Both pods matched the locally built image; the core matched this checkout.

## 10s · Same image; separate stores

Status: ARCHITECTURE. Source: `codetalk_deployment/k8s/overlays`.

EKS to Region store
kind to local PostgreSQL

Identity, configuration, and data stay specific to each placement.

## 15s · Portability is the option

Status: CLAIM LIMIT. Source: `talk/arc401-stage-walkthrough.md`.

Recovery and residency need separate controls.

An image match does not measure RTO/RPO or prove permitted data flows.
