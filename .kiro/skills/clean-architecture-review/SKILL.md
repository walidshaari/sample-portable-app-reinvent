---
name: clean-architecture-review
description: Brownfield architecture review of a Python codebase. Runs archguard scan to classify the code (hexagonal-clean, layered, mvc, monolith, mixed), compute a coupling score, map boundaries and violations, and produce an ordered six-move refactor plan toward clean architecture. Use when asked to assess, audit or plan a refactor of existing Python code, or to explain why code is hard to move between runtimes.
---

# Clean architecture review

Read-only. Do not edit code during the review.

## Step 1: Locate archguard

Use the first that exists:

- `kiro/archguard/archguard.py` in this repository, or `tools/archguard/archguard.py` in a repository set up by `install.sh`
- `kiro/archguard/archguard.py` in the workspace (source layout)
- `../../archguard/archguard.py` relative to this skill folder (inside the power)

Call it `$AG` below. If an MCP client has the `archguard` server, `archguard_scan` and
`archguard_check` give the same results.

## Step 2: Scan

```sh
python3 "$AG" scan <path> --format md
```

`<path>` is the directory that contains the code (for example `src/` or the service
folder). No configuration is needed. Use `--churn-days N` to change the churn window.
Keep the full report; later steps quote it.

## Step 3: Check (only if `archguard.toml` exists)

```sh
python3 "$AG" check
```

Exit 0 is clean, 1 means violations (listed with rule ids AG001, AG002, AG003),
3 means a configuration error. AG000 lines are parse warnings.

## Step 4: Write the review

Produce these sections, quoting archguard output where possible:

1. Classification and coupling score, with the reason archguard printed.
2. Boundary map: the table from the scan, then two or three sentences on the
   dependency direction. Include the Mermaid graph and name each red edge.
3. Violation report: each finding with file:line, what it breaks and the fix pattern
   (port in `application/ports/`, adapter in `infrastructure/`, wiring in the
   composition root).
4. Refactor plan: the six moves from the scan (0 baseline, 1 entities, 2 ports,
   3 use cases, 4 in-memory adapter, 5 composition root, 6 real adapters and
   entrypoints). Keep the concrete files and the verification command for each move.
   Say which moves are skipped and why. Order work by churn: high-churn files first.
5. Risks: behavior that the moves could change (error codes, response shapes,
   resource names) and how the baseline from move 0 protects it.

Label anything that is your judgment rather than archguard output.

## Step 5: Offer a tracked spec

Offer to create a Kiro spec from the template: copy `.kiro/specs/monolith-to-clean/`
(or `specs/monolith-to-clean/` inside the power) to `.kiro/specs/<name>/` and fill the
placeholders from the scan. Only do this if the user agrees.
