# Portable clean architecture: EKS and kind

This Python example starts with a monolith and extracts its business rules into
a clean core. One container image runs on EKS in an AWS Region and on a local
kind cluster that stands in for on-premises Kubernetes. The clusters use
separate settings and data stores. Shared code makes a move possible; it does
not by itself provide failover or prove data residency.

## Run the core locally

Use Python 3.11 or later. From the repository root:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r base_python/clean/requirements-dev.txt
cd base_python/clean
../../.venv/bin/python -m pytest
HOST=127.0.0.1 REPOSITORY_BACKEND=memory ../../.venv/bin/python server.py
```

Request `http://127.0.0.1:9000/api/health` in another terminal. The
composition root also supports DynamoDB and PostgreSQL when their connection
settings are provided.

## Follow the code

1. `src_python/monolith/monolith.py` puts a storage call inside a route.
2. `base_python/clean/domain` validates entities. The use cases in
   `base_python/clean/application` depend on repository ports.
3. `base_python/clean/infrastructure/composition.py` selects a repository
   adapter. `infrastructure/http/fastapi_app.py` maps requests and store errors
   to HTTP responses.
4. `codetalk_deployment/Dockerfile` packages that app. The EKS and kind
   overlays select their runtime settings and persistence.
5. `demo/09-two-targets.sh` checks the running image digest and core
   fingerprint on both clusters. `demo/03-config-axis.sh` shows a settings
   change; `demo/04-tech-axis.sh` shows an adapter change.
6. `archguard.toml`, `.kiro/`, and `kiro/` contain the boundary checker,
   agent hooks, skills, steering, MCP server, and installer. Run
   `python3 kiro/archguard/archguard.py check` from the repository root.

See `codetalk_deployment/README.md` for build and deployment commands. This
project is licensed under MIT-0; see `LICENSE`.
