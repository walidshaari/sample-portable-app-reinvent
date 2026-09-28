"""
HTTP adapter (FastAPI).

Translates HTTP into use-case calls and entity results back into JSON. It
receives every dependency as a parameter from the composition root: the two
repositories, the runtime label, identity facts, which exceptions mean "the
store is unavailable", and a readiness probe. It imports no concrete adapter
and reads no configuration.
"""
import os
import socket
from typing import Awaitable, Callable, Mapping, Optional, Tuple, Type

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from application.ports.order_repository import OrderRepository
from application.ports.user_repository import UserRepository
from application.use_cases.create_order import CreateOrderUseCase
from application.use_cases.create_user import CreateUserUseCase
from application.use_cases.delete_order import DeleteOrderUseCase
from application.use_cases.delete_user import DeleteUserUseCase

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")


async def _always_ready() -> None:
    return None


def create_fastapi_app(
    custom_user_repository: UserRepository,
    custom_order_repository: OrderRepository,
    *,
    runtime: str = "unknown",
    backend_name: Optional[str] = None,
    identity: Optional[Mapping[str, str]] = None,
    unavailable_errors: Tuple[Type[BaseException], ...] = (),
    readiness_probe: Callable[[], Awaitable[None]] = _always_ready,
) -> FastAPI:
    """FastAPI application factory. Called by the composition root."""
    app = FastAPI(title="Clean Architecture API")
    templates = Jinja2Templates(directory=TEMPLATES_DIR)

    user_repository = custom_user_repository
    order_repository = custom_order_repository

    create_user_use_case = CreateUserUseCase(user_repository)
    delete_user_use_case = DeleteUserUseCase(user_repository)
    create_order_use_case = CreateOrderUseCase(order_repository)
    delete_order_use_case = DeleteOrderUseCase(order_repository)

    # Identity of this deployment: which compute is serving and which adapter
    # is persisting. Same core code everywhere; only these values differ.
    backend = type(user_repository).__name__
    host = socket.gethostname()
    facts = dict(identity or {})

    # Failure containment: when the store is unreachable the adapter raises a
    # driver exception. It stops here as a 503; the process and the core stay up.
    async def store_unavailable(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "detail": "persistence unavailable",
                "backend": backend,
                "error": type(exc).__name__,
            },
        )

    for error_type in unavailable_errors:
        app.add_exception_handler(error_type, store_unavailable)

    # Health check - Web UI
    @app.get("/health", response_class=HTMLResponse)
    async def health_check_page(request: Request):
        return templates.TemplateResponse(
            request,
            "health.html",
            {"status": "healthy", "runtime": runtime, "backend": backend},
        )

    # Health check - API. Process-level: stays 200 while the store is down.
    @app.get("/api/health")
    def health_check_api():
        return {
            "status": "healthy",
            "message": "health from clean architecture",
            "runtime": runtime,
            "backend": backend,
            "repository_backend": backend_name or backend,
            "host": host,
            **facts,
        }

    # Readiness of the store behind the port, for people and monitors.
    @app.get("/api/ready")
    async def ready():
        await readiness_probe()
        return {"status": "ready", "backend": backend}

    # User endpoints - Web UI
    @app.get("/users", response_class=HTMLResponse)
    async def get_users_page(request: Request):
        users = await user_repository.find_all()
        return templates.TemplateResponse(request, "users.html", {"users": users})

    # User endpoints - API
    @app.post("/api/users", status_code=201)
    async def create_user(data: dict):
        try:
            user = await create_user_use_case.execute(data)
            return user.to_dict()
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.get("/api/users/{user_id}")
    async def get_user(user_id: str):
        user = await user_repository.find_by_id(user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        return user.to_dict()

    @app.get("/api/users")
    async def get_users_api():
        users = await user_repository.find_all()
        return [user.to_dict() for user in users]

    @app.delete("/api/users/{user_id}", status_code=204)
    async def delete_user(user_id: str):
        try:
            await delete_user_use_case.execute(user_id)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))

    # Order endpoints - Web UI
    @app.get("/orders", response_class=HTMLResponse)
    async def get_orders_page(request: Request):
        orders = await order_repository.find_all()
        return templates.TemplateResponse(request, "orders.html", {"orders": orders})

    # Order endpoints - API
    @app.post("/api/orders", status_code=201)
    async def create_order(data: dict):
        try:
            order = await create_order_use_case.execute(data)
            return order.to_dict()
        except (ValueError, TypeError) as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.get("/api/orders/{order_id}")
    async def get_order(order_id: str):
        order = await order_repository.find_by_id(order_id)
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")
        return order.to_dict()

    @app.get("/api/orders")
    async def get_orders_api():
        orders = await order_repository.find_all()
        return [order.to_dict() for order in orders]

    @app.delete("/api/orders/{order_id}", status_code=204)
    async def delete_order(order_id: str):
        try:
            await delete_order_use_case.execute(order_id)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))

    return app
