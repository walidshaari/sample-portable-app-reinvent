"""
Core test guard rails.

Every test in tests/core runs with the network switched off and with no cloud
credentials in the environment. If a core test ever needed either, it would
fail here, loudly. These tests import only domain/ and application/, plus
fakes defined in this folder.
"""
import os
import socket

import pytest

_CREDENTIAL_VARIABLES = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_PROFILE",
    "AWS_CONTAINER_CREDENTIALS_FULL_URI",
    "AWS_WEB_IDENTITY_TOKEN_FILE",
)


class NetworkDisabled(RuntimeError):
    pass


def _refuse(*args, **kwargs):
    raise NetworkDisabled("core tests must not open network connections")


@pytest.fixture(autouse=True)
def _no_network_no_credentials(monkeypatch):
    for name in _CREDENTIAL_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    monkeypatch.setattr(socket, "getaddrinfo", _refuse)
    yield
    assert not any(os.environ.get(name) for name in _CREDENTIAL_VARIABLES)
