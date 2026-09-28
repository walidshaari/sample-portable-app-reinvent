---
inclusion: fileMatch
fileMatchPattern: "**/domain/**"
---

# Domain purity (you are editing a domain file)

- Import only `domain` and the standard library modules allowed by `archguard.toml`.
  Archguard blocks other imports as AG001.
- Keep SDKs, database drivers, and web frameworks out of the domain.
- Do not read configuration (AG002) or use dynamic imports.
- Validate entity invariants in the entity. Raise `ValueError` with a clear message.
- Keep I/O and environment-dependent clock reads out of entities. Pass timestamps
  into the entity or generate IDs in a use case.
- When an entity needs an outside capability, define a port in
  `application/ports/`. Call it from a use case and implement it in
  `infrastructure/`. See the `boundary-violation-fix` skill.
