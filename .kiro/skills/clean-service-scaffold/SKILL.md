---
name: clean-service-scaffold
description: Greenfield scaffold for a new clean-architecture Python service (stdlib only). Generates domain entity, repository port, use cases, in-memory adapter, composition root, stdlib HTTP JSON API, network-blocked tests, vendored archguard with Kiro hooks and steering, and a Dockerfile. Use when asked to create, start or bootstrap a new Python service or microservice with clean or hexagonal architecture.
---

# Clean service scaffold

## Step 1: Choose names

- Service name: lowercase letters, digits, `-` or `_` (for example `notes-service`).
- Entity: CamelCase singular (default `Note`).
- Destination directory: where the service folder is created (must not already hold a
  non-empty folder with that name).

Ask only if the user gave no service name.

## Step 2: Generate

```sh
python3 scripts/new_service.py <service_name> <dest_dir> --entity <Entity>
```

`scripts/` is inside this skill folder. The script finds `archguard.py` and the
steering files by searching upward; pass `--archguard PATH` if it cannot.

## Step 3: Verify (both must pass before you report success)

```sh
cd <dest_dir>/<service_name>
python3 -m pytest
python3 tools/archguard/archguard.py check   # run inside the generated service, which vendors archguard there
```

The tests block network sockets, need no cloud credentials and should finish in
well under a second. If either command fails, fix the generated code, not the rules.

## Step 4: Report

Tell the user the created path, the test count, the archguard result, and how to run
the server (`PORT=8080 python3 src/main.py`, then `curl localhost:8080/api/health`).
Mention that the HTTP API has no authentication and binds to 127.0.0.1 by default.

## Extending the service

- New capability: add a port in `src/application/ports/`, a use case in
  `src/application/use_cases/`, an in-memory adapter, then register the adapter in
  `src/infrastructure/composition.py`.
- Real storage: add `src/infrastructure/repositories/<backend>_<entity>_repository.py`,
  add it to `REPOSITORY_REGISTRY`, read its settings only in the composition root.
