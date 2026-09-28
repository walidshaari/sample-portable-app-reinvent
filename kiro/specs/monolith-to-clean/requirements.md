# Requirements: <SERVICE> monolith to clean architecture

Replace every `<PLACEHOLDER>` from the `archguard scan` report. A filled example for
`src_python/monolith -> base_python/clean` follows the template.

## Introduction

`<SOURCE_PATH>` is classified `<CLASSIFICATION>` with coupling score `<SCORE>`.
The goal is a layered service (`domain`, `application/ports`, `application/use_cases`,
`infrastructure`) that runs on any compute target with only the composition root
changing, while the external HTTP behavior stays the same.

## Requirement 1: Behavior is preserved

User story: As an API consumer, I want the same endpoints and responses, so that the
refactor is invisible to me.

1. WHEN a client calls any endpoint listed in the move 0 baseline THEN the system SHALL
   return the same status code and response shape as the baseline.
2. IF a request is invalid THEN the system SHALL return the same error status the
   baseline returned for that input.

## Requirement 2: Business rules live in the domain

1. The domain layer SHALL contain one entity per resource (`<ENTITIES>`), each validating
   its own fields.
2. WHEN an entity is constructed with invalid data THEN the entity SHALL raise
   `ValueError` without any I/O.
3. The domain layer SHALL import only modules allowed by `archguard.toml` (AG001).

## Requirement 3: Storage is behind ports

1. The system SHALL define one repository port per entity in `application/ports/` with
   abstract methods only.
2. WHEN a use case needs storage THEN it SHALL call a port, never an SDK (AG001).
3. The system SHALL provide an in-memory adapter for every port.
4. WHILE the core test suite runs, the system SHALL NOT open network sockets.

## Requirement 4: One composition root

1. The system SHALL read configuration only in `infrastructure/composition.py` (AG002).
2. The system SHALL import concrete adapters only in the composition root (AG003).
3. WHERE `REPOSITORY_BACKEND` selects an adapter, the composition root SHALL pick it
   from a registry and fail with a clear error for unknown names.
4. Resource names (tables, buckets, queues) SHALL come from configuration, not literals.

## Requirement 5: Real adapters and entrypoints

1. The system SHALL provide `<REAL_ADAPTERS>` implementing the same ports.
2. WHEN a port contract test runs THEN it SHALL pass against every adapter.
3. The system SHALL provide entrypoints `<ENTRYPOINTS>` that obtain their application
   from the composition root.

## Requirement 6: Enforcement

1. WHEN `archguard check` runs in CI THEN it SHALL exit 0 on the main branch.
2. WHEN an agent proposes a write that breaks a layer rule THEN the PreToolUse hook
   SHALL block it before the file is written.

---

## Filled example: src_python/monolith -> base_python/clean

- Source: `src_python/monolith/monolith.py`, classification `monolith`, coupling
  score 100. One module creates `boto3.resource('dynamodb')` at import time, mixes
  FastAPI routes with DynamoDB calls, and hard-codes the table names `Users` and
  `Orders`.
- Entities: `User`, `Order`.
- Baseline endpoints: `GET /health`, `POST /users`, `GET /users/{user_id}`,
  `POST /orders`, `GET /orders/{order_id}`.
- Real adapters: `DynamoDBUserRepository`, `DynamoDBOrderRepository`.
- Entrypoints: `infrastructure/local/server.py` (uvicorn), `infrastructure/lambda/handler.py`
  (Lambda), container CMD.
- Resource names: `DYNAMODB_USERS_TABLE`, `DYNAMODB_ORDERS_TABLE`, read in the
  composition root.
