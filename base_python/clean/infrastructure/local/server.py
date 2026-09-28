import uvicorn

from infrastructure.composition import (
    compose_app,
    load_settings,
    repository_backend,
    runtime_label,
    server_bind,
)


def run_server():
    """Run the FastAPI server"""
    settings = load_settings()
    host, port = server_bind(settings)
    print("Starting Clean Architecture Server...")
    print(f"Server running at http://localhost:{port}")
    print(f"Runtime label:      {runtime_label(settings)}")
    print(f"Repository backend: {repository_backend(settings)}")
    print("\nAvailable endpoints:")
    print("  GET    /health              - Health page (HTML)")
    print("  GET    /api/health          - Health check (JSON)")
    print("  GET    /api/ready           - Store readiness (JSON, 503 when unreachable)")
    print("  GET    /users               - Users page (HTML)")
    print("  POST   /api/users           - Create user")
    print("  GET    /api/users           - Get all users")
    print("  GET    /api/users/{id}      - Get user")
    print("  DELETE /api/users/{id}      - Delete user")
    print("  GET    /orders              - Orders page (HTML)")
    print("  POST   /api/orders          - Create order")
    print("  GET    /api/orders          - Get all orders")
    print("  GET    /api/orders/{id}     - Get order")
    print("  DELETE /api/orders/{id}     - Delete order")
    print()
    app = compose_app(settings)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run_server()
