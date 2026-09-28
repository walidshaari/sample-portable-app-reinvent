---
name: clean-architecture-review
description: Brownfield architecture review of a Python codebase. Runs archguard score and scan (JSON) to report portability and cleanliness scores, classify the code (hexagonal-clean, layered, mvc, monolith, mixed), map boundaries and violations, and produce an ordered six-move refactor plan toward clean architecture. Use when asked to assess, audit or plan a refactor of existing Python code, or to explain why code is hard to move between runtimes or infrastructure.
---

# Clean architecture review

Read-only. Do not edit code during the review. Works the same in Kiro, Claude Code and
Codex: every step runs a command with JSON output, or the equivalent MCP tool.

## Step 1: Locate archguard

Use the first that exists and call it `$AG`:

- `tools/archguard/archguard.py` (a repository set up by `install.sh`)
- `kiro/archguard/archguard.py` (the source repository)
- `../../archguard/archguard.py` relative to this skill folder (inside the power)

If the `archguard` MCP server is connected, `archguard_score`, `archguard_scan` and
`archguard_check` return the same JSON.

## Step 2: Score (only if `archguard.toml` exists)

```sh
python3 "$AG" score --format json
```

Read these fields and do not re-derive them:

- `scores.portability.score`, `.clean_files`, `.files`, `.by_rule`
- `scores.cleanliness.score`, `.functions`, `.thresholds`, `.by_rule`
- `findings[]`: `id`, `rule`, `dimension`, `severity`, `path`, `line`, `message`, `hint`,
  and for AGQ rules `symbol`, `value`, `threshold`
- `warnings[]`: AG000 files that do not parse

Exit code 0 means scored (1 only when a `--min-*` gate fails, 3 is a configuration
error). Without `archguard.toml`, skip this step and say so.

## Step 3: Scan

```sh
python3 "$AG" scan <path> --format json
```

`<path>` is the directory that contains the code (for example `src/`). No configuration
is needed. Use `classification`, `classification_reason`, `coupling_score`, `modules[]`,
`edges[]` (`violation` is true for a wrong-direction edge), `findings`, `churn` and
`plan[]` (`move`, `title`, `status`, `actions`, `verify`). If the user wants the
dependency graph, run `scan <path> --format md` once and copy its Mermaid block.

## Step 4: Write the review

1. Scores: portability and cleanliness with their counts, and what drives each number.
2. Classification and coupling score, with `classification_reason`.
3. Boundary map: module, layer, framework and SDK dependencies, notes. Name each
   violating edge.
4. Findings: group by `dimension`, then `severity`. For each give `rule`, `path:line`,
   `message`, the fix pattern (port in `application/ports/`, adapter in
   `infrastructure/`, wiring in the composition root) and the finding `id`.
5. Refactor plan: the six moves from `plan[]` (0 baseline, 1 entities, 2 ports,
   3 use cases, 4 in-memory adapter, 5 composition root, 6 real adapters and
   entrypoints), each with its files and `verify` command. Say which moves are
   skipped and why. Order work by churn: high-churn files first.
6. Risks: behavior the moves could change (error codes, response shapes, resource
   names) and how the move 0 baseline protects it.

Label anything that is your judgment rather than archguard output.

## Step 5: Offer next steps

- `boundary-violation-fix` for individual findings.
- In Kiro, a tracked spec copied from `.kiro/specs/monolith-to-clean/`. Only if the
  user agrees.
