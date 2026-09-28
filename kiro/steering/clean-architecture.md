---
inclusion: always
---

# Clean architecture rules

This repository enforces these rules with archguard (`tools/archguard/archguard.py`,
config in `archguard.toml`). A PreToolUse hook blocks writes that break them. Follow
the rules so the hook never has to.

## 1. Dependency rule (AG001)

Dependencies point inward only: `infrastructure -> application -> domain`.
Nothing in `domain/` or `application/` imports from an outer layer.

## 2. Allowed imports per layer (AG001)

Imports are default-deny. Anything not listed is a violation.

- `domain/`: `domain`, and the stdlib modules `typing`, `dataclasses`, `re`, `uuid`,
  `datetime`, `decimal`, `enum`, `abc`, `__future__`, `collections`, `functools`,
  `itertools`, `math`, `numbers`, `string`.
- `application/` (ports and use cases): everything `domain/` may import, plus `domain`
  and `application`.
- `infrastructure/`: any import. AG002 and AG003 still apply.
- No dynamic imports (`importlib.import_module`, `__import__`) in `domain/` or
  `application/`.

The exact lists live in `archguard.toml`; that file wins over this summary.

## 3. Configuration is read only in the composition root (AG002)

`os.environ`, `os.getenv`, `from os import environ`, and config loaders (`dotenv`,
`decouple`, `environs`) appear only in the composition root
(`infrastructure/composition.py`). Every other module receives values as parameters.

## 4. No runtime branching in the core (AG001)

Entities and use cases do not import `os`, `sys` or `platform`, and do not check a
runtime label (for example "lambda", "ecs", "local"). The same core code runs on every
compute target. Only the composition root chooses adapters for a runtime.

## 5. No framework object crosses a use-case boundary (AG001)

Use cases take and return plain data (dataclasses, primitives) or domain entities.
They never receive or return a request, response, `HTTPException`, SDK client or ORM
row. HTTP adapters translate at the edge.

## 6. Adapters are wired only in the composition root (AG003)

Only the composition root imports concrete adapters from
`infrastructure/repositories/`. HTTP apps, handlers and use cases depend on ports and
receive adapters as parameters.

## 7. Every port has a contract test against every adapter

For each port in `application/ports/`, one parametrized test runs the same assertions
against every adapter (in-memory first, then real ones behind an opt-in flag). Core
tests run with no network and no credentials.

## 8. Fix pattern for a violation

When archguard reports an SDK or framework import in the core:

1. Define a port (an ABC) in `application/ports/` named for the capability, not the
   vendor (`AuditLog`, not `S3Client`).
2. Implement it in `infrastructure/` (for example `infrastructure/audit/s3_audit_log.py`).
3. Construct the adapter in the composition root and inject it into the use case.
4. Add an in-memory adapter and a contract test that both adapters pass.
5. Run `python3 tools/archguard/archguard.py check` and the tests.

The `boundary-violation-fix` skill walks through this with a worked example.
