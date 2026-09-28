"""In-memory backend: the default, per-process, used by tests and local runs."""
from typing import Mapping

from infrastructure.repositories.bundle import RepositoryBundle
from infrastructure.repositories.in_memory_order_repository import (
    InMemoryOrderRepository,
)
from infrastructure.repositories.in_memory_user_repository import (
    InMemoryUserRepository,
)


def build(options: Mapping[str, str]) -> RepositoryBundle:
    return RepositoryBundle(
        users=InMemoryUserRepository(),
        orders=InMemoryOrderRepository(),
        describe={"store": "process memory (not shared, not durable)"},
    )
