"""Entity rules, tested with nothing but the entities."""
import pytest

from domain.order import Order
from domain.user import User


def test_valid_user_round_trips_to_dict():
    user = User("u-1", "Ada Lovelace", "ada@example.com")
    assert user.to_dict() == {"id": "u-1", "name": "Ada Lovelace", "email": "ada@example.com"}


@pytest.mark.parametrize(
    "args, message",
    [
        (("", "Ada", "ada@example.com"), "User ID is required"),
        (("u-1", "A", "ada@example.com"), "Name must be at least 2 characters"),
        (("u-1", "  ", "ada@example.com"), "Name must be at least 2 characters"),
        (("u-1", "Ada", "not-an-email"), "Invalid email format"),
        (("u-1", "Ada", "ada@example"), "Invalid email format"),
        (("u-1", 42, "ada@example.com"), "Name must be at least 2 characters"),
        (("u-1", "Ada", None), "Invalid email format"),
        (("u-1", "Ada", 42), "Invalid email format"),
    ],
)
def test_invalid_user_is_rejected(args, message):
    with pytest.raises(ValueError, match=message):
        User(*args)


def test_order_defaults_to_pending():
    order = Order("o-1", "u-1", "Notebook", 2)
    assert order.status == "pending"
    assert order.to_dict()["quantity"] == 2


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"id": ""}, "Order ID is required"),
        ({"user_id": ""}, "User ID is required"),
        ({"product": "x"}, "Product must be at least 2 characters"),
        ({"quantity": 0}, "Quantity must be greater than 0"),
        ({"quantity": -3}, "Quantity must be greater than 0"),
        ({"status": "shipped"}, "Status must be either"),
        ({"user_id": 42}, "User ID is required"),
        ({"product": 42}, "Product must be at least 2 characters"),
        ({"quantity": "2"}, "Quantity must be greater than 0"),
        ({"quantity": None}, "Quantity must be greater than 0"),
        ({"quantity": True}, "Quantity must be greater than 0"),
    ],
)
def test_invalid_order_is_rejected(kwargs, message):
    base = {"id": "o-1", "user_id": "u-1", "product": "Notebook", "quantity": 1, "status": "pending"}
    with pytest.raises(ValueError, match=message):
        Order(**{**base, **kwargs})


def test_completed_is_a_valid_status():
    assert Order("o-1", "u-1", "Notebook", 1, "completed").status == "completed"
