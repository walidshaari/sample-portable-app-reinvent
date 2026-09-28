"""
DynamoDB implementation of the OrderRepository port.

Table contract (provisioned by codetalk_deployment/dynamodb/create-tables.sh):
  - partition key: id (String)
  - item shape:    {"id": str, "user_id": str, "product": str,
                    "quantity": Number, "status": str}

DynamoDB returns numbers as decimal.Decimal. The adapter converts quantity
back to int before rebuilding the Order entity so the domain never sees a
persistence-specific type.
"""
import asyncio
from typing import Any, List, Optional

import boto3

from application.ports.order_repository import OrderRepository
from domain.order import Order


class DynamoDBOrderRepository(OrderRepository):
    """DynamoDB-backed OrderRepository"""

    def __init__(self, table_name: str, dynamodb_resource: Any = None):
        if not table_name:
            raise ValueError("DynamoDBOrderRepository requires a table name")
        resource = dynamodb_resource or boto3.resource("dynamodb")
        self._table = resource.Table(table_name)

    async def create(self, order: Order) -> None:
        """Create (or overwrite) an order item"""
        await asyncio.to_thread(self._table.put_item, Item=order.to_dict())

    async def find_by_id(self, id: str) -> Optional[Order]:
        """Find order by ID"""
        response = await asyncio.to_thread(self._table.get_item, Key={"id": id})
        item = response.get("Item")
        return self._to_entity(item) if item else None

    async def find_all(self) -> List[Order]:
        """Get all orders (paginated Scan, demo-scale only)"""
        items = await asyncio.to_thread(self._scan_all)
        return [self._to_entity(item) for item in items]

    async def delete(self, id: str) -> None:
        """Delete order by ID. Mirrors the in-memory adapter: missing -> ValueError."""
        if await self.find_by_id(id) is None:
            raise ValueError("Order not found")
        await asyncio.to_thread(self._table.delete_item, Key={"id": id})

    def _scan_all(self) -> List[dict]:
        items: List[dict] = []
        kwargs: dict = {}
        while True:
            response = self._table.scan(**kwargs)
            items.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                return items
            kwargs["ExclusiveStartKey"] = last_key

    @staticmethod
    def _to_entity(item: dict) -> Order:
        return Order(
            id=item["id"],
            user_id=item["user_id"],
            product=item["product"],
            quantity=int(item["quantity"]),
            status=item.get("status", "pending"),
        )
