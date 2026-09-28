---
name: clean-guard-install
description: Install the clean-architecture guard into the current repository for Kiro, Claude Code and Codex. Copies archguard to tools/archguard, creates archguard.toml, and writes each assistant's hooks, rules, skills, read-only reviewer agent and MCP config. Use when asked to install, enable or set up architecture enforcement, boundary hooks or archguard in a repository.
---

# Install the clean guard

Powers do not document a way to bundle hooks, so this skill runs the installer that
writes the hook file.

## Step 1: Confirm the target

The target is the workspace root unless the user names another repository. Tell the
user the installer writes under `tools/archguard/`, `.kiro/` (including
`.kiro/settings/mcp.json`) and `archguard.toml`. With `--with-claude` it also writes
`.claude/`, `.mcp.json` and a marked block in `CLAUDE.md`; with `--with-codex` it
writes `.agents/skills/` and `.codex/`. Both add a marked block to `AGENTS.md`. It
never overwrites a different existing file (it writes `<file>.new` instead) and
changes only the text between the `archguard` markers in `AGENTS.md` and `CLAUDE.md`.

## Step 2: Run the installer

`install.sh` is two directories above this skill folder (the power root).

```sh
bash ../../install.sh <target-repo-dir> [--source-root <dir>] [--with-git-hook] [--with-claude] [--with-codex] [--no-kiro]
```

- `--source-root`: the directory that contains `domain/`, `application/`,
  `infrastructure/` (detected when omitted).
- `--with-git-hook`: installs `.githooks/pre-commit` and prints the
  `git config core.hooksPath .githooks` command. Do not run that command yourself;
  show it to the user.
- `--with-claude`, `--with-codex`: also install for Claude Code and Codex (hooks,
  rules, skills, reviewer agent, MCP). Ask which assistants the team uses.
- `--no-kiro`: skip the `.kiro/` files.

## Step 3: Verify

```sh
cd <target-repo-dir>
python3 tools/archguard/archguard.py check --format json
python3 tools/archguard/archguard.py score --format json
```

Report the violation count and both scores from the JSON. Existing violations are expected in brownfield code; offer
the `clean-architecture-review` skill for a plan. List any `.new` files the installer
reported so the user can merge them.
