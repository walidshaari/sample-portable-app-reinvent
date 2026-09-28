# Tasks: <SERVICE> monolith to clean architecture

`$AG` is the path to archguard (`tools/archguard/archguard.py` after install).
Each task ends with a verification command that must pass before the box is ticked.

- [ ] 0. Record the monolith baseline
  - [ ] 0.1 Save the scan: `python3 $AG scan <SOURCE_PATH> --format json > archguard-baseline.json`
  - [ ] 0.2 Record one request and response per endpoint (status and body shape).
  - [ ] 0.3 List routes that call SDKs directly and hard-coded resource names from the scan.
  - Verify: `python3 $AG scan <SOURCE_PATH>` classification and score match the saved baseline.

- [ ] 1. Extract entities
  - [ ] 1.1 Create `<ROOT>/domain/<entity>.py` for each of `<ENTITIES>` with validation.
  - [ ] 1.2 Remove validation from the route handlers.
  - [ ] 1.3 Unit tests: invalid input raises `ValueError`.
  - Verify: `python3 $AG check <ROOT>/domain` exits 0 and `python3 -m pytest tests/test_domain*.py -q` passes.

- [ ] 2. Define ports
  - [ ] 2.1 Create `<ROOT>/application/ports/<entity>_repository.py` (ABC, abstract methods only).
  - Verify: `python3 $AG check <ROOT>/application/ports` exits 0.

- [ ] 3. Extract use cases
  - [ ] 3.1 One use case per write operation in `<ROOT>/application/use_cases/`.
  - [ ] 3.2 Move id generation and orchestration into the use cases.
  - [ ] 3.3 Use cases take and return plain data or entities, never framework objects.
  - Verify: `python3 $AG check <ROOT>/application` exits 0 and use-case tests pass with sockets blocked.

- [ ] 4. Write the first adapter
  - [ ] 4.1 `InMemory<Entity>Repository` in `<ROOT>/infrastructure/repositories/`.
  - [ ] 4.2 Contract test per port, parametrized over adapters.
  - Verify: `python3 -m pytest -q` passes with no credentials and no network.

- [ ] 5. Add a composition root
  - [ ] 5.1 `<ROOT>/infrastructure/composition.py` reads configuration and selects adapters from a registry.
  - [ ] 5.2 Remove configuration reads from every other module (AG002).
  - [ ] 5.3 Remove adapter imports from HTTP apps and handlers (AG003).
  - Verify: `python3 $AG check` reports no AG002 or AG003.

- [ ] 6. Add real adapters and entrypoints
  - [ ] 6.1 `<Backend><Entity>Repository` implementing each port; SDK clients created inside functions.
  - [ ] 6.2 HTTP app routes call use cases only.
  - [ ] 6.3 Entrypoints (local server, container, serverless handler) call the composition root.
  - [ ] 6.4 Contract tests against the real adapter behind an opt-in flag with an emulator.
  - Verify: `python3 $AG scan <ROOT>` reports `hexagonal-clean` with coupling score 0, and `python3 $AG check` exits 0.

---

## Filled example: src_python/monolith -> base_python/clean

- [x] 0. Baseline: `python3 $AG scan src_python/monolith` reports `monolith`, score 100,
  `boto3.resource('dynamodb')` at import time, table literals `Users` and `Orders`.
- [x] 1. Entities: `base_python/clean/domain/user.py`, `base_python/clean/domain/order.py`.
- [x] 2. Ports: `base_python/clean/application/ports/user_repository.py`, `order_repository.py`.
- [x] 3. Use cases: `create_user.py`, `delete_user.py`, `create_order.py`, `delete_order.py`.
- [x] 4. In-memory adapters: `in_memory_user_repository.py`, `in_memory_order_repository.py`.
- [ ] 5. Composition root: `base_python/clean/infrastructure/composition.py` exists; open
  items are AG003 in `infrastructure/http/fastapi_app.py` and `flask_app.py`, and AG002
  in `infrastructure/local/server.py`.
  - Verify: `python3 kiro/archguard/archguard.py check --config kiro/archguard/archguard.example.toml` exits 0.
- [x] 6. Real adapters and entrypoints: `dynamodb_user_repository.py`,
  `dynamodb_order_repository.py`, `infrastructure/local/server.py`,
  `infrastructure/lambda/handler.py`.
