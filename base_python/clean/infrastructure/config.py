"""
Typed settings, built once by the composition root.

Why this is not a port in the application layer: no use case needs
configuration. Configuration only decides which adapters the composition
root builds, so it lives with the composition root in infrastructure.

Every target delivers configuration to the process the same way, as
environment variables. Only the delivery mechanism differs, and it lives in
deployment files, not in code:

  Lambda         function environment variables
  ECS            task definition "environment" and "secrets" (SSM Parameter
                 Store or Secrets Manager values injected by the agent)
  EKS            ConfigMap plus a Secret kept in sync from AWS Secrets Manager
                 by External Secrets Operator, credentials from Pod Identity
  kind / on-prem ConfigMap plus a Kubernetes Secret created in-cluster
  Laptop         shell environment

This module only parses a mapping it is given. The one read of os.environ is
in infrastructure/composition.py.
"""
from dataclasses import dataclass, field
from typing import Mapping, Tuple

DEFAULT_BACKEND = "memory"

# Environment prefixes that carry backend options. A backend receives every
# key that starts with one of these, so adding a backend never edits this file
# as long as its settings use its own prefix.
BACKEND_OPTION_PREFIXES: Tuple[str, ...] = ("DYNAMODB_", "POSTGRES_")


@dataclass(frozen=True)
class Settings:
    repository_backend: str = DEFAULT_BACKEND
    runtime: str = "unknown"
    host: str = "0.0.0.0"
    port: int = 9000
    # Non-secret identity facts shown by /api/health (pod, node, image tag).
    identity: Mapping[str, str] = field(default_factory=dict)
    # Backend-specific options, passed through to the selected backend.
    backend_options: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, environ: Mapping[str, str]) -> "Settings":
        def get(name: str, default: str = "") -> str:
            return str(environ.get(name, default)).strip()

        port_text = get("PORT", "9000") or "9000"
        try:
            port = int(port_text)
        except ValueError as exc:
            raise ValueError(f"PORT must be an integer, got {port_text!r}") from exc

        identity = {
            key: value
            for key, value in (
                ("pod", get("POD_NAME")),
                ("node", get("NODE_NAME")),
                ("namespace", get("POD_NAMESPACE")),
                ("image", get("APP_IMAGE")),
            )
            if value
        }
        backend_options = {
            key: str(value)
            for key, value in environ.items()
            if key.startswith(BACKEND_OPTION_PREFIXES)
        }
        return cls(
            repository_backend=(get("REPOSITORY_BACKEND", DEFAULT_BACKEND) or DEFAULT_BACKEND).lower(),
            runtime=get("APP_RUNTIME", "unknown") or "unknown",
            host=get("HOST", "0.0.0.0") or "0.0.0.0",
            port=port,
            identity=identity,
            backend_options=backend_options,
        )
