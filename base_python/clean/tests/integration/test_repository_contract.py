"""
Port contract tests.

The same assertions run against every adapter: in-memory, DynamoDB (moto) and
PostgreSQL (a real server). If an adapter passes, it satisfies the port the
use cases depend on. This is the proof that swapping key-value DynamoDB for
relational PostgreSQL is an adapter decision and not a change to the core.
"""
import pytest

from application.ports.order_repository import OrderRepository
from application.ports.user_repository import UserRepository
from domain.order import Order
from domain.user import User
from infrastructure.repositories.dynamodb_order_repository import (
    DynamoDBOrderRepository,
)
from infrastructure.repositories.dynamodb_user_repository import (
    DynamoDBUserRepository,
)
from infrastructure.repositories.in_memory_order_repository import (
    InMemoryOrderRepository,
)
from infrastructure.repositories.in_memory_user_repository import (
    InMemoryUserRepository,
)
from tests.integration.conftest import ORDERS_TABLE, USERS_TABLE

ADAPTERS = ["memory", "dynamodb", "postgres"]


@pytest.fixture(params=ADAPTERS)
def repos(request):
    """(user_repo, order_repo) for each adapter family"""
    if request.param == "memory":
        return InMemoryUserRepository(), InMemoryOrderRepository()
    if request.param == "dynamodb":
        resource = request.getfixturevalue("dynamodb_tables")
        return (
            DynamoDBUserRepository(USERS_TABLE, dynamodb_resource=resource),
            DynamoDBOrderRepository(ORDERS_TABLE, dynamodb_resource=resource),
        )
    bundle = request.getfixturevalue("postgres_bundle")
    return bundle.users, bundle.orders


@pytest.fixture
def user_repo(repos) -> UserRepository:
    return repos[0]


@pytest.fixture
def order_repo(repos) -> OrderRepository:
    return repos[1]


# --- UserRepository contract -------------------------------------------------

def test_user_adapter_implements_the_port(user_repo):
    assert isinstance(user_repo, UserRepository)


async def test_user_create_then_find_by_id(user_repo):
    user = User(id="u-1", name="Ada Lovelace", email="ada@example.com")
    await user_repo.create(user)
    found = await user_repo.find_by_id("u-1")
    assert found is not None
    assert found.to_dict() == user.to_dict()


async def test_user_create_with_same_id_overwrites(user_repo):
    await user_repo.create(User(id="u-1", name="Ada Lovelace", email="ada@example.com"))
    await user_repo.create(User(id="u-1", name="Ada King", email="ada.king@example.com"))
    found = await user_repo.find_by_id("u-1")
    assert found.to_dict() == {"id": "u-1", "name": "Ada King", "email": "ada.king@example.com"}
    assert len(await user_repo.find_all()) == 1


async def test_user_find_by_id_missing_returns_none(user_repo):
    assert await user_repo.find_by_id("does-not-exist") is None


async def test_user_find_all_returns_every_user(user_repo):
    await user_repo.create(User(id="u-1", name="Ada Lovelace", email="ada@example.com"))
    await user_repo.create(User(id="u-2", name="Alan Turing", email="alan@example.com"))
    users = await user_repo.find_all()
    assert sorted(u.id for u in users) == ["u-1", "u-2"]


async def test_user_delete_removes_user(user_repo):
    await user_repo.create(User(id="u-1", name="Ada Lovelace", email="ada@example.com"))
    await user_repo.delete("u-1")
    assert await user_repo.find_by_id("u-1") is None
    assert await user_repo.find_all() == []


async def test_user_delete_missing_raises_value_error(user_repo):
    with pytest.raises(ValueError, match="User not found"):
        await user_repo.delete("does-not-exist")


# --- OrderRepository contract ------------------------------------------------

def test_order_adapter_implements_the_port(order_repo):
    assert isinstance(order_repo, OrderRepository)


async def test_order_round_trip_preserves_types(order_repo):
    order = Order(id="o-1", user_id="u-1", product="Notebook", quantity=3, status="completed")
    await order_repo.create(order)
    found = await order_repo.find_by_id("o-1")
    assert found is not None
    assert found.to_dict() == order.to_dict()
    # DynamoDB hands numbers back as Decimal; the adapter must restore int.
    assert type(found.quantity) is int


async def test_order_for_unknown_user_is_accepted(order_repo):
    """The domain allows it, so no adapter may add a foreign-key rule"""
    await order_repo.create(Order(id="o-9", user_id="nobody", product="Pen", quantity=1))
    assert (await order_repo.find_by_id("o-9")).user_id == "nobody"


async def test_order_find_all_and_delete(order_repo):
    await order_repo.create(Order(id="o-1", user_id="u-1", product="Notebook", quantity=1))
    await order_repo.create(Order(id="o-2", user_id="u-1", product="Pen", quantity=2))
    assert sorted(o.id for o in await order_repo.find_all()) == ["o-1", "o-2"]
    await order_repo.delete("o-1")
    assert [o.id for o in await order_repo.find_all()] == ["o-2"]


async def test_order_delete_missing_raises_value_error(order_repo):
    with pytest.raises(ValueError, match="Order not found"):
        await order_repo.delete("does-not-exist")


# --- Adapter construction guards ---------------------------------------------

def test_dynamodb_adapters_require_a_table_name():
    with pytest.raises(ValueError):
        DynamoDBUserRepository("")
    with pytest.raises(ValueError):
        DynamoDBOrderRepository("")
