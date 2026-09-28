"""
Composition root.

This is the one place where configuration meets wiring. Every entrypoint
(local uvicorn, container CMD, Lambda handler, migration job) calls into this
module. It is the only module that reads os.environ, and the only module that
chooses concrete adapters. The domain and use cases are never touched.

Environment variables (all optional):
  REPOSITORY_BACKEND  a key of BACKENDS below. Defaults to "memory".
  APP_RUNTIME         free-text label returned by GET /api/health, for
                      example "eks", "kind", "lambda", "local". Defaults to
                      "unknown". It is a label only; nothing branches on it.
  HOST, PORT          bind address for the HTTP server (default 0.0.0.0:9000).
  POD_NAME, NODE_NAME, POD_NAMESPACE, APP_IMAGE
                      non-secret identity facts shown by /api/health.
  DYNAMODB_*, POSTGRES_*
                      backend options, passed to the selected backend.
                      Each backend module in infrastructure/repositories/
                      documents its own options.
"""
import hashlib
import importlib
import os
from pathlib import Path
from typing import Callable, Dict, Mapping, Optional, Tuple

from application.ports.order_repository import OrderRepository
from application.ports.user_repository import UserRepository
from infrastructure.config import Settings
from infrastructure.repositories.bundle import (
    BackendConfigurationError,
    RepositoryBundle,
)

# Backend registry: name -> "module:function". The function receives the
# backend options and returns a RepositoryBundle. Modules are imported only
# when selected, so the in-memory path needs no database driver.
BACKENDS: Dict[str, str] = {
    "memory": "infrastructure.repositories.in_memory_backend:build",
    "dynamodb": "infrastructure.repositories.dynamodb_backend:build",
    "postgres": "infrastructure.repositories.postgres_repositories:build",
}

MEMORY = "memory"
DYNAMODB = "dynamodb"

# Kept as the public name used by tests and entrypoints.
ConfigurationError = BackendConfigurationError

_CORE_DIRECTORIES = ("domain", "application")
_APP_ROOT = Path(__file__).resolve().parent.parent


def load_settings(environ: Optional[Mapping[str, str]] = None) -> Settings:
    """The single configuration read of the application"""
    return Settings.from_mapping(os.environ if environ is None else environ)


def repository_backend(settings: Optional[Settings] = None) -> str:
    """Return the configured backend name, validated against the registry"""
    backend = (settings or load_settings()).repository_backend
    if backend not in BACKENDS:
        raise ConfigurationError(
            f"REPOSITORY_BACKEND={backend!r} is not supported. "
            f"Use one of: {', '.join(BACKENDS)}"
        )
    return backend


def runtime_label(settings: Optional[Settings] = None) -> str:
    """Return the label that identifies which compute is serving the request"""
    return (settings or load_settings()).runtime


def _resolve(target: str) -> Callable[[Mapping[str, str]], RepositoryBundle]:
    module_name, function_name = target.split(":", 1)
    return getattr(importlib.import_module(module_name), function_name)


def build_bundle(settings: Optional[Settings] = None) -> RepositoryBundle:
    """Build the repository bundle selected by REPOSITORY_BACKEND"""
    settings = settings or load_settings()
    backend = repository_backend(settings)
    return _resolve(BACKENDS[backend])(settings.backend_options)


def build_repositories(settings: Optional[Settings] = None) -> Tuple[UserRepository, OrderRepository]:
    """Instantiate the repository adapters selected by REPOSITORY_BACKEND"""
    bundle = build_bundle(settings)
    return bundle.users, bundle.orders


def core_fingerprint(root: Path = _APP_ROOT) -> str:
    """sha256 over every .py file in domain/ and application/.

    Served by /api/health so each running target can prove it carries the
    same core bytes as the repository, without shelling into the container.
    """
    digest = hashlib.sha256()
    for directory in _CORE_DIRECTORIES:
        for path in sorted((root / directory).rglob("*.py")):
            relative = path.relative_to(root).as_posix()
            digest.update(relative.encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def compose_app(settings: Optional[Settings] = None):
    """Build the FastAPI app with adapters chosen from configuration"""
    # Local import keeps this module importable without FastAPI in tests that
    # only exercise backend selection.
    from infrastructure.http.fastapi_app import create_fastapi_app

    settings = settings or load_settings()
    bundle = build_bundle(settings)
    return create_fastapi_app(
        custom_user_repository=bundle.users,
        custom_order_repository=bundle.orders,
        runtime=settings.runtime,
        backend_name=settings.repository_backend,
        identity={**settings.identity, **bundle.describe, "core_sha256": core_fingerprint()},
        unavailable_errors=bundle.unavailable_errors,
        readiness_probe=bundle.ping,
    )


def server_bind(settings: Optional[Settings] = None) -> Tuple[str, int]:
    """Host and port for the local HTTP server"""
    settings = settings or load_settings()
    return settings.host, settings.port


def run_migrations(settings: Optional[Settings] = None) -> str:
    """Run the selected backend's idempotent migration, if it has one"""
    settings = settings or load_settings()
    bundle = build_bundle(settings)
    try:
        if bundle.migrate is None:
            return f"{settings.repository_backend}: nothing to migrate"
        bundle.migrate()
        return f"{settings.repository_backend}: migration applied"
    finally:
        bundle.close()
