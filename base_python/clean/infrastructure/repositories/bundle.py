"""
What a persistence backend hands to the composition root.

Every backend module exposes one function, ``build(options) -> RepositoryBundle``.
``options`` is a plain mapping of the backend's own settings, already read by
the composition root. Backends never read the environment themselves.

The bundle carries the two port implementations plus the operational hooks
the edges need: which exceptions mean "the store is unreachable" (so the HTTP
adapter can answer 503 instead of crashing), a readiness probe, an optional
schema migration, and a close function. None of this reaches the core.
"""
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Mapping, Optional, Tuple, Type

from application.ports.order_repository import OrderRepository
from application.ports.user_repository import UserRepository


class BackendConfigurationError(RuntimeError):
    """Raised when a backend is selected but its settings are incomplete"""


async def _always_ready() -> None:
    return None


def _noop() -> None:
    return None


@dataclass(frozen=True)
class RepositoryBundle:
    users: UserRepository
    orders: OrderRepository
    # Exceptions that mean the store is unavailable (network, timeouts, pool).
    unavailable_errors: Tuple[Type[BaseException], ...] = ()
    # Raises one of unavailable_errors if the store cannot be reached.
    ping: Callable[[], Awaitable[None]] = _always_ready
    # Idempotent schema or table creation. None when the store needs none.
    migrate: Optional[Callable[[], None]] = None
    close: Callable[[], None] = _noop
    # Free-form, non-secret facts for /api/health (for example the endpoint kind).
    describe: Mapping[str, str] = field(default_factory=dict)


def require(options: Mapping[str, str], *names: str, backend: str) -> None:
    """Fail fast with every missing setting named, before any network call"""
    missing = [name for name in names if not str(options.get(name, "")).strip()]
    if missing:
        raise BackendConfigurationError(
            f"REPOSITORY_BACKEND={backend} requires " + " and ".join(missing)
        )
