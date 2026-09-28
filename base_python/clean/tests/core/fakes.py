"""
Test doubles that implement the ports.

Defined here, next to the tests, on purpose: the core tests do not import
anything from infrastructure/, not even the in-memory adapter.
"""
from typing import Dict, List, Optional

from application.ports.order_repository import OrderRepository
from application.ports.user_repository import UserRepository
from domain.order import Order
from domain.user import User


class FakeUserRepository(UserRepository):
    def __init__(self):
        self.saved: Dict[str, User] = {}
        self.deleted: List[str] = []

    async def create(self, user: User) -> None:
        self.saved[user.id] = user

    async def find_by_id(self, id: str) -> Optional[User]:
        return self.saved.get(id)

    async def find_all(self) -> List[User]:
        return list(self.saved.values())

    async def delete(self, id: str) -> None:
        self.deleted.append(id)
        self.saved.pop(id, None)


class FakeOrderRepository(OrderRepository):
    def __init__(self):
        self.saved: Dict[str, Order] = {}
        self.deleted: List[str] = []

    async def create(self, order: Order) -> None:
        self.saved[order.id] = order

    async def find_by_id(self, id: str) -> Optional[Order]:
        return self.saved.get(id)

    async def find_all(self) -> List[Order]:
        return list(self.saved.values())

    async def delete(self, id: str) -> None:
        self.deleted.append(id)
        self.saved.pop(id, None)
