---
inclusion: fileMatch
fileMatchPattern: "**/domain/**"
---

# Domain purity (you are editing a domain file)

- Allowed imports only: `domain` and the stdlib list in `archguard.toml`
  (`typing`, `dataclasses`, `re`, `uuid`, `datetime`, `decimal`, `enum`, `abc`,
  `__future__`, `collections`, `functools`, `itertools`, `math`, `numbers`, `string`).
  Anything else is AG001 and the write is blocked.
- No `os`, `sys`, `platform`, `logging`, SDKs (`boto3`, database drivers, HTTP
  clients) or frameworks (`fastapi`, `flask`, `pydantic` unless it is allowlisted).
- No configuration reads (AG002) and no dynamic imports.
- Entities validate their own invariants and raise `ValueError` with a clear message.
- Entities hold no I/O: no saving, loading, logging, sending or clock reads that
  depend on the environment. Pass timestamps and IDs in, or generate IDs in the
  use case.
- If an entity seems to need an outside capability (audit log, email, storage), stop:
  define a port in `application/ports/`, call it from a use case, and implement it in
  `infrastructure/`. See the `boundary-violation-fix` skill.
