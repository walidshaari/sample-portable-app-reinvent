# Two ways to change a store

Automated, captioned evidence replay. No voiceover.

## 00s · What kind of change is this?

Status: CODE TOUR. Source: `docs/adr/0001-persistence-two-axes.md`.

Connection settings  /  data model

The distinction tells us where work belongs.

## 04s · The composition root chooses

Status: CURRENT SOURCE. Source: `base_python/clean/infrastructure/composition.py:39-43`.

BACKENDS: Dict[str, str] = {
    "memory": "infrastructure.repositories.in_memory_backend:build",
    "dynamodb": "infrastructure.repositories.dynamodb_backend:build",
    "postgres": "infrastructure.repositories.postgres_repositories:build",
}

A same-protocol placement change selects settings and credentials.

## 10s · A new model needs an adapter

Status: ARCHIVED · 26 SEP 2026. Source: `docs/evidence/captures/03c-config-adapter-hashes.txt + 04b-tech-after.txt`.

PostgreSQL adapter hash: 5cf3cad3d235a050
EKS = kind = checkout
shared repository contract: .....................................                                    [100%]

Captured patch replay checked adapter behavior; data migration was not part of it.

## 16s · Contain the difference

Status: PRESENTER LINE. Source: `base_python/clean/infrastructure/repositories/postgres_repositories.py`.

The core stays fixed; the adapter owns SQL behavior.

PostgreSQL upsert preserves create() semantics. Cutover still needs a plan.
