# clean-architecture-guard

Enforcement, detection and refactor tooling for clean architecture in Python.
The core is `archguard/archguard.py`: one stdlib-only file (Python 3.11+) that checks
layer imports, configuration reads and adapter wiring, and analyzes brownfield code.
The same checker can run from agent hooks, git pre-commit, CI and an MCP server.

## What each piece does

| Mechanism | Job | Blocks or informs |
|---|---|---|
| PreToolUse hook (`hooks/kiro-clean-guard.json`, Claude and Codex snippets) | evaluates the proposed file content of agent writes, and staged content on agent `git commit` | blocks (exit 2, report goes to the agent) |
| Stop hook | runs `archguard check` when the agent finishes | informs (report shown to the user) |
| `install.sh` + `render.py` | install the pack for Kiro, and with `--with-claude`/`--with-codex` render the same rules, skills, reviewer agent, MCP and hooks for Claude Code and Codex | installs |
| Steering `clean-architecture.md` (always) and `domain-purity.md` (fileMatch `**/domain/**`) | states the rules before code is written | prevents |
| Skills `clean-architecture-review`, `clean-service-scaffold`, `boundary-violation-fix`, `clean-guard-install` | review brownfield code, scaffold a service, fix a finding, install the guard | informs |
| Custom agent `architecture-reviewer` | read-only analysis and six-move plan | informs |
| Spec template `specs/monolith-to-clean` | tracked refactor with a verification command per task | informs |
| MCP server `archguard/mcp_server.py` | `archguard_scan`, `archguard_check` and `archguard_score` for any MCP client | informs |
| git pre-commit (`hooks/git-pre-commit`) and CI (`ci/github-actions-architecture.yml`) | checks staged or merged files when installed and enabled | blocks |

Agent hooks are guardrails, not the boundary. Shell commands can write files without
going through the write tools, and Kiro file triggers (`PostFileSave` and similar) fire
only for agent changes, not for edits a person makes. The boundary for humans and for
shell writes is the pre-commit hook plus a required CI check once both are enabled.

## Rules

| Id | Rule |
|---|---|
| AG001 | a layer imports a module that is not in its `allow` list (default-deny, dotted-prefix match; relative imports, `importlib.import_module` and `__import__` included) |
| AG002 | configuration read (`os.environ`, `os.getenv`, `from os import environ`, `dotenv`/`decouple`/`environs`) outside `composition_roots` |
| AG003 | import of `adapter_packages` outside the composition roots and the adapter package itself |
| AG000 | file does not parse (warning in `check`; in `hook` mode a line-based fallback scan is used) |
| AGQ101 | function cyclomatic complexity above `quality.max_complexity` (default 10); `score` only |
| AGQ102 | function longer than `quality.max_function_lines` (default 50); `score` only |
| AGQ103 | function takes more than `quality.max_parameters` (default 5; `self`/`cls` not counted); `score` only |

`check`, `staged` and the hooks enforce AG001 to AG003 only. The AGQ rules feed the
cleanliness score and never block a write or a commit.

Test files (`tests/` directories, `test_*.py`, `conftest.py`), non-`.py` files and files
outside `source_roots` are not governed. See `archguard/archguard.example.toml`.

## Install

### Kiro

This power uses the Agent Plugins layout: `plugin.json` identifies it,
`skills/` supplies tasks, and `mcp.json` starts archguard. `POWER.md` belongs
to the older Power layout. Kiro manages a power's MCP server when it is
imported; the workspace MCP config below is for repositories that install
archguard directly.

1. Import the power: Powers panel, Add Custom Power, then Import power from a folder
   (select this `kiro/` directory). The power provides the skills, the MCP server
   (`mcp.json`) and steering (`dev.kiro/steering/`). Verify in Kiro that a
   clean-architecture prompt activates it and that `archguard_scan` responds.
2. Powers do not document a way to bundle hooks, so run the installer to write the hook
   file. Either ask for the `clean-guard-install` skill, or run it directly:

   ```sh
   kiro/install.sh <target-repo> [--source-root src] [--with-git-hook] [--with-claude] [--with-codex]
   ```

   It copies archguard to `<target>/tools/archguard/`, steering to `.kiro/steering/`,
   skills to `.kiro/skills/`, the agent to `.kiro/agents/`, the spec template to
   `.kiro/specs/monolith-to-clean/` (only if absent), the hook file to
   `.kiro/hooks/clean-guard.json` (commands rewritten to `tools/archguard`), a
   workspace MCP config to `.kiro/settings/mcp.json`, and runs `archguard init`
   when no `archguard.toml` exists. The MCP config uses an absolute path. After
   moving the target repository, rerun the installer and merge the `.new` MCP
   config it produces. The installer is idempotent and never
   overwrites a different file; it writes `<file>.new` and prints a notice.
   `--with-git-hook` installs `.githooks/pre-commit` and prints (does not run)
   `git config core.hooksPath .githooks`.

Hook exit semantics: exit 0 allows the tool call; exit 2 blocks a PreToolUse call and
sends stderr to the agent. The Stop hook cannot block; its command redirects the report
to stderr (`check 1>&2`) because a non-zero exit shows stderr to the user.

### Claude Code and Codex

The installer renders thin per-assistant files from the same sources, so every
assistant reads the same rules, uses the same skills and calls the same archguard
commands with JSON output. `render.py` does the rendering; it holds no rules.

```sh
kiro/install.sh <target> --with-claude --with-codex [--no-kiro]
```

| Piece | Source | Claude Code (`--with-claude`) | Codex (`--with-codex`) |
|---|---|---|---|
| Rules | `steering/*.md` | `AGENTS.md` block, plus `CLAUDE.md` block with `@AGENTS.md` | `AGENTS.md` block |
| Skills | `skills/` (copied unchanged, Agent Skills format) | `.claude/skills/` | `.agents/skills/` |
| Read-only reviewer | `agents/architecture-reviewer.md` | `.claude/agents/architecture-reviewer.md` | `.codex/agents/architecture-reviewer.toml` |
| MCP server | `archguard/mcp_server.py` | `.mcp.json` | `.codex/config.toml` |
| PreToolUse hook | `hooks/*.snippet.json` | `.claude/settings.json` | `.codex/hooks.json` |

- `AGENTS.md` and `CLAUDE.md` get a block between `<!-- archguard:begin -->` and
  `<!-- archguard:end -->`. The installer replaces only that block and keeps the rest
  of the file. Edit the steering sources and rerun instead of editing the block.
  File-scoped steering (`domain-purity.md`) becomes a section that names its file pattern.
- Claude Code reads `AGENTS.md` by itself only when no `CLAUDE.md` exists; the
  `@AGENTS.md` import covers both cases and does not load the file twice.
- Kiro also reads `AGENTS.md`, so a repository with Kiro steering and the `AGENTS.md`
  block gives Kiro the rules twice. Use `--no-kiro` for repositories where only Claude
  Code or Codex is used.
- Read-only means different things per host. Kiro denies `fs_write` and all shell
  commands except archguard. Codex runs the agent with `sandbox_mode = "read-only"`.
  Claude Code gets no write tools (`tools` allowlist plus `disallowedTools`), but keeps
  `Bash` to run archguard; the prompt limits it to archguard commands, which is an
  instruction, not a sandbox.
- Claude Code hook: matcher `Write|Edit|MultiEdit|Bash`, command
  `python3 "$CLAUDE_PROJECT_DIR"/tools/archguard/archguard.py hook --agent claude`.
  Claude Code asks you to approve the project MCP server on first use.
- Codex hook: matcher `apply_patch|Edit|Write|Bash`. Codex skips new or changed hooks
  until you trust them with `/hooks`, and loads `.codex/config.toml` only for trusted
  projects.
- MCP configs use an absolute script path, like the Kiro config. After moving the
  repository, rerun the installer and merge the `.new` files.

### CI and pre-commit

Copy `ci/github-actions-architecture.yml` to `.github/workflows/` and make the job a
required check. Install the pre-commit hook with `--with-git-hook`.

## Detection

- Skill: ask for a clean architecture review, or invoke `/clean-architecture-review`.
- Agent: switch to `architecture-reviewer` (read-only).
- CLI:

  ```sh
  python3 tools/archguard/archguard.py scan <path> --format md   # no config needed
  python3 tools/archguard/archguard.py check [PATHS...] [--format json|sarif]
  python3 tools/archguard/archguard.py score [PATHS...] [--format text|json|sarif] \
      [--min-portability N] [--min-cleanliness N]
  python3 tools/archguard/archguard.py staged                    # index content
  python3 tools/archguard/archguard.py init [--root DIR] [--source-root DIR]
  ```

  Exit codes: 0 clean, 1 violations (or a failed `score` gate), 2 blocked (hook),
  3 configuration or usage error.
- MCP: any MCP client can run `python3 tools/archguard/mcp_server.py` over stdio and
  call `archguard_scan` (`path`, `churn_days`), `archguard_check` (`paths`, `config`,
  `root`) or `archguard_score` (`paths`, `config`, `root`, `format`, `min_portability`,
  `min_cleanliness`). Pass absolute paths; the server's working directory is not the
  repository.

### Scores

`score` reads the same `archguard.toml` and the same governed files as `check`.

| Score | Formula |
|---|---|
| portability | percent of governed files with no AG001, AG002 or AG003 violation |
| cleanliness | percent of per-function checks (complexity, length, parameters) within the `[quality]` limits |

Both are 0 to 100 with one decimal, and 100 when there is nothing to measure. The
output contains no timestamps or absolute paths, so the same commit and configuration
give byte-identical JSON. Without gates, `score` exits 0. With `--min-portability` or
`--min-cleanliness` it exits 1 when a score is below the gate.

JSON (`--format json`, `schema_version` `"1.0"`) contains `scores`, `gates`, `passed`,
`findings` and `warnings`. Each finding has:

| Field | Meaning |
|---|---|
| `id` | 16 hex characters from rule, path and the offending import or function name; it does not change when lines move |
| `rule`, `name`, `dimension`, `severity` | for example `AG001`, `layer-import`, `portability`, `error` |
| `path`, `line` | path relative to the directory of `archguard.toml`; line 0 means unknown |
| `message`, `hint` | what is wrong and the fix pattern |
| `symbol`, `value`, `threshold` | AGQ rules only: qualified function name, measured value, limit |

SARIF (`--format sarif` on `check`, `staged` and `score`) is SARIF 2.1.0 with one run.
URIs are relative to the directory of `archguard.toml` with `uriBaseId` `%SRCROOT%`,
and `partialFingerprints["archguardFindingId/v1"]` carries the same `id` as the JSON.
`score` also records the two scores in `runs[0].properties.scores`. Put
`archguard.toml` at the repository root so the URIs line up with code-scanning tools.

`scan` classifies the tree (`hexagonal-clean`, `layered`, `mvc`, `monolith`, `mixed`),
reports a coupling score (0 is fully decoupled), a boundary map, a Mermaid dependency
graph with violating edges in red, git churn, and a six-move refactor plan. The
classification rules are printed in every report.

## Tests

```sh
env -i PATH="$PATH" HOME="$HOME" python3 -m pytest kiro/archguard/tests -q
```

Offline; uses temporary directories and a temporary git repository.

## Notes and sources

- The Kiro write-tool stdin field names (`fs_write` with `path`/`text`, `fs_append`,
  `str_replace` with `oldStr`/`newStr`/`replace_all`, `execute_bash` with `command`)
  were captured empirically on 26 Sep 2026. They are not documented. archguard also
  accepts the CLI-style names (`write`, `file_text`, `old_str`, `new_str`) defensively.
- Kiro hooks: https://kiro.dev/docs/hooks and https://kiro.dev/docs/hooks/types
- Kiro steering: https://kiro.dev/docs/steering
- Kiro skills: https://kiro.dev/docs/skills
- Kiro powers: https://kiro.dev/docs/powers
- Kiro power creation and installation: https://kiro.dev/docs/powers/create/
  and https://kiro.dev/docs/powers/installation/
- Kiro custom agents: https://kiro.dev/docs/custom-agents
- Claude Code hooks: https://code.claude.com/docs/en/hooks
- Codex hooks: https://learn.chatgpt.com/docs/hooks
