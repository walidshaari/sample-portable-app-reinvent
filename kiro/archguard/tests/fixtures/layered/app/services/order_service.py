import boto3

from app.models.order import Order

_table = None


def place_order(order: Order) -> None:
    table = boto3.resource("dynamodb").Table("orders")
    table.put_item(Item={"id": order.id, "total": str(order.total)})
