"""
Fixtures for adapter, composition and HTTP tests.

DynamoDB runs against moto, offline, with fake credentials and a fixed region
so boto3 never reaches for a real profile.

PostgreSQL runs against a real server. Point TEST_POSTGRES_DSN at one (the
Makefile target `pg-up` starts a pinned container on localhost). Without it
the PostgreSQL cases are skipped with a reason, unless REQUIRE_POSTGRES=1, in
which case they fail. Evidence runs set REQUIRE_POSTGRES=1.
"""
import os

import boto3
import pytest
from moto import mock_aws

USERS_TABLE = "test-users"
ORDERS_TABLE = "test-orders"

POSTGRES_DSN = os.environ.get("TEST_POSTGRES_DSN", "").strip()
REQUIRE_POSTGRES = os.environ.get("REQUIRE_POSTGRES", "") == "1"


@pytest.fixture(autouse=True)
def _fake_aws_environment(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")


def _create_table(resource, name: str):
    table = resource.create_table(
        TableName=name,
        KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    table.wait_until_exists()
    return table


@pytest.fixture
def dynamodb_tables():
    """Yield a mocked DynamoDB resource with the two demo tables created"""
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name=os.environ["AWS_DEFAULT_REGION"])
        _create_table(resource, USERS_TABLE)
        _create_table(resource, ORDERS_TABLE)
        yield resource


@pytest.fixture
def postgres_options():
    """Backend options for a clean PostgreSQL schema, or skip/fail"""
    if not POSTGRES_DSN:
        if REQUIRE_POSTGRES:
            pytest.fail("REQUIRE_POSTGRES=1 but TEST_POSTGRES_DSN is not set")
        pytest.skip("TEST_POSTGRES_DSN not set (run `make pg-up` to start PostgreSQL)")
    import psycopg

    with psycopg.connect(POSTGRES_DSN, autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS users; DROP TABLE IF EXISTS orders;")
    options = {"POSTGRES_DSN": POSTGRES_DSN, "POSTGRES_POOL_MAX": "3"}
    yield options


@pytest.fixture
def postgres_bundle(postgres_options):
    from infrastructure.repositories.postgres_repositories import build

    bundle = build(postgres_options)
    bundle.migrate()
    yield bundle
    bundle.close()
