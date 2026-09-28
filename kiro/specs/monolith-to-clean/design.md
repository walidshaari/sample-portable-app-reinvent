# Design: <SERVICE> monolith to clean architecture

## Overview

Split `<SOURCE_PATH>` into four layers. Dependencies point inward only. The
composition root is the single place that knows about configuration and concrete
adapters.

## Target layer map

| Layer | Path | May import | Contents |
|---|---|---|---|
| domain | `<ROOT>/domain/` | domain, allowlisted stdlib | entities and validation |
| ports | `<ROOT>/application/ports/` | domain, application, allowlisted stdlib | ABCs, no implementation |
| use cases | `<ROOT>/application/use_cases/` | domain, application, allowlisted stdlib | orchestration, id generation |
| infrastructure | `<ROOT>/infrastructure/` | anything | adapters, HTTP, handlers, composition root |

The allowlist is in `archguard.toml`; archguard rule AG001 enforces it.

## Ports

| Port | Methods | Replaces |
|---|---|---|
| `<Entity>Repository` | `create`, `find_by_id`, `find_all`, `delete` (async) | `<SDK calls from the scan>` |

## Composition root

`<ROOT>/infrastructure/composition.py`:

- Reads configuration (backend name, resource names, endpoint overrides, runtime label).
- Selects adapters from a registry keyed by backend name.
- Creates SDK clients once, inside functions, never at import time.
- Builds the HTTP application and returns it to each entrypoint.

## Adapters

| Adapter | Port | Notes |
|---|---|---|
| `InMemory<Entity>Repository` | `<Entity>Repository` | default; used by tests |
| `<Backend><Entity>Repository` | `<Entity>Repository` | SDK import lives here only |
| HTTP app | use cases | translates requests and errors; no adapter imports |
| Entrypoints | composition root | local server, container, serverless handler |

## Data mapping notes

- Entity fields to storage attributes: `<field> -> <attribute>`.
- Keys: `<partition key / primary key>`.
- Types that need conversion at the adapter boundary (for example floats to decimals
  for DynamoDB, datetimes to ISO strings).
- Error mapping: domain `ValueError` -> HTTP 400/422 in the HTTP adapter; missing item
  -> 404.

## Testing

- Entity and use-case tests with the network blocked.
- One contract test per port, parametrized over every adapter.
- `archguard check` as a test and as a required CI check.

---

## Filled example: src_python/monolith -> base_python/clean

- Layer map root: `base_python/clean`.
- Ports: `UserRepository`, `OrderRepository` replace `save_to_dynamodb` and
  `get_from_dynamodb` in `monolith.py`.
- Composition root: `base_python/clean/infrastructure/composition.py` reads
  `REPOSITORY_BACKEND`, `DYNAMODB_USERS_TABLE`, `DYNAMODB_ORDERS_TABLE`,
  `DYNAMODB_ENDPOINT_URL`, `APP_RUNTIME`.
- Adapters: `InMemoryUserRepository`, `InMemoryOrderRepository`,
  `DynamoDBUserRepository`, `DynamoDBOrderRepository`; FastAPI app in
  `infrastructure/http/fastapi_app.py`; entrypoints in `infrastructure/local/server.py`
  and `infrastructure/lambda/handler.py`.
- Data mapping: `id` is the partition key for both tables; order `total` needs a
  decimal conversion for DynamoDB.
- Open findings at the time of writing (from `archguard check`): the HTTP apps import
  in-memory adapters (AG003) and `infrastructure/local/server.py` reads `os.environ`
  (AG002). Tasks 5.2 and 5.3 cover them.
