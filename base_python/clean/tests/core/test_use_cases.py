"""Use cases, tested against fakes of the ports. No adapter is involved."""
import pytest

from application.use_cases.create_order import CreateOrderUseCase
from application.use_cases.create_user import CreateUserUseCase
from application.use_cases.delete_order import DeleteOrderUseCase
from application.use_cases.delete_user import DeleteUserUseCase
from domain.order import Order
from domain.user import User
from tests.core.fakes import FakeOrderRepository, FakeUserRepository


async def test_create_user_generates_an_id_and_saves_through_the_port():
    repo = FakeUserRepository()
    user = await CreateUserUseCase(repo).execute({"name": "Grace Hopper", "email": "grace@example.com"})
    assert user.id
    assert repo.saved[user.id] is user


async def test_create_user_ignores_a_caller_supplied_id():
    repo = FakeUserRepository()
    user = await CreateUserUseCase(repo).execute({"id": "chosen", "name": "Grace", "email": "g@example.com"})
    assert user.id != "chosen"


async def test_invalid_user_is_never_saved():
    repo = FakeUserRepository()
    with pytest.raises(ValueError):
        await CreateUserUseCase(repo).execute({"name": "G", "email": "g@example.com"})
    assert repo.saved == {}


async def test_create_order_keeps_a_supplied_id_and_defaults_status():
    repo = FakeOrderRepository()
    order = await CreateOrderUseCase(repo).execute({"id": "o-1", "user_id": "u-1", "product": "Pen", "quantity": 2})
    assert order.id == "o-1"
    assert order.status == "pending"
    assert repo.saved["o-1"] is order


async def test_create_order_generates_an_id_when_absent():
    repo = FakeOrderRepository()
    order = await CreateOrderUseCase(repo).execute({"user_id": "u-1", "product": "Pen", "quantity": 1})
    assert order.id and order.id in repo.saved


async def test_delete_user_checks_existence_through_the_port():
    repo = FakeUserRepository()
    await repo.create(User("u-1", "Ada Lovelace", "ada@example.com"))
    await DeleteUserUseCase(repo).execute("u-1")
    assert repo.deleted == ["u-1"]
    with pytest.raises(ValueError, match="User not found"):
        await DeleteUserUseCase(repo).execute("u-1")


async def test_delete_order_of_missing_order_never_calls_delete():
    repo = FakeOrderRepository()
    with pytest.raises(ValueError, match="Order not found"):
        await DeleteOrderUseCase(repo).execute("missing")
    assert repo.deleted == []
    await repo.create(Order("o-1", "u-1", "Pen", 1))
    await DeleteOrderUseCase(repo).execute("o-1")
    assert repo.deleted == ["o-1"]
