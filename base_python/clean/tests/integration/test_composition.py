"""
Composition-root tests.

These prove that adapter selection is driven by configuration, that the
in-memory adapter remains the default, that misconfiguration fails fast with
a clear message, and that configuration is parsed in one place.
"""
import pytest

from infrastructure import composition
from infrastructure.config import Settings
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


def _dynamodb_env(monkeypatch, backend="dynamodb"):
    monkeypatch.setenv("REPOSITORY_BACKEND", backend)
    monkeypatch.setenv("DYNAMODB_USERS_TABLE", "test-users")
    monkeypatch.setenv("DYNAMODB_ORDERS_TABLE", "test-orders")


def test_default_backend_is_memory():
    users, orders = composition.build_repositories()
    assert isinstance(users, InMemoryUserRepository)
    assert isinstance(orders, InMemoryOrderRepository)
    assert composition.repository_backend() == "memory"


def test_dynamodb_backend_selected_from_environment(monkeypatch, dynamodb_tables):
    _dynamodb_env(monkeypatch)
    users, orders = composition.build_repositories()
    assert isinstance(users, DynamoDBUserRepository)
    assert isinstance(orders, DynamoDBOrderRepository)


def test_backend_value_is_case_insensitive(monkeypatch, dynamodb_tables):
    _dynamodb_env(monkeypatch, backend="DynamoDB")
    assert composition.repository_backend() == "dynamodb"


def test_dynamodb_backend_without_table_names_fails_fast(monkeypatch):
    monkeypatch.setenv("REPOSITORY_BACKEND", "dynamodb")
    with pytest.raises(composition.ConfigurationError, match="DYNAMODB_USERS_TABLE"):
        composition.build_repositories()


def test_postgres_backend_without_connection_settings_fails_fast(monkeypatch):
    monkeypatch.setenv("REPOSITORY_BACKEND", "postgres")
    with pytest.raises(composition.ConfigurationError, match="POSTGRES_DSN"):
        composition.build_repositories()


def test_unknown_backend_is_rejected(monkeypatch):
    monkeypatch.setenv("REPOSITORY_BACKEND", "cassandra")
    with pytest.raises(composition.ConfigurationError, match="not supported"):
        composition.build_repositories()


def test_runtime_label_defaults_to_unknown(monkeypatch):
    assert composition.runtime_label() == "unknown"
    monkeypatch.setenv("APP_RUNTIME", "eks")
    assert composition.runtime_label() == "eks"


def test_dynamodb_endpoint_override_is_honoured(monkeypatch, dynamodb_tables):
    _dynamodb_env(monkeypatch)
    monkeypatch.setenv("DYNAMODB_ENDPOINT_URL", "http://localhost:8000")
    users, _ = composition.build_repositories()
    assert users._table.meta.client.meta.endpoint_url == "http://localhost:8000"


def test_settings_are_parsed_from_one_mapping():
    settings = Settings.from_mapping(
        {
            "REPOSITORY_BACKEND": "Postgres",
            "APP_RUNTIME": "kind",
            "PORT": "8080",
            "POD_NAME": "app-1",
            "POSTGRES_DSN": "postgresql://example",
            "DYNAMODB_USERS_TABLE": "u",
            "UNRELATED": "ignored",
        }
    )
    assert settings.repository_backend == "postgres"
    assert settings.runtime == "kind"
    assert settings.port == 8080
    assert settings.identity == {"pod": "app-1"}
    assert settings.backend_options == {
        "POSTGRES_DSN": "postgresql://example",
        "DYNAMODB_USERS_TABLE": "u",
    }


def test_server_bind_comes_from_the_composition_root(monkeypatch):
    monkeypatch.setenv("PORT", "9100")
    assert composition.server_bind() == ("0.0.0.0", 9100)


def test_invalid_port_is_a_clear_error(monkeypatch):
    monkeypatch.setenv("PORT", "nine")
    with pytest.raises(ValueError, match="PORT must be an integer"):
        composition.load_settings()


def test_core_fingerprint_is_stable_and_covers_only_the_core(tmp_path):
    (tmp_path / "domain").mkdir()
    (tmp_path / "application").mkdir()
    (tmp_path / "infrastructure").mkdir()
    (tmp_path / "domain" / "a.py").write_text("x = 1\n")
    first = composition.core_fingerprint(tmp_path)
    (tmp_path / "infrastructure" / "b.py").write_text("y = 2\n")
    assert composition.core_fingerprint(tmp_path) == first
    (tmp_path / "application" / "c.py").write_text("z = 3\n")
    assert composition.core_fingerprint(tmp_path) != first


def test_memory_backend_has_nothing_to_migrate():
    assert composition.run_migrations() == "memory: nothing to migrate"


def test_dynamodb_migration_only_verifies_tables_by_default(monkeypatch, dynamodb_tables):
    """In a Region tables are infrastructure; the app must not create them"""
    _dynamodb_env(monkeypatch)
    assert composition.run_migrations() == "dynamodb: migration applied"
    monkeypatch.setenv("DYNAMODB_USERS_TABLE", "missing-users")
    with pytest.raises(composition.ConfigurationError, match="does not exist"):
        composition.run_migrations()


def test_dynamodb_migration_creates_missing_tables_when_allowed(monkeypatch, dynamodb_tables):
    monkeypatch.setenv("REPOSITORY_BACKEND", "dynamodb")
    monkeypatch.setenv("DYNAMODB_USERS_TABLE", "fresh-users")
    monkeypatch.setenv("DYNAMODB_ORDERS_TABLE", "fresh-orders")
    monkeypatch.setenv("DYNAMODB_CREATE_TABLES", "true")
    assert composition.run_migrations() == "dynamodb: migration applied"
    names = dynamodb_tables.meta.client.list_tables()["TableNames"]
    assert {"fresh-users", "fresh-orders"} <= set(names)
    # Idempotent
    assert composition.run_migrations() == "dynamodb: migration applied"


def test_postgres_backend_selected_from_environment(monkeypatch, postgres_options):
    monkeypatch.setenv("REPOSITORY_BACKEND", "postgres")
    monkeypatch.setenv("POSTGRES_DSN", postgres_options["POSTGRES_DSN"])
    assert composition.run_migrations() == "postgres: migration applied"
    bundle = composition.build_bundle()
    try:
        assert type(bundle.users).__name__ == "PostgresUserRepository"
    finally:
        bundle.close()
