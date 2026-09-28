#!/usr/bin/env bash
# Install the clean-architecture enforcement layer into a target repository.
#
#   kiro/install.sh <target-repo-dir> [--source-root <dir>] [--with-git-hook]
#                   [--with-claude] [--with-codex] [--no-kiro]
#
# Always: archguard in tools/archguard and archguard.toml.
# Kiro (default, skip with --no-kiro): .kiro/ steering, skills, agent, spec, hooks, MCP.
# --with-claude: .claude/ skills, agent, hooks; .mcp.json; AGENTS.md and CLAUDE.md blocks.
# --with-codex:  .agents/skills, .codex/ agent, hooks, config.toml; AGENTS.md block.
# Rules for Claude Code and Codex are rendered from steering/*.md by render.py, so
# every assistant reads the same rules and calls the same archguard commands.
#
# Idempotent. An existing file with different content is never overwritten:
# the new version is written next to it as <file>.new and a notice is printed.
set -euo pipefail

usage() {
    echo "usage: $0 <target-repo-dir> [--source-root <dir>] [--with-git-hook] [--with-claude] [--with-codex] [--no-kiro]" >&2
    exit 2
}

[ "$#" -ge 1 ] || usage
TARGET=""
SOURCE_ROOT=""
WITH_GIT_HOOK=0
WITH_CLAUDE=0
WITH_CODEX=0
WITH_KIRO=1
while [ "$#" -gt 0 ]; do
    case "$1" in
        --source-root)
            [ "$#" -ge 2 ] || usage
            SOURCE_ROOT="$2"
            shift 2
            ;;
        --with-git-hook) WITH_GIT_HOOK=1; shift ;;
        --with-claude) WITH_CLAUDE=1; shift ;;
        --with-codex) WITH_CODEX=1; shift ;;
        --no-kiro) WITH_KIRO=0; shift ;;
        -h|--help) usage ;;
        -*) echo "unknown option: $1" >&2; usage ;;
        *)
            [ -z "$TARGET" ] || usage
            TARGET="$1"
            shift
            ;;
    esac
done
[ -n "$TARGET" ] || usage
[ -d "$TARGET" ] || { echo "target is not a directory: $TARGET" >&2; exit 2; }

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="$(cd "$TARGET" && pwd)"
PYTHON="${PYTHON:-python3}"
TOOLS_REL="tools/archguard"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/archguard-install.XXXXXXXX")"
trap 'rm -rf "$TMP"' EXIT

CREATED=0
UNCHANGED=0
CONFLICTS=0
UPDATED=0
RENDER=("$PYTHON" "$SRC/render.py")

# install_file <source> <destination relative to target> [mode]
install_file() {
    local src="$1" rel="$2" mode="${3:-}"
    local dst="$TARGET/$rel"
    mkdir -p "$(dirname "$dst")"
    if [ ! -e "$dst" ]; then
        cp "$src" "$dst"
        [ -z "$mode" ] || chmod "$mode" "$dst"
        echo "created   $rel"
        CREATED=$((CREATED + 1))
    elif cmp -s "$src" "$dst"; then
        echo "unchanged $rel"
        UNCHANGED=$((UNCHANGED + 1))
    else
        cp "$src" "$dst.new"
        [ -z "$mode" ] || chmod "$mode" "$dst.new"
        echo "NOTICE    $rel exists with different content; wrote $rel.new (merge it by hand)"
        CONFLICTS=$((CONFLICTS + 1))
    fi
}

# rewrite_paths <source> <output>: point hook commands at the installed archguard
rewrite_paths() {
    sed "s#kiro/archguard/#$TOOLS_REL/#g" "$1" > "$2"
}

# install_skills <destination dir relative to target>
# clean-guard-install is only meaningful inside the power itself.
install_skills() {
    local dest="$1" f rel mode
    while IFS= read -r -d '' f; do
        rel="${f#"$SRC"/skills/}"
        case "$rel" in clean-guard-install/*) continue ;; esac
        mode=""
        [ -x "$f" ] && mode="755"
        install_file "$f" "$dest/$rel" "$mode"
    done < <(find "$SRC/skills" -type f ! -name '*.pyc' ! -path '*/__pycache__/*' -print0 | sort -z)
}

# upsert_block <file relative to target> <content file> [header]
# Replaces only the text between the archguard markers; the rest of the file is kept.
upsert_block() {
    local rel="$1" content="$2" header="${3:-}" status
    status="$("${RENDER[@]}" block "$TARGET/$rel" "$content" --header "$header")"
    case "$status" in
        created) echo "created   $rel (archguard block)"; CREATED=$((CREATED + 1)) ;;
        updated) echo "updated   $rel (archguard block)"; UPDATED=$((UPDATED + 1)) ;;
        *) echo "unchanged $rel (archguard block)"; UNCHANGED=$((UNCHANGED + 1)) ;;
    esac
}

# 1. archguard (tests are not copied)
for f in archguard.py mcp_server.py archguard.example.toml; do
    install_file "$SRC/archguard/$f" "$TOOLS_REL/$f"
done

# 2-7. Kiro: steering, skills, agent, spec template, hooks, MCP
if [ "$WITH_KIRO" -eq 1 ]; then
    # 2. steering
    for f in "$SRC"/steering/*.md; do
        rewrite_paths "$f" "$TMP/steering.md"
        install_file "$TMP/steering.md" ".kiro/steering/$(basename "$f")"
    done

    # 3. skills
    install_skills ".kiro/skills"

    # 4. custom agent
    for f in "$SRC"/agents/*.md; do
        rewrite_paths "$f" "$TMP/agent.md"
        install_file "$TMP/agent.md" ".kiro/agents/$(basename "$f")"
    done

    # 5. spec template, only if absent
    if [ -e "$TARGET/.kiro/specs/monolith-to-clean" ]; then
        echo "unchanged .kiro/specs/monolith-to-clean (already present, not touched)"
    else
        for f in "$SRC"/specs/monolith-to-clean/*.md; do
            rewrite_paths "$f" "$TMP/spec.md"
            install_file "$TMP/spec.md" ".kiro/specs/monolith-to-clean/$(basename "$f")"
        done
    fi

    # 6. Kiro hook file
    rewrite_paths "$SRC/hooks/kiro-clean-guard.json" "$TMP/clean-guard.json"
    install_file "$TMP/clean-guard.json" ".kiro/hooks/clean-guard.json"

    # 7. Workspace MCP server. Use an absolute script path because MCP clients
    #    need not start the server from the target repository.
    "$PYTHON" - "$TARGET/$TOOLS_REL/mcp_server.py" "$TMP/mcp.json" <<'PY'
import json
import sys

script, output = sys.argv[1:]
with open(output, "w", encoding="utf-8") as file:
    json.dump(
        {
            "mcpServers": {
                "archguard": {
                    "command": "python3",
                    "args": [script],
                    "disabled": False,
                    "autoApprove": [],
                }
            }
        },
        file,
        indent=2,
    )
    file.write("\n")
PY
    install_file "$TMP/mcp.json" ".kiro/settings/mcp.json"
fi

# 8. archguard.toml
if [ -e "$TARGET/archguard.toml" ]; then
    echo "unchanged archguard.toml (exists, not touched)"
else
    init_args=(init --root "$TARGET")
    [ -z "$SOURCE_ROOT" ] || init_args+=(--source-root "$SOURCE_ROOT")
    "$PYTHON" "$TARGET/$TOOLS_REL/archguard.py" "${init_args[@]}"
fi

# 9. optional integrations
if [ "$WITH_GIT_HOOK" -eq 1 ]; then
    rewrite_paths "$SRC/hooks/git-pre-commit" "$TMP/pre-commit"
    install_file "$TMP/pre-commit" ".githooks/pre-commit" 755
    echo "To enable the pre-commit hook, run in $TARGET:"
    echo "    git config core.hooksPath .githooks"
fi
MCP_SCRIPT="$TARGET/$TOOLS_REL/mcp_server.py"
AGENT_SRC="$SRC/agents/architecture-reviewer.md"
if [ "$WITH_CLAUDE" -eq 1 ] || [ "$WITH_CODEX" -eq 1 ]; then
    # Shared rules: Codex reads AGENTS.md; Claude Code reads it directly or via CLAUDE.md.
    "${RENDER[@]}" rules "$SRC/steering" "$TOOLS_REL" "$TMP/rules.md"
    upsert_block "AGENTS.md" "$TMP/rules.md" "# AGENTS.md"
fi
if [ "$WITH_CLAUDE" -eq 1 ]; then
    rewrite_paths "$SRC/hooks/claude-settings.snippet.json" "$TMP/claude.json"
    install_file "$TMP/claude.json" ".claude/settings.json"
    install_skills ".claude/skills"
    "${RENDER[@]}" claude-agent "$AGENT_SRC" "$TOOLS_REL" "$TMP/claude-agent.md"
    install_file "$TMP/claude-agent.md" ".claude/agents/architecture-reviewer.md"
    "${RENDER[@]}" claude-mcp "$MCP_SCRIPT" "$TMP/claude-mcp.json"
    install_file "$TMP/claude-mcp.json" ".mcp.json"
    # Claude Code stops reading AGENTS.md on its own once a CLAUDE.md exists; the
    # import keeps one shared rules file either way and never loads it twice.
    printf '@AGENTS.md\n' > "$TMP/claude-import.md"
    upsert_block "CLAUDE.md" "$TMP/claude-import.md" "# CLAUDE.md"
    echo "Claude Code asks you to approve the project MCP server in .mcp.json on first use."
fi
if [ "$WITH_CODEX" -eq 1 ]; then
    rewrite_paths "$SRC/hooks/codex-hooks.snippet.json" "$TMP/codex.json"
    install_file "$TMP/codex.json" ".codex/hooks.json"
    install_skills ".agents/skills"
    "${RENDER[@]}" codex-agent "$AGENT_SRC" "$TOOLS_REL" "$TMP/codex-agent.toml"
    install_file "$TMP/codex-agent.toml" ".codex/agents/architecture-reviewer.toml"
    "${RENDER[@]}" codex-mcp "$MCP_SCRIPT" "$TMP/codex-config.toml"
    install_file "$TMP/codex-config.toml" ".codex/config.toml"
    echo "Codex skips new hooks until you review and trust them with /hooks, and loads"
    echo ".codex/config.toml (the archguard MCP server) only for trusted projects."
fi

echo "done: $CREATED created, $UNCHANGED unchanged, $CONFLICTS written as .new, $UPDATED updated"
echo "next: python3 $TOOLS_REL/archguard.py check   (from $TARGET)"
