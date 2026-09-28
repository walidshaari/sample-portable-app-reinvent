"""
DynamoDB backend wiring.

The adapters (dynamodb_user_repository.py, dynamodb_order_repository.py) are
used unchanged for every DynamoDB-API endpoint. Only these options differ:

  DYNAMODB_USERS_TABLE    required
  DYNAMODB_ORDERS_TABLE   required
  DYNAMODB_ENDPOINT_URL   optional. Unset: the regional Amazon DynamoDB
                          endpoint. Set: another endpoint that speaks the
                          DynamoDB API, for example DynamoDB Local
                          (development and testing only).
  DYNAMODB_REGION         optional region name for the client.
  DYNAMODB_CREATE_TABLES  "true" lets the migration create missing tables.
                          Set it only for DynamoDB Local. In a Region the
                          tables are infrastructure, the runtime role has no
                          CreateTable permission, and the migration only
                          verifies that both tables exist.

Switching between those endpoints is the configuration axis for the
DynamoDB API: same adapter bytes, different values.
"""
from typing import Mapping

from infrastructure.repositories.bundle import (
    BackendConfigurationError,
    RepositoryBundle,
    require,
)


def build(options: Mapping[str, str]) -> RepositoryBundle:
    require(options, "DYNAMODB_USERS_TABLE", "DYNAMODB_ORDERS_TABLE", backend="dynamodb")

    # Imported here so the in-memory path never needs boto3.
    import asyncio

    import boto3
    from botocore.config import Config
    from botocore.exceptions import BotoCoreError, ClientError

    from infrastructure.repositories.dynamodb_order_repository import (
        DynamoDBOrderRepository,
    )
    from infrastructure.repositories.dynamodb_user_repository import (
        DynamoDBUserRepository,
    )

    users_table = options["DYNAMODB_USERS_TABLE"].strip()
    orders_table = options["DYNAMODB_ORDERS_TABLE"].strip()
    endpoint_url = options.get("DYNAMODB_ENDPOINT_URL", "").strip() or None
    region = options.get("DYNAMODB_REGION", "").strip() or None

    # Fail fast when the endpoint is unreachable, so the HTTP edge can answer
    # 503 in a couple of seconds instead of hanging on default retries.
    client_config = Config(
        connect_timeout=2, read_timeout=5, retries={"max_attempts": 2, "mode": "standard"}
    )
    resource = boto3.resource(
        "dynamodb", endpoint_url=endpoint_url, region_name=region, config=client_config
    )

    async def ping() -> None:
        await asyncio.to_thread(resource.meta.client.describe_table, TableName=users_table)

    create_tables = options.get("DYNAMODB_CREATE_TABLES", "").strip().lower() == "true"

    def _exists(name: str) -> bool:
        try:
            resource.meta.client.describe_table(TableName=name)
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ResourceNotFoundException":
                return False
            raise

    def migrate() -> None:
        """Verify both tables exist; create them only when allowed."""
        for name in (users_table, orders_table):
            if _exists(name):
                continue
            if not create_tables:
                raise BackendConfigurationError(
                    f"DynamoDB table {name!r} does not exist. Provision it with "
                    "infrastructure code, or set DYNAMODB_CREATE_TABLES=true for DynamoDB Local."
                )
            resource.create_table(
                TableName=name,
                KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
                AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
                BillingMode="PAY_PER_REQUEST",
            ).wait_until_exists()

    return RepositoryBundle(
        users=DynamoDBUserRepository(users_table, dynamodb_resource=resource),
        orders=DynamoDBOrderRepository(orders_table, dynamodb_resource=resource),
        unavailable_errors=(BotoCoreError, ClientError),
        ping=ping,
        migrate=migrate,
        describe={"store": "dynamodb-api", "endpoint": "custom" if endpoint_url else "regional"},
    )
