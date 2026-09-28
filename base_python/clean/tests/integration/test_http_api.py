"""
HTTP tests through the real FastAPI app.

Proven here:
  1. GET /api/health reports which runtime and which repository adapter are
     serving, plus the core fingerprint, which is what the live demo curls.
  2. The same HTTP flow works unchanged across in-memory, DynamoDB and
     PostgreSQL.
  3. When the store is unreachable, data routes answer 503 and /api/health
     stays 200: the failure is contained at the adapter edge.
"""
import pytest
from fastapi.testclient import TestClient

from infrastructure import composition
from infrastructure.composition import compose_app


def test_default_app_is_in_memory_and_health_reports_it():
    client = TestClient(compose_app())
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    assert body["runtime"] == "unknown"
    assert body["backend"] == "InMemoryUserRepository"
    assert body["repository_backend"] == "memory"
    assert body["core_sha256"] == composition.core_fingerprint()
    assert body["host"]


def test_health_reports_runtime_and_identity_from_environment(monkeypatch):
    monkeypatch.setenv("APP_RUNTIME", "lambda")
    monkeypatch.setenv("POD_NAME", "app-7f9")
    monkeypatch.setenv("NODE_NAME", "node-a")
    body = TestClient(compose_app()).get("/api/health").json()
    assert body["runtime"] == "lambda"
    assert body["pod"] == "app-7f9"
    assert body["node"] == "node-a"


@pytest.mark.parametrize("path", ["/health", "/users", "/orders"])
def test_html_pages_render(path):
    response = TestClient(compose_app()).get(path)
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_html_health_page_shows_runtime_and_backend(monkeypatch):
    monkeypatch.setenv("APP_RUNTIME", "local")
    html = TestClient(compose_app()).get("/health").text
    assert "local" in html
    assert "InMemoryUserRepository" in html


@pytest.fixture(params=["memory", "dynamodb", "postgres"])
def backend_env(request, monkeypatch):
    monkeypatch.setenv("REPOSITORY_BACKEND", request.param)
    if request.param == "dynamodb":
        request.getfixturevalue("dynamodb_tables")
        monkeypatch.setenv("DYNAMODB_USERS_TABLE", "test-users")
        monkeypatch.setenv("DYNAMODB_ORDERS_TABLE", "test-orders")
    if request.param == "postgres":
        options = request.getfixturevalue("postgres_options")
        monkeypatch.setenv("POSTGRES_DSN", options["POSTGRES_DSN"])
        composition.run_migrations()
    return request.param


def test_user_flow_is_identical_across_backends(backend_env):
    with TestClient(compose_app()) as client:
        created = client.post("/api/users", json={"name": "Grace Hopper", "email": "grace@example.com"})
        assert created.status_code == 201
        user_id = created.json()["id"]

        fetched = client.get(f"/api/users/{user_id}")
        assert fetched.status_code == 200
        assert fetched.json() == created.json()

        assert [u["id"] for u in client.get("/api/users").json()] == [user_id]
        assert client.get("/api/ready").status_code == 200

        assert client.delete(f"/api/users/{user_id}").status_code == 204
        assert client.get(f"/api/users/{user_id}").status_code == 404


def test_order_validation_is_a_400_not_a_500():
    client = TestClient(compose_app())
    missing_quantity = client.post("/api/orders", json={"user_id": "u-1", "product": "Pen"})
    assert missing_quantity.status_code == 400
    zero = client.post("/api/orders", json={"user_id": "u-1", "product": "Pen", "quantity": 0})
    assert zero.status_code == 400


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "Ada"},
        {"name": 42, "email": "ada@example.com"},
        {"name": "Ada", "email": 42},
    ],
)
def test_malformed_user_input_returns_400(payload):
    response = TestClient(compose_app()).post("/api/users", json=payload)
    assert response.status_code == 400
    assert response.json()["detail"]


@pytest.mark.parametrize(
    "payload",
    [
        {"user_id": "u-1", "product": 42, "quantity": 1},
        {"user_id": "u-1", "product": "Pen", "quantity": "2"},
        {"user_id": "u-1", "product": "Pen", "quantity": None},
    ],
)
def test_malformed_order_input_returns_400(payload):
    response = TestClient(compose_app()).post("/api/orders", json=payload)
    assert response.status_code == 400
    assert response.json()["detail"]


def test_shared_state_two_apps_one_table(monkeypatch, dynamodb_tables):
    """
    The shared-state proof in miniature: two separately composed apps
    (think Lambda and EKS) that share one DynamoDB table see the same record.
    The in-memory adapter cannot do this, by design.
    """
    monkeypatch.setenv("REPOSITORY_BACKEND", "dynamodb")
    monkeypatch.setenv("DYNAMODB_USERS_TABLE", "test-users")
    monkeypatch.setenv("DYNAMODB_ORDERS_TABLE", "test-orders")
    monkeypatch.setenv("APP_RUNTIME", "lambda")
    writer = TestClient(compose_app())
    monkeypatch.setenv("APP_RUNTIME", "eks")
    reader = TestClient(compose_app())

    assert writer.get("/api/health").json()["runtime"] == "lambda"
    assert reader.get("/api/health").json()["runtime"] == "eks"

    created = writer.post("/api/users", json={"name": "Grace Hopper", "email": "grace@example.com"}).json()
    assert reader.get(f"/api/users/{created['id']}").json() == created


def test_in_memory_apps_do_not_share_state():
    a = TestClient(compose_app())
    b = TestClient(compose_app())
    created = a.post("/api/users", json={"name": "Grace Hopper", "email": "grace@example.com"}).json()
    assert b.get(f"/api/users/{created['id']}").status_code == 404


def test_dead_dynamodb_endpoint_is_contained_as_503(monkeypatch):
    """Nothing listens on port 9; the adapter error stops at the HTTP edge"""
    monkeypatch.setenv("REPOSITORY_BACKEND", "dynamodb")
    monkeypatch.setenv("DYNAMODB_USERS_TABLE", "t-users")
    monkeypatch.setenv("DYNAMODB_ORDERS_TABLE", "t-orders")
    monkeypatch.setenv("DYNAMODB_ENDPOINT_URL", "http://127.0.0.1:9")
    client = TestClient(compose_app())
    assert client.get("/api/health").status_code == 200
    response = client.get("/api/users")
    assert response.status_code == 503
    assert response.json()["detail"] == "persistence unavailable"
    assert client.get("/api/ready").status_code == 503


def test_dead_postgres_endpoint_is_contained_as_503(monkeypatch):
    monkeypatch.setenv("REPOSITORY_BACKEND", "postgres")
    monkeypatch.setenv("POSTGRES_DSN", "postgresql://app@127.0.0.1:9/app")
    monkeypatch.setenv("POSTGRES_POOL_TIMEOUT", "1")
    monkeypatch.setenv("POSTGRES_CONNECT_TIMEOUT", "1")
    app = compose_app()
    client = TestClient(app)
    assert client.get("/api/health").status_code == 200
    response = client.get("/api/users")
    assert response.status_code == 503
    assert response.json()["error"] in ("PoolTimeout", "OperationalError")
