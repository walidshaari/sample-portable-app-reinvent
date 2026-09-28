"""
Shared fixtures for every test.

Kept deliberately light: it imports nothing beyond pytest, so the core tests
in tests/core start fast and load no SDK or driver. Cloud and database
fixtures live in tests/integration/conftest.py.
"""
import pytest

_APP_VARIABLES = (
    "REPOSITORY_BACKEND",
    "APP_RUNTIME",
    "PORT",
    "HOST",
    "POD_NAME",
    "NODE_NAME",
    "POD_NAMESPACE",
    "APP_IMAGE",
)


@pytest.fixture(autouse=True)
def _scrub_app_environment(monkeypatch):
    """A stray shell setting must never change which adapter a test gets"""
    import os

    for name in list(os.environ):
        if name in _APP_VARIABLES or name.startswith(("DYNAMODB_", "POSTGRES_")):
            monkeypatch.delenv(name, raising=False)
