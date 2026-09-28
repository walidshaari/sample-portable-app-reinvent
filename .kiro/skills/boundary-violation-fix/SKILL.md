---
name: boundary-violation-fix
description: Step-by-step refactor of an archguard boundary violation (AG001 SDK or framework import in domain or application, AG002 configuration read outside the composition root, AG003 adapter import outside the composition root) into a port, an adapter, composition-root wiring and a contract test. Use when archguard or a hook blocked a change, or when core code needs an outside capability such as storage, audit logging, email or HTTP calls.
---

# Fix a boundary violation

Locate archguard first: `tools/archguard/archguard.py`, `kiro/archguard/archguard.py`,
or `../../archguard/archguard.py` from this skill folder. Call it `$AG`.

## Step 1: Read the finding

Each finding has a rule id, `file:line`, the offending import, why it is denied and a
fix hint. Do not work around it (no dynamic imports, no moving the file out of the
layer, no editing `archguard.toml` to allow the import) unless the user explicitly asks
to change the rules.

| Rule | Fix |
|---|---|
| AG001 SDK/framework in core | port + adapter + composition wiring (steps 2 to 6) |
| AG001 outer-layer import | move the shared type inward, or depend on a port |
| AG002 config read | read the value in the composition root, pass it as a parameter |
| AG003 adapter import | accept the port as a parameter; construct the adapter in the composition root |

## Step 2: Name the capability

Name the port after what the core needs, not the vendor: `AuditLog.record(event)`,
not `S3Client.put_object`. Methods take and return plain data or entities.

## Step 3: Define the port

`application/ports/<capability>.py`: an ABC with abstract methods and no implementation.

## Step 4: Implement adapters

- In-memory adapter in `infrastructure/<area>/in_memory_<capability>.py` for tests.
- Real adapter in `infrastructure/<area>/<vendor>_<capability>.py`. The SDK import and
  client creation live here, inside `__init__` or a factory, never at module import time.

## Step 5: Call the port from a use case

Entities stay pure. The use case receives the port in its constructor and calls it
after the entity is valid.

## Step 6: Wire it in the composition root

Only `infrastructure/composition.py` reads the configuration (bucket name, endpoint)
and constructs the adapter. Register adapters by name so the environment selects one.

## Step 7: Contract test

One parametrized test runs the same assertions against every adapter. The in-memory
case always runs; the real case runs only behind an opt-in flag with an emulator.

## Step 8: Verify

```sh
python3 "$AG" check        # the original finding is gone, no new findings
python3 -m pytest -q       # core tests pass with the network blocked
```

## Worked example: S3 audit log requested inside a User entity

Request: "Every time a User is created, write an audit record to S3."

Wrong (blocked by the hook, AG001 and AG002):

```python
# domain/user.py
import os
import boto3  # AG001: domain imports 'boto3'

class User:
    def __init__(self, id, name, email):
        ...
        boto3.client("s3").put_object(Bucket=os.environ["AUDIT_BUCKET"], ...)  # AG002
```

Port:

```python
# application/ports/audit_log.py
from abc import ABC, abstractmethod


class AuditLog(ABC):
    @abstractmethod
    async def record(self, event: str, subject_id: str, payload: dict) -> None:
        """Persist one audit event."""
```

Use case (entity unchanged and pure):

```python
# application/use_cases/create_user.py
class CreateUserUseCase:
    def __init__(self, users: UserRepository, audit: AuditLog) -> None:
        self._users = users
        self._audit = audit

    async def execute(self, data: CreateUserInput) -> User:
        user = User(id=str(uuid.uuid4()), name=data.name, email=data.email)
        await self._users.create(user)
        await self._audit.record("user.created", user.id, {"email": user.email})
        return user
```

Adapters:

```python
# infrastructure/audit/in_memory_audit_log.py
class InMemoryAuditLog(AuditLog):
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []

    async def record(self, event, subject_id, payload) -> None:
        self.events.append((event, subject_id, payload))


# infrastructure/audit/s3_audit_log.py
import asyncio
import json
import uuid

import boto3


class S3AuditLog(AuditLog):
    def __init__(self, bucket: str, client=None) -> None:
        self._bucket = bucket
        self._client = client or boto3.client("s3")

    async def record(self, event, subject_id, payload) -> None:
        key = f"audit/{event}/{subject_id}/{uuid.uuid4()}.json"
        body = json.dumps({"event": event, "subject_id": subject_id, "payload": payload})
        await asyncio.to_thread(self._client.put_object, Bucket=self._bucket, Key=key, Body=body)
```

Composition root:

```python
# infrastructure/composition.py
def build_audit_log() -> AuditLog:
    backend = os.environ.get("AUDIT_BACKEND", "memory")
    if backend == "s3":
        from infrastructure.audit.s3_audit_log import S3AuditLog
        return S3AuditLog(bucket=os.environ["AUDIT_BUCKET"])
    return InMemoryAuditLog()
```

Contract test:

```python
# tests/test_audit_log_contract.py
ADAPTERS = [InMemoryAuditLog]
if os.environ.get("RUN_S3_CONTRACT"):
    ADAPTERS.append(lambda: S3AuditLog(bucket=os.environ["AUDIT_BUCKET"]))


@pytest.mark.parametrize("make", ADAPTERS)
def test_record_accepts_event(make):
    asyncio.run(make().record("user.created", "u1", {"email": "user@example.com"}))
```

Then run step 8. `python3 "$AG" check` should report no findings for `domain/user.py`.
