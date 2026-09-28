"""
DynamoDB implementation of the UserRepository port.

This adapter lives in the infrastructure layer. The domain (User) and the
application layer (UserRepository port, use cases) do not know it exists.
Swapping it for the in-memory repository is a composition-root decision,
not a code change in the core.

Table contract (provisioned by codetalk_deployment/dynamodb/create-tables.sh):
  - partition key: id (String)
  - item shape:    {"id": str, "name": str, "email": str}
"""
import asyncio
from typing import Any, List, Optional

import boto3

from application.ports.user_repository import UserRepository
from domain.user import User


class DynamoDBUserRepository(UserRepository):
    """DynamoDB-backed UserRepository.

    boto3 is synchronous. Each call is pushed to a worker thread with
    asyncio.to_thread so the async port contract is honoured without
    blocking the event loop.
    """

    def __init__(self, table_name: str, dynamodb_resource: Any = None):
        if not table_name:
            raise ValueError("DynamoDBUserRepository requires a table name")
        resource = dynamodb_resource or boto3.resource("dynamodb")
        self._table = resource.Table(table_name)

    async def create(self, user: User) -> None:
        """Create (or overwrite) a user item"""
        await asyncio.to_thread(self._table.put_item, Item=user.to_dict())

    async def find_by_id(self, id: str) -> Optional[User]:
        """Find user by ID"""
        response = await asyncio.to_thread(self._table.get_item, Key={"id": id})
        item = response.get("Item")
        return self._to_entity(item) if item else None

    async def find_all(self) -> List[User]:
        """Get all users.

        Uses a paginated Scan, which is fine for a demo table with a handful
        of items. A production adapter would model access patterns with a
        query instead of a table scan.
        """
        items = await asyncio.to_thread(self._scan_all)
        return [self._to_entity(item) for item in items]

    async def delete(self, id: str) -> None:
        """Delete user by ID. Mirrors the in-memory adapter: missing -> ValueError."""
        if await self.find_by_id(id) is None:
            raise ValueError("User not found")
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
    def _to_entity(item: dict) -> User:
        return User(id=item["id"], name=item["name"], email=item["email"])
