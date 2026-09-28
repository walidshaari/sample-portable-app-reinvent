"""Checks for the packaged power: manifests, front matter, hooks, installer, scaffold."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

KIRO = Path(__file__).resolve().parents[2]
FIX = Path(__file__).resolve().parent / "fixtures"
TEXT_SUFFIXES = {".md", ".py", ".json", ".toml", ".yml", ".yaml", ".sh", ".txt", ""}


def env() -> dict:
    return {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")}


def front_matter(path: Path) -> dict[str, str]:
    text = path.read_text()
    assert text.startswith("---\n"), path
    block = text[4:text.index("\n---\n", 4)]
    out = {}
    for line in block.splitlines():
        m = re.match(r"^([A-Za-z]\w*):\s*(.*)$", line)
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


def test_plugin_manifest_matches_agent_plugins_schema():
    data = json.loads((KIRO / "plugin.json").read_text())
    allowed = {"$schema", "name", "version", "description", "author", "homepage", "repository",
               "license", "keywords", "extensions"}
    assert set(data) <= allowed
    assert data["$schema"] == "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
    assert re.fullmatch(r"(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", data["name"])
    assert data["name"] == "clean-architecture-guard"
    assert set(data["author"]) <= {"name", "email", "url"} and data["author"]["name"] == "Sample maintainers"
    assert all(isinstance(k, str) for k in data["keywords"]) and data["keywords"]
    mcp = json.loads((KIRO / "mcp.json").read_text())
    assert set(mcp) == {"$schema", "mcpServers"}
    server = mcp["mcpServers"]["archguard"]
    assert server["type"] == "stdio" and server["args"] == ["${PLUGIN_ROOT}/archguard/mcp_server.py"]


def test_skills_front_matter():
    dirs = sorted(p for p in (KIRO / "skills").iterdir() if p.is_dir())
    assert [d.name for d in dirs] == ["boundary-violation-fix", "clean-architecture-review",
                                      "clean-guard-install", "clean-service-scaffold"]
    for d in dirs:
        fm = front_matter(d / "SKILL.md")
        assert fm["name"] == d.name
        assert re.fullmatch(r"[a-z0-9-]{1,64}", fm["name"])
        assert 0 < len(fm["description"]) <= 1024


def test_steering_front_matter_and_power_copy():
    always = (KIRO / "steering/clean-architecture.md").read_text()
    assert always.startswith("---\ninclusion: always\n---\n")
    domain = (KIRO / "steering/domain-purity.md").read_text()
    assert domain.startswith('---\ninclusion: fileMatch\nfileMatchPattern: "**/domain/**"\n---\n')
    for name in ("clean-architecture.md", "domain-purity.md"):
        assert (KIRO / "dev.kiro/steering" / name).read_text() == (KIRO / "steering" / name).read_text()


def test_agent_front_matter():
    fm = front_matter(KIRO / "agents/architecture-reviewer.md")
    assert fm["name"] == "architecture-reviewer"
    text = (KIRO / "agents/architecture-reviewer.md").read_text()
    assert "capability: fs_write" in text and "effect: deny" in text
    assert '"write"' not in fm["tools"]


def test_hook_templates():
    k = json.loads((KIRO / "hooks/kiro-clean-guard.json").read_text())
    assert k["version"] == "v1"
    names = [(h["name"], h["trigger"], h.get("matcher"), h["action"]["command"],
              h["timeout"]) for h in k["hooks"]]
    assert all("timeout" not in h["action"] for h in k["hooks"])
    assert names == [
        ("Block boundary violations before write", "PreToolUse", "fs_write|str_replace|fs_append|write",
         "python3 kiro/archguard/archguard.py hook --agent kiro", 20),
        ("Block commits that break boundaries", "PreToolUse", "execute_bash|shell",
         "python3 kiro/archguard/archguard.py hook --agent kiro", 20),
        ("Architecture check when the agent stops", "Stop", None,
         "python3 kiro/archguard/archguard.py check 1>&2", 30),
    ]
    c = json.loads((KIRO / "hooks/claude-settings.snippet.json").read_text())
    assert "--agent claude" in c["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    x = json.loads((KIRO / "hooks/codex-hooks.snippet.json").read_text())
    assert x["hooks"]["PreToolUse"][0]["matcher"] == "apply_patch|Edit|Write|Bash"
    assert subprocess.run(["sh", "-n", str(KIRO / "hooks/git-pre-commit")]).returncode == 0
    assert subprocess.run(["bash", "-n", str(KIRO / "install.sh")]).returncode == 0


def test_text_files_have_no_em_or_en_dashes():
    bad = []
    for p in KIRO.rglob("*"):
        if not p.is_file() or "__pycache__" in p.parts or ".pytest_cache" in p.parts:
            continue
        if p.suffix not in TEXT_SUFFIXES:
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        if "\u2014" in text or "\u2013" in text:
            bad.append(str(p))
    assert bad == []


def run_install(target: Path, *flags: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(KIRO / "install.sh"), str(target), *flags],
                          capture_output=True, text=True, env=env(), timeout=120)


def test_install_is_idempotent_and_never_overwrites(tmp_path):
    target = tmp_path / "target"
    shutil.copytree(FIX / "clean" / "src", target / "src")
    first = run_install(target, "--with-git-hook", "--with-claude", "--with-codex")
    assert first.returncode == 0, first.stderr
    assert "git config core.hooksPath .githooks" in first.stdout
    assert not (target / ".git").exists()
    hooks = json.loads((target / ".kiro/hooks/clean-guard.json").read_text())
    assert all("tools/archguard/archguard.py" in h["action"]["command"] for h in hooks["hooks"])
    mcp = json.loads((target / ".kiro/settings/mcp.json").read_text())
    assert mcp["mcpServers"]["archguard"]["args"] == [
        str(target / "tools/archguard/mcp_server.py")
    ]
    assert mcp["mcpServers"]["archguard"]["disabled"] is False
    assert "tools/archguard/mcp_server.py" in (target / ".kiro/agents/architecture-reviewer.md").read_text()
    assert "tools/archguard/archguard.py" in (target / ".claude/settings.json").read_text()
    assert "tools/archguard/archguard.py" in (target / ".codex/hooks.json").read_text()
    assert os.access(target / ".githooks/pre-commit", os.X_OK)
    assert os.access(target / ".kiro/skills/clean-service-scaffold/scripts/new_service.py", os.X_OK)
    assert not (target / ".kiro/skills/clean-guard-install").exists()
    assert (target / ".kiro/specs/monolith-to-clean/tasks.md").is_file()
    assert (target / "archguard.toml").is_file()
    check = subprocess.run([sys.executable, "tools/archguard/archguard.py", "check"], cwd=str(target),
                           capture_output=True, text=True, env=env())
    assert check.returncode == 0, check.stdout

    second = run_install(target, "--with-git-hook", "--with-claude", "--with-codex")
    assert second.returncode == 0
    assert "0 created" in second.stdout and "0 written as .new" in second.stdout

    (target / ".kiro/steering/clean-architecture.md").write_text("local edits\n")
    third = run_install(target)
    assert "NOTICE" in third.stdout
    assert (target / ".kiro/steering/clean-architecture.md").read_text() == "local edits\n"
    assert (target / ".kiro/steering/clean-architecture.md.new").is_file()


def test_pre_commit_hook_blocks_staged_violation(tmp_path):
    target = tmp_path / "target"
    shutil.copytree(FIX / "clean" / "src", target / "src")
    assert run_install(target, "--with-git-hook").returncode == 0
    subprocess.run(["git", "init", "-q"], cwd=str(target), check=True, env=env())
    (target / "src/domain/note.py").write_text("import boto3\n")
    subprocess.run(["git", "add", "src/domain/note.py"], cwd=str(target), check=True, env=env())
    hook = subprocess.run(["sh", ".githooks/pre-commit"], cwd=str(target), capture_output=True, text=True, env=env())
    assert hook.returncode == 1
    assert "AG001" in hook.stdout and "commit refused" in hook.stderr


@pytest.mark.parametrize("entity", ["Note", "Category"])
def test_scaffold_generates_passing_service(tmp_path, entity):
    script = KIRO / "skills/clean-service-scaffold/scripts/new_service.py"
    gen = subprocess.run([sys.executable, str(script), "demo-svc", str(tmp_path), "--entity", entity],
                         capture_output=True, text=True, env=env())
    assert gen.returncode == 0, gen.stderr
    root = tmp_path / "demo-svc"
    tests = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=str(root), capture_output=True,
                           text=True, env=env(), timeout=120)
    assert tests.returncode == 0, tests.stdout + tests.stderr
    check = subprocess.run([sys.executable, "tools/archguard/archguard.py", "check"], cwd=str(root),
                           capture_output=True, text=True, env=env())
    assert check.returncode == 0, check.stdout
    assert (root / ".kiro/steering/domain-purity.md").is_file()
    dockerfile = (root / "Dockerfile").read_text()
    assert "ARG PYTHON_BASE_IMAGE\n" in dockerfile and "USER 65532:65532" in dockerfile


# ---------------------------------------------------------------------------
# Cross-assistant rendering (render.py) and installer flags
# ---------------------------------------------------------------------------

sys.path.insert(0, str(KIRO))
import render  # noqa: E402


def test_render_rules_block_has_all_steering_and_json_commands():
    text = render.render_rules(KIRO / "steering", "tools/archguard")
    assert "kiro/archguard/" not in text
    assert "### Clean architecture rules" in text and "#### 1. Dependency rule (AG001)" in text
    assert "Applies when you edit files matching `**/domain/**`." in text
    assert "### Domain purity" in text
    for cmd in ("check --format json", "score --format json", "score --format sarif",
                "scan <dir> --format json"):
        assert f"python3 tools/archguard/archguard.py {cmd}" in text
    assert "inclusion:" not in text and "fileMatchPattern" not in text
    assert text == render.render_rules(KIRO / "steering", "tools/archguard")


def test_demote_headings_skips_code_fences():
    md = "# Title\n```sh\n# comment\n```\n## Sub\n"
    assert render.demote_headings(md, 1) == "## Title\n```sh\n# comment\n```\n### Sub"


def test_upsert_block_preserves_surrounding_text(tmp_path):
    f = tmp_path / "AGENTS.md"
    assert render.upsert_block(f, "one", "# AGENTS.md") == "created"
    assert f.read_text() == f"# AGENTS.md\n\n{render.BEGIN}\none\n{render.END}\n"
    f.write_text("# Mine\n\nkeep me\n")
    assert render.upsert_block(f, "one") == "updated"
    assert render.upsert_block(f, "one") == "unchanged"
    f.write_text(f.read_text() + "\ntrailer\n")
    assert render.upsert_block(f, "two") == "updated"
    text = f.read_text()
    assert text.startswith("# Mine\n\nkeep me\n\n") and text.endswith("\ntrailer\n")
    assert "two" in text and "one" not in text and text.count(render.BEGIN) == 1
    f.write_text(f"{render.BEGIN}\nno end\n")
    with pytest.raises(ValueError):
        render.upsert_block(f, "x")


def test_render_claude_and_codex_agents():
    import tomllib
    src = KIRO / "agents/architecture-reviewer.md"
    claude = render.render_claude_agent(src, "tools/archguard")
    fm = front_matter_text(claude)
    assert fm["name"] == "architecture-reviewer"
    assert json.loads(fm["description"]).startswith("Read-only clean-architecture reviewer.")
    tools = [t.strip() for t in fm["tools"].split(",")]
    assert "Write" not in tools and "Edit" not in tools and "mcp__archguard__archguard_score" in tools
    assert {"Write", "Edit", "MultiEdit", "NotebookEdit"} <= {t.strip() for t in fm["disallowedTools"].split(",")}
    assert "tools/archguard/archguard.py score --format json" in claude
    assert "kiro/archguard/" not in claude and "switching to the default agent" not in claude
    codex = tomllib.loads(render.render_codex_agent(src, "tools/archguard"))
    assert set(codex) == {"name", "description", "sandbox_mode", "developer_instructions"}
    assert codex["name"] == "architecture_reviewer" and codex["sandbox_mode"] == "read-only"
    assert "score --format json" in codex["developer_instructions"]
    assert "kiro/archguard/" not in codex["developer_instructions"]


def test_render_mcp_configs():
    import tomllib
    claude = json.loads(render.render_claude_mcp("/abs/tools/archguard/mcp_server.py"))
    assert claude == {"mcpServers": {"archguard": {"type": "stdio", "command": "python3",
                                                   "args": ["/abs/tools/archguard/mcp_server.py"]}}}
    codex = tomllib.loads(render.render_codex_mcp('/abs/with "quote"/mcp_server.py'))
    server = codex["mcp_servers"]["archguard"]
    assert server["args"] == ['/abs/with "quote"/mcp_server.py'] and server["command"] == "python3"
    assert server["enabled_tools"] == ["archguard_scan", "archguard_check", "archguard_score"]


def front_matter_text(text: str) -> dict[str, str]:
    block = text[4:text.index("\n---\n", 4)]
    out = {}
    for line in block.splitlines():
        m = re.match(r"^([A-Za-z]\w*):\s*(.*)$", line)
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


def test_install_claude_and_codex_outputs(tmp_path):
    import tomllib
    target = tmp_path / "target"
    shutil.copytree(FIX / "clean" / "src", target / "src")
    (target / "AGENTS.md").write_text("# Team rules\n\nKeep this.\n")
    first = run_install(target, "--with-claude", "--with-codex")
    assert first.returncode == 0, first.stderr
    assert "updated   AGENTS.md (archguard block)" in first.stdout
    agents_md = (target / "AGENTS.md").read_text()
    assert agents_md.startswith("# Team rules\n\nKeep this.\n\n" + render.BEGIN)
    assert "tools/archguard/archguard.py score --format json" in agents_md
    assert (target / "CLAUDE.md").read_text() == f"# CLAUDE.md\n\n{render.BEGIN}\n@AGENTS.md\n{render.END}\n"
    expected = ["boundary-violation-fix", "clean-architecture-review", "clean-service-scaffold"]
    for skills in (".claude/skills", ".agents/skills"):
        assert sorted(p.name for p in (target / skills).iterdir()) == expected
        for name in expected:
            assert (target / skills / name / "SKILL.md").read_text() == \
                (KIRO / "skills" / name / "SKILL.md").read_text()
        assert os.access(target / skills / "clean-service-scaffold/scripts/new_service.py", os.X_OK)
    assert (target / ".claude/agents/architecture-reviewer.md").read_text().startswith(
        "---\nname: architecture-reviewer\n")
    codex_agent = tomllib.loads((target / ".codex/agents/architecture-reviewer.toml").read_text())
    assert codex_agent["sandbox_mode"] == "read-only"
    script = str(target / "tools/archguard/mcp_server.py")
    assert json.loads((target / ".mcp.json").read_text())["mcpServers"]["archguard"]["args"] == [script]
    codex_cfg = tomllib.loads((target / ".codex/config.toml").read_text())
    assert codex_cfg["mcp_servers"]["archguard"]["args"] == [script]
    for rel in ("AGENTS.md", ".claude/agents/architecture-reviewer.md",
                ".codex/agents/architecture-reviewer.toml", ".kiro/steering/clean-architecture.md",
                ".kiro/specs/monolith-to-clean/tasks.md"):
        assert "kiro/archguard/" not in (target / rel).read_text(), rel
    # the rendered MCP server answers for both clients
    init = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    out = subprocess.run([sys.executable, script], input=json.dumps(init) + "\n", capture_output=True,
                         text=True, env=env(), timeout=60)
    names = [t["name"] for t in json.loads(out.stdout)["result"]["tools"]]
    assert names == ["archguard_scan", "archguard_check", "archguard_score"]
    second = run_install(target, "--with-claude", "--with-codex")
    assert second.returncode == 0
    assert re.search(r"^done: 0 created, \d+ unchanged, 0 written as \.new, 0 updated$",
                     second.stdout, re.M), second.stdout
    (target / "AGENTS.md").write_text((target / "AGENTS.md").read_text().replace(
        "Keep this.", "Keep this, edited."))
    third = run_install(target, "--with-codex")
    assert "unchanged AGENTS.md (archguard block)" in third.stdout
    assert "Keep this, edited." in (target / "AGENTS.md").read_text()


def test_install_no_kiro_writes_no_kiro_files(tmp_path):
    target = tmp_path / "target"
    shutil.copytree(FIX / "clean" / "src", target / "src")
    r = run_install(target, "--no-kiro", "--with-codex")
    assert r.returncode == 0, r.stderr
    assert not (target / ".kiro").exists() and not (target / ".claude").exists()
    assert not (target / "CLAUDE.md").exists()
    assert (target / ".agents/skills/clean-architecture-review/SKILL.md").is_file()
    assert (target / "tools/archguard/archguard.py").is_file() and (target / "archguard.toml").is_file()
    score = subprocess.run([sys.executable, "tools/archguard/archguard.py", "score", "--format", "json"],
                           cwd=str(target), capture_output=True, text=True, env=env())
    assert score.returncode == 0 and json.loads(score.stdout)["scores"]["portability"]["score"] == 100.0
