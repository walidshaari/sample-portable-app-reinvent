"""
Migration entrypoint: python -m infrastructure.migrate

Runs the selected backend's idempotent schema or table creation. Used by the
Kubernetes migration Job before the app rolls out. Configuration is read by
the composition root, like every other entrypoint.
"""
from infrastructure.composition import run_migrations

if __name__ == "__main__":
    print(run_migrations())
