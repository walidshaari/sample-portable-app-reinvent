"""Offline tests for archguard.py. No network, no cloud, no installs."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
AG_DIR = HERE.parent
AG = AG_DIR / "archguard.py"
FIX = HERE / "fixtures"
sys.path.insert(0, str(AG_DIR))

import archguard as ag  # noqa: E402

CORE = ('"typing", "dataclasses", "re", "uuid", "datetime", "decimal", "enum", "abc", '
        '"__future__", "collections", "functools", "itertools", "math", "numbers", "string"')
CONFIG = f"""
[project]
source_roots = ["src"]
[layers.domain]
paths = ["domain"]
allow = ["domain", {CORE}]
[layers.application]
paths = ["application"]
allow = ["domain", "application", {CORE}]
[layers.infrastructure]
paths = ["infrastructure"]
allow = ["*"]
[rules]
composition_roots = ["infrastructure/composition.py", "infrastructure/config.py"]
adapter_packages = ["infrastructure.repositories"]
config_read_modules = ["dotenv", "decouple", "environs"]
"""


def clean_env() -> dict:
    return {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")}


def make_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "repo"
    root.mkdir(exist_ok=True)
    (root / "archguard.toml").write_text(CONFIG)
    (root / "src").mkdir(exist_ok=True)
    for rel, text in files.items():
        p = root / "src" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


def check(root: Path) -> list[tuple[str, str, int, str]]:
    cfg = ag.load_config(root / "archguard.toml")
    rep = ag.run_check(cfg, None, root)
    return [(v.rule, v.path, v.line, v.target) for v in rep.violations]


def cli(args: list[str], cwd: Path, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(AG), *args], cwd=str(cwd), input=stdin,
                          capture_output=True, text=True, env=clean_env(), timeout=60)


def hook(payload, cwd: Path, agent: str = "kiro") -> tuple[int, str]:
    """Run hook mode as the agent would: a subprocess fed JSON on stdin."""
    text = payload if isinstance(payload, str) else json.dumps(payload)
    p = cli(["hook", "--agent", agent], cwd, stdin=text)
    assert p.stdout == "", "hook mode must never write to stdout"
    return p.returncode, p.stderr


# ---------------------------------------------------------------------------
# AG001 layer imports
# ---------------------------------------------------------------------------


def test_ag001_import_in_domain(tmp_path):
    root = make_repo(tmp_path, {"domain/user.py": "import re\nimport boto3\n"})
    assert check(root) == [("AG001", "src/domain/user.py", 2, "boto3")]


def test_ag001_from_import_in_application(tmp_path):
    root = make_repo(tmp_path, {"application/use_cases/create.py": "from fastapi import HTTPException\n"})
    assert check(root) == [("AG001", "src/application/use_cases/create.py", 1, "fastapi")]


def test_ag001_relative_imports_are_resolved(tmp_path):
    root = make_repo(tmp_path, {
        "application/use_cases/create.py": (
            "from ..ports.user_repository import UserRepository\n"
            "from ...infrastructure.repositories import memory\n"
        ),
        "application/ports/user_repository.py": "from abc import ABC\nclass UserRepository(ABC): pass\n",
        "domain/order.py": "from . import user\nfrom .user import User\n",
        "domain/user.py": "class User: pass\n",
    })
    found = check(root)
    assert ("AG001", "src/application/use_cases/create.py", 2, "infrastructure.repositories") in found
    assert ("AG003", "src/application/use_cases/create.py", 2, "infrastructure.repositories") in found
    assert all(v[2] != 1 for v in found if v[1].endswith("create.py"))
    assert not [v for v in found if "domain/" in v[1]]


def test_ag001_inward_hint_for_outer_layer_import(tmp_path):
    root = make_repo(tmp_path, {"domain/user.py": "from application.ports import x\n"})
    rep = ag.run_check(ag.load_config(root / "archguard.toml"), None, root)
    assert rep.violations[0].hint == ag.HINT_INWARD


def test_ag001_sdk_hint(tmp_path):
    root = make_repo(tmp_path, {"domain/user.py": "import boto3\n"})
    rep = ag.run_check(ag.load_config(root / "archguard.toml"), None, root)
    assert rep.violations[0].hint == ag.HINT_PORT


def test_ag001_importlib_and_dunder_import(tmp_path):
    root = make_repo(tmp_path, {
        "domain/user.py": (
            "import importlib\n"
            "m = importlib.import_module('requests')\n"
            "n = __import__('boto3')\n"
            "from importlib import import_module\n"
            "o = import_module('re')\n"
        ),
    })
    found = check(root)
    assert ("AG001", "src/domain/user.py", 1, "importlib") in found
    assert ("AG001", "src/domain/user.py", 2, "requests") in found
    assert ("AG001", "src/domain/user.py", 3, "boto3") in found
    assert ("AG001", "src/domain/user.py", 4, "importlib") in found
    # the call itself is flagged even for an allowlisted target, because importlib is not allowed
    assert ("AG001", "src/domain/user.py", 5, "re") in found


def test_allowlisted_stdlib_passes(tmp_path):
    src = "\n".join(
        ["from __future__ import annotations"]
        + [f"import {m}" for m in ["typing", "dataclasses", "re", "uuid", "datetime", "decimal", "enum",
                                    "abc", "collections", "functools", "itertools", "math", "numbers", "string"]]
        + ["from collections.abc import Mapping", "from domain.order import Order", "from . import order"]
    ) + "\n"
    root = make_repo(tmp_path, {"domain/user.py": src, "domain/order.py": "class Order: pass\n"})
    assert check(root) == []


def test_non_allowlisted_stdlib_is_denied(tmp_path):
    root = make_repo(tmp_path, {"domain/user.py": "import os\nimport sys\n"})
    assert [v[3] for v in check(root)] == ["os", "sys"]


def test_infrastructure_allows_everything(tmp_path):
    root = make_repo(tmp_path, {"infrastructure/http/app.py": "import fastapi\nimport boto3\n"})
    assert check(root) == []


def test_resolve_relative():
    assert ag.resolve_relative("application.use_cases.x", False, 2, "ports") == "application.ports"
    assert ag.resolve_relative("domain.user", False, 1, None) == "domain"
    assert ag.resolve_relative("domain", True, 1, "user") == "domain.user"
    assert ag.resolve_relative("x", False, 3, "y").startswith("...")


# ---------------------------------------------------------------------------
# AG002 config reads
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("src,what", [
    ("import os\nX = os.environ['A']\n", "os.environ"),
    ("import os\nX = os.getenv('A')\n", "os.getenv"),
    ("import os as o\nX = o.environ.get('A')\n", "os.environ"),
    ("from os import environ\n", "from os import environ"),
    ("from os import getenv as g\n", "from os import getenv"),
    ("from dotenv import load_dotenv\n", "dotenv"),
    ("import decouple\n", "decouple"),
])
def test_ag002_config_reads(tmp_path, src, what):
    root = make_repo(tmp_path, {"infrastructure/http/app.py": src})
    found = check(root)
    assert [v[0] for v in found] == ["AG002"]
    assert found[0][3] == what


def test_ag002_composition_root_is_exempt(tmp_path):
    src = "import os\nfrom dotenv import load_dotenv\nX = os.environ.get('A')\nY = os.getenv('B')\n"
    root = make_repo(tmp_path, {"infrastructure/composition.py": src, "infrastructure/config.py": src})
    assert check(root) == []


def test_ag002_applies_outside_layers(tmp_path):
    root = make_repo(tmp_path, {"main.py": "import os\nPORT = os.environ['PORT']\n"})
    assert check(root) == [("AG002", "src/main.py", 2, "os.environ")]


def test_ag002_and_ag001_both_fire_in_domain(tmp_path):
    root = make_repo(tmp_path, {"domain/user.py": "import os\nX = os.getenv('A')\n"})
    assert sorted(v[0] for v in check(root)) == ["AG001", "AG002"]


# ---------------------------------------------------------------------------
# AG003 adapter imports
# ---------------------------------------------------------------------------


def test_ag003_http_adapter_import_flagged(tmp_path):
    root = make_repo(tmp_path, {
        "infrastructure/http/app.py": "from infrastructure.repositories.memory import MemoryRepo\n",
        "infrastructure/lambda_handler.py": "from infrastructure import repositories\n",
    })
    found = check(root)
    assert ("AG003", "src/infrastructure/http/app.py", 1, "infrastructure.repositories.memory") in found
    assert ("AG003", "src/infrastructure/lambda_handler.py", 1, "infrastructure.repositories") in found


def test_ag003_composition_and_adapter_package_allowed(tmp_path):
    root = make_repo(tmp_path, {
        "infrastructure/composition.py": "from infrastructure.repositories.memory import MemoryRepo\n",
        "infrastructure/repositories/dynamo.py": "from .memory import MemoryRepo\n",
        "infrastructure/repositories/memory.py": "class MemoryRepo: pass\n",
    })
    assert check(root) == []


def test_tests_are_exempt(tmp_path):
    bad = "import os\nimport boto3\nX = os.environ\nfrom infrastructure.repositories import memory\n"
    root = make_repo(tmp_path, {
        "tests/test_a.py": bad, "domain/tests/helpers.py": bad,
        "domain/test_user.py": bad, "conftest.py": bad,
    })
    assert check(root) == []


# ---------------------------------------------------------------------------
# check CLI
# ---------------------------------------------------------------------------


def test_check_cli_on_violating_fixture():
    p = cli(["check"], FIX / "violating")
    assert p.returncode == 1, p.stdout + p.stderr
    out = p.stdout
    assert "AG001 src/domain/user.py:2 domain imports 'boto3' (not in allow list for layer 'domain')" in out
    assert "AG001 src/domain/order.py:5 domain imports 'requests' dynamically" in out
    assert "AG002 src/domain/order.py:6" in out
    assert "AG003 src/domain/order.py:3" in out
    assert "AG001 src/application/use_cases/create_user.py:1 application imports 'fastapi'" in out
    assert "AG002 src/infrastructure/http/app.py:1" in out
    assert "AG002 src/infrastructure/http/app.py:2" in out
    assert "AG003 src/infrastructure/http/app.py:3" in out
    assert "warning AG000 src/infrastructure/broken.py:1" in out
    assert "tests/test_user.py" not in out


def test_check_cli_json_and_clean_fixture():
    p = cli(["check", "--format", "json"], FIX / "violating")
    data = json.loads(p.stdout)
    assert {v["rule"] for v in data["violations"]} == {"AG001", "AG002", "AG003"}
    assert [w["rule"] for w in data["warnings"]] == ["AG000"]
    p = cli(["check"], FIX / "clean")
    assert p.returncode == 0, p.stdout
    assert "no violations" in p.stdout


def test_parse_error_is_warning_only(tmp_path):
    root = make_repo(tmp_path, {"domain/user.py": "def x(:\n"})
    p = cli(["check"], root)
    assert p.returncode == 0
    assert "warning AG000" in p.stdout


def test_check_explicit_paths_and_ignored_files(tmp_path):
    root = make_repo(tmp_path, {"domain/user.py": "import boto3\n", "domain/ok.py": "import re\n"})
    (root / "outside.py").write_text("import boto3\n")
    (root / "src" / "domain" / "notes.txt").write_text("import boto3\n")
    p = cli(["check", "src/domain/ok.py", "outside.py"], root)
    assert p.returncode == 0, p.stdout
    p = cli(["check", "src/domain"], root)
    assert p.returncode == 1


def test_config_errors_exit_3(tmp_path):
    assert cli(["check"], tmp_path).returncode == 3
    (tmp_path / "archguard.toml").write_text("[project\n")
    assert cli(["check"], tmp_path).returncode == 3
    (tmp_path / "archguard.toml").write_text('[project]\nsource_roots=["missing"]\n[layers.domain]\n')
    p = cli(["check"], tmp_path)
    assert p.returncode == 3 and "does not exist" in p.stderr
    assert cli(["check", "--nope"], tmp_path).returncode == 3


def test_check_is_fast_on_50_files(tmp_path):
    files = {f"domain/m{i}.py": "import re\nfrom typing import List\nclass A:\n    pass\n" * 20 for i in range(25)}
    files.update({f"infrastructure/m{i}.py": "import os\nimport boto3\n" * 20 for i in range(25)})
    root = make_repo(tmp_path, files)
    cfg = ag.load_config(root / "archguard.toml")
    start = time.perf_counter()
    rep = ag.run_check(cfg, None, root)
    elapsed = time.perf_counter() - start
    assert rep.files_checked == 50
    assert elapsed < 1.0, elapsed


# ---------------------------------------------------------------------------
# hook mode
# ---------------------------------------------------------------------------


def kiro(tool: str, tool_input: dict, cwd: Path) -> dict:
    return {"session_id": "s", "hook_event_name": "PreToolUse", "cwd": str(cwd),
            "tool_name": tool, "tool_input": tool_input}


def test_hook_kiro_fs_write_blocks(tmp_path):
    root = make_repo(tmp_path, {})
    code, err = hook(kiro("fs_write", {"path": "src/domain/user.py", "text": "import re\nimport boto3\n"}, root), root)
    assert code == 2
    assert "AG001 src/domain/user.py:2 domain imports 'boto3'" in err
    assert "offending: import boto3" in err
    assert "why:" in err and "fix: Define a port in application/ports" in err
    assert "The change was blocked before it was written." in err
    assert not (root / "src/domain/user.py").exists()


def test_hook_kiro_fs_write_clean_allows(tmp_path):
    root = make_repo(tmp_path, {})
    code, err = hook(kiro("fs_write", {"path": str(root / "src/domain/user.py"), "text": "import re\n"}, root), root)
    assert (code, err) == (0, "")


def test_hook_kiro_str_replace_applies_to_disk(tmp_path):
    root = make_repo(tmp_path, {"domain/user.py": "import re\n\nclass User: pass\n"})
    payload = kiro("str_replace", {"path": "src/domain/user.py", "oldStr": "import re",
                                   "newStr": "import re\nimport boto3", "replace_all": False}, root)
    code, err = hook(payload, root)
    assert code == 2 and "boto3" in err
    assert (root / "src/domain/user.py").read_text() == "import re\n\nclass User: pass\n"
    payload["tool_input"]["oldStr"] = "not present"
    assert hook(payload, root)[0] == 0


def test_hook_kiro_fs_append_inserts_newline(tmp_path):
    root = make_repo(tmp_path, {"domain/user.py": "import re"})
    code, err = hook(kiro("fs_append", {"path": "src/domain/user.py", "text": "import boto3\n"}, root), root)
    assert code == 2 and "src/domain/user.py:2" in err


def test_hook_kiro_cli_write_variants(tmp_path):
    root = make_repo(tmp_path, {"domain/user.py": "import re\n"})
    code, _ = hook(kiro("write", {"command": "create", "path": "src/domain/a.py", "file_text": "import boto3\n"}, root), root)
    assert code == 2
    code, _ = hook(kiro("fs_write", {"command": "str_replace", "path": "src/domain/user.py",
                                     "old_str": "import re", "new_str": "import requests"}, root), root)
    assert code == 2


def git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, env=clean_env())


def test_hook_execute_bash_git_commit_checks_staged(tmp_path):
    root = make_repo(tmp_path, {"domain/user.py": "import re\n"})
    git(["init", "-q"], root)
    (root / "src/domain/user.py").write_text("import re\nimport boto3\n")
    git(["add", "src/domain/user.py"], root)
    # Fix the working tree after staging: the index still holds the violation.
    (root / "src/domain/user.py").write_text("import re\n")
    payload = kiro("execute_bash", {"command": "git commit -m 'add user'", "cwd": str(root)}, root)
    code, err = hook(payload, root)
    assert code == 2
    assert "archguard blocked this commit" in err and "src/domain/user.py:2" in err
    assert "No commit was created." in err
    assert cli(["staged"], root).returncode == 1
    assert hook(kiro("execute_bash", {"command": "ls -la", "cwd": str(root)}, root), root)[0] == 0
    git(["add", "src/domain/user.py"], root)
    assert hook(payload, root)[0] == 0
    assert cli(["staged"], root).returncode == 0


def test_hook_git_commit_all_checks_worktree(tmp_path):
    root = make_repo(tmp_path, {"domain/user.py": "import re\n"})
    git(["init", "-q"], root)
    git(["add", "."], root)
    git(["-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "init"], root)
    (root / "src/domain/user.py").write_text("import boto3\n")
    assert hook(kiro("shell", {"command": "git commit -am wip", "cwd": str(root)}, root), root)[0] == 2
    assert hook(kiro("shell", {"command": "git commit -m wip", "cwd": str(root)}, root), root)[0] == 0


def test_hook_claude_write_edit_bash(tmp_path):
    root = make_repo(tmp_path, {"application/use_cases/create.py": "from domain.user import User\n"})
    target = str(root / "src/application/use_cases/create.py")
    code, err = hook({"hook_event_name": "PreToolUse", "cwd": str(root), "tool_name": "Write",
                      "tool_input": {"file_path": target, "content": "import fastapi\n"}}, root, "claude")
    assert code == 2 and "application imports 'fastapi'" in err
    code, err = hook({"hook_event_name": "PreToolUse", "cwd": str(root), "tool_name": "Edit",
                      "tool_input": {"file_path": target, "old_string": "from domain.user import User",
                                     "new_string": "import os\nX = os.environ['A']", "replace_all": False}},
                     root, "claude")
    assert code == 2 and "AG002" in err and "AG001" in err
    code, _ = hook({"hook_event_name": "PreToolUse", "cwd": str(root), "tool_name": "Bash",
                    "tool_input": {"command": "pytest -q"}}, root, "claude")
    assert code == 0


def test_hook_codex_apply_patch_add_and_update(tmp_path):
    root = make_repo(tmp_path, {"domain/user.py": "import re\n\n\nclass User:\n    pass\n"})
    add = ("*** Begin Patch\n*** Add File: src/domain/order.py\n+import re\n+import boto3\n+\n"
           "+class Order:\n+    pass\n*** End Patch\n")
    code, err = hook({"hook_event_name": "PreToolUse", "cwd": str(root), "tool_name": "apply_patch",
                      "tool_input": {"command": add}}, root, "codex")
    assert code == 2 and "src/domain/order.py:2" in err
    upd = ("*** Begin Patch\n*** Update File: src/domain/user.py\n@@\n import re\n+import requests\n"
           " \n*** End Patch\n")
    code, err = hook({"hook_event_name": "PreToolUse", "cwd": str(root), "tool_name": "apply_patch",
                      "tool_input": {"command": upd}}, root, "codex")
    assert code == 2 and "src/domain/user.py:2 domain imports 'requests'" in err
    ok = ("*** Begin Patch\n*** Update File: src/domain/user.py\n@@ class User:\n-    pass\n"
          "+    name: str = ''\n*** End Patch\n")
    assert hook({"hook_event_name": "PreToolUse", "cwd": str(root), "tool_name": "apply_patch",
                 "tool_input": {"command": ok}}, root, "codex")[0] == 0
    delete = "*** Begin Patch\n*** Delete File: src/domain/user.py\n*** End Patch\n"
    assert hook({"hook_event_name": "PreToolUse", "cwd": str(root), "tool_name": "apply_patch",
                 "tool_input": {"command": delete}}, root, "codex")[0] == 0


def test_hook_codex_update_fallback_to_added_lines(tmp_path):
    root = make_repo(tmp_path, {"domain/user.py": "import re\n"})
    upd = ("*** Begin Patch\n*** Update File: src/domain/user.py\n@@\n-this line does not exist\n"
           "+import boto3\n*** End Patch\n")
    code, err = hook({"hook_event_name": "PreToolUse", "cwd": str(root), "tool_name": "apply_patch",
                      "tool_input": {"command": upd}}, root, "codex")
    assert code == 2 and "boto3" in err


def test_hook_codex_bash_heredoc_patch(tmp_path):
    root = make_repo(tmp_path, {})
    cmd = ("apply_patch <<'EOF'\n*** Begin Patch\n*** Add File: src/domain/x.py\n+import boto3\n"
           "*** End Patch\nEOF\n")
    assert hook({"cwd": str(root), "tool_name": "Bash", "tool_input": {"command": cmd}}, root, "codex")[0] == 2


def test_hook_syntax_error_uses_regex_fallback(tmp_path):
    root = make_repo(tmp_path, {})
    code, err = hook(kiro("fs_write", {"path": "src/domain/u.py", "text": "import boto3\ndef broken(:\n"}, root), root)
    assert code == 2 and "boto3" in err
    code, err = hook(kiro("fs_write", {"path": "src/infrastructure/http/a.py",
                                       "text": "x = os.environ['A']\ndef broken(:\n"}, root), root)
    assert code == 2 and "AG002" in err


def test_hook_non_governed_allowed(tmp_path):
    root = make_repo(tmp_path, {})
    for path in ["README.md", "scripts/tool.py", "src/tests/test_x.py", "src/domain/test_user.py"]:
        code, err = hook(kiro("fs_write", {"path": path, "text": "import boto3\nimport os\nos.environ\n"}, root), root)
        assert (code, err) == (0, ""), path


def test_hook_bad_input_allowed(tmp_path):
    root = make_repo(tmp_path, {})
    assert hook("this is not json", root) == (0, "")
    assert hook("[1, 2]", root) == (0, "")
    assert hook(kiro("fs_read", {"path": "src/domain/user.py"}, root), root) == (0, "")
    assert hook("", root) == (0, "")


def test_hook_governed_but_undeterminable_blocks(tmp_path):
    root = make_repo(tmp_path, {})
    code, err = hook(kiro("fs_write", {"path": "src/domain/user.py"}, root), root)
    assert code == 2 and "could not evaluate" in err


def test_hook_without_config_allows(tmp_path):
    code, err = hook(kiro("fs_write", {"path": "domain/user.py", "text": "import boto3\n"}, tmp_path), tmp_path)
    assert (code, err) == (0, "")


def test_hook_invalid_config_blocks_py_writes_only(tmp_path):
    (tmp_path / "archguard.toml").write_text("[project\n")
    assert hook(kiro("fs_write", {"path": "a.py", "text": "x = 1\n"}, tmp_path), tmp_path)[0] == 2
    assert hook(kiro("fs_write", {"path": "a.md", "text": "x\n"}, tmp_path), tmp_path)[0] == 0


def test_hook_explicit_config_flag(tmp_path):
    root = make_repo(tmp_path, {"domain/user.py": "import re\n"})
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    payload = json.dumps({"cwd": str(elsewhere), "tool_name": "fs_write",
                          "tool_input": {"path": str(root / "src/domain/user.py"), "text": "import boto3\n"}})
    p = subprocess.run([sys.executable, str(AG), "hook", "--agent", "kiro", "--config", str(root / "archguard.toml")],
                       cwd=str(elsewhere), input=payload, capture_output=True, text=True, env=clean_env())
    assert p.returncode == 2 and p.stdout == ""


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------


def scan_copy(tmp_path: Path, name: str) -> dict:
    dest = tmp_path / name
    shutil.copytree(FIX / name, dest)
    return ag.run_scan(str(dest), 30, tmp_path)


def test_scan_monolith_fixture(tmp_path):
    r = scan_copy(tmp_path, "monolith")
    assert r["classification"] == "monolith"
    assert r["coupling_score"] > 0
    assert r["findings"]["sdk_clients_at_import_time"][0]["line"] == 4
    assert {x["name"] for x in r["findings"]["hard_coded_resource_names"]} == {"Widgets"}
    assert r["churn"]["available"] is False
    moves = {m["move"]: m for m in r["plan"]}
    assert set(moves) == {0, 1, 2, 3, 4, 5, 6}
    assert moves[0]["status"] == "baseline"
    assert any("domain/widget.py" in a for a in moves[1]["actions"])
    assert any("dynamodb_widget_repository.py" in a for a in moves[6]["actions"])


def test_scan_clean_fixture(tmp_path):
    r = scan_copy(tmp_path, "clean")
    assert r["classification"] == "hexagonal-clean", r["classification_reason"]
    assert r["coupling_score"] == 0
    assert r["composition_roots"] == ["src/infrastructure/composition.py"]
    assert r["test_modules"] == 1
    statuses = {m["move"]: m["status"] for m in r["plan"]}
    assert statuses[0] == "skip" and statuses[1] == "done" and statuses[2] == "done"


def test_scan_layered_fixture(tmp_path):
    r = scan_copy(tmp_path, "layered")
    assert r["classification"] == "layered"
    assert r["findings"]["inward_violations"] == [
        {"module": "app/services/order_service.py", "layer": "application", "imports": ["boto3"]}]
    assert r["coupling_score"] == 25
    md = ag.format_scan_md(r)
    assert "```mermaid\nflowchart LR" in md
    assert "linkStyle" in md
    assert "## Refactor plan (six moves)" in md
    assert "| app/services/order_service.py | application | boto3 |" in md


def test_scan_cli_formats(tmp_path):
    shutil.copytree(FIX / "monolith", tmp_path / "m")
    p = cli(["scan", "m", "--format", "json", "--churn-days", "7"], tmp_path)
    assert p.returncode == 0 and json.loads(p.stdout)["classification"] == "monolith"
    p = cli(["scan", "m"], tmp_path)
    assert p.returncode == 0 and p.stdout.startswith("# archguard scan: m")
    assert cli(["scan", "missing"], tmp_path).returncode == 3


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


def test_init_detects_source_root_and_config_works(tmp_path):
    root = tmp_path / "svc"
    shutil.copytree(FIX / "clean" / "src", root / "src")
    p = cli(["init", "--root", str(root)], tmp_path)
    assert p.returncode == 0, p.stderr
    cfg = ag.load_config(root / "archguard.toml")
    assert [c.name for c in cfg.source_roots] == ["src"]
    assert "infrastructure.repositories" in cfg.adapter_packages
    assert cli(["check"], root).returncode == 0
    assert cli(["init", "--root", str(root)], tmp_path).returncode == 3


# ---------------------------------------------------------------------------
# score, JSON and SARIF
# ---------------------------------------------------------------------------

METRICS_SRC = '''
def simple(a, b):
    return a + b


def branchy(x, items):
    if x and items:
        for i in items:
            if i > 2 or i < -2:
                pass
    elif x:
        return [y for y in items if y]
    try:
        pass
    except ValueError:
        pass
    return x if x else None


class Svc:
    def method(self, a, b, c, d, e):
        def inner(z):
            return z
        return inner(a)

    @classmethod
    def build(cls, *args, key=None, **kw):
        return cls()
'''


def test_function_metrics_counts():
    import ast as _ast
    m = {f.name: f for f in ag.function_metrics(_ast.parse(METRICS_SRC))}
    assert set(m) == {"simple", "branchy", "Svc.method", "Svc.method.inner", "Svc.build"}
    assert m["simple"].complexity == 1 and m["simple"].parameters == 2
    # if + and + for + if + or + elif + comprehension(1 + 1 if) + except + ternary
    assert m["branchy"].complexity == 11
    assert m["Svc.method"].parameters == 5          # self is not counted
    assert m["Svc.method"].complexity == 1          # nested def scored on its own
    assert m["Svc.method.inner"].parameters == 1    # nested def is not a method
    assert m["Svc.build"].parameters == 3           # cls excluded; *args, key, **kw
    assert m["simple"].length == 2 and m["simple"].line == 2


def _score(root: Path, *args: str) -> tuple[int, dict]:
    p = cli(["score", "--format", "json", *args], root)
    return p.returncode, json.loads(p.stdout)


def test_score_violating_fixture_json():
    code, r = _score(FIX / "violating")
    assert code == 0  # no gates set: scores are informational
    assert r["schema_version"] == "1.0" and r["tool"] == {"name": "archguard", "version": ag.VERSION}
    port = r["scores"]["portability"]
    assert (port["files"], port["clean_files"], port["score"]) == (7, 3, 42.9)
    assert port["by_rule"] == {"AG001": 7, "AG002": 3, "AG003": 3}
    assert port["violations"] == len([f for f in r["findings"] if f["dimension"] == "portability"])
    assert [w["rule"] for w in r["warnings"]] == ["AG000"]
    f = next(f for f in r["findings"] if f["path"] == "src/domain/user.py")
    assert f["rule"] == "AG001" and f["name"] == "layer-import" and f["severity"] == "error"
    assert f["line"] == 2 and "boto3" in f["message"] and f["hint"]
    assert len(f["id"]) == 16 and len({x["id"] for x in r["findings"]}) == len(r["findings"])


def test_score_is_deterministic_and_cwd_independent():
    a = cli(["score", "--format", "json"], FIX / "violating").stdout
    b = cli(["score", "--format", "json"], FIX / "violating").stdout
    c = cli(["score", "--format", "json", "--config", str(FIX / "violating" / "archguard.toml")], HERE).stdout
    assert a == b == c


def test_score_ids_survive_line_shifts(tmp_path):
    root = make_repo(tmp_path, {"domain/user.py": "import boto3\n"})
    _, before = _score(root)
    (root / "src" / "domain" / "user.py").write_text("\n\n# moved\nimport boto3\n")
    _, after = _score(root)
    assert before["findings"][0]["line"] == 1 and after["findings"][0]["line"] == 4
    assert before["findings"][0]["id"] == after["findings"][0]["id"]


def test_score_cleanliness_and_quality_config(tmp_path):
    root = make_repo(tmp_path, {"domain/calc.py": METRICS_SRC.replace("pass", "x = 1")})
    _, r = _score(root)
    clean = r["scores"]["cleanliness"]
    assert clean["functions"] == 5 and clean["checks"] == 15
    assert clean["by_rule"] == {"AGQ101": 1} and clean["passed"] == 14 and clean["score"] == 93.3
    f = next(f for f in r["findings"] if f["rule"] == "AGQ101")
    assert (f["symbol"], f["value"], f["threshold"], f["severity"]) == ("branchy", 11, 10, "warning")
    assert f["dimension"] == "cleanliness"
    cfg = root / "archguard.toml"
    cfg.write_text(cfg.read_text() + "\n[quality]\nmax_complexity = 20\nmax_parameters = 4\n")
    _, r = _score(root)
    clean = r["scores"]["cleanliness"]
    assert clean["thresholds"] == {"max_complexity": 20, "max_function_lines": 50, "max_parameters": 4}
    assert clean["by_rule"] == {"AGQ103": 1}
    # the check command ignores cleanliness findings
    assert cli(["check"], root).returncode == 0


def test_score_quality_config_errors(tmp_path):
    root = make_repo(tmp_path, {"domain/a.py": "x = 1\n"})
    cfg = root / "archguard.toml"
    base = cfg.read_text()
    for bad in ("[quality]\nmax_complexity = 0\n", "[quality]\nmax_nesting = 3\n",
                "[quality]\nmax_parameters = true\n", "quality = 3\n"):
        cfg.write_text(base + "\n" + bad if not bad.startswith("quality =") else bad + base)
        p = cli(["score"], root)
        assert p.returncode == 3, (bad, p.stdout, p.stderr)


def test_score_gates_and_text(tmp_path):
    root = make_repo(tmp_path, {"domain/user.py": "import boto3\n", "domain/ok.py": "import re\n"})
    code, r = _score(root, "--min-portability", "50")
    assert code == 0 and r["scores"]["portability"]["score"] == 50.0
    assert r["gates"] == {"portability": {"min": 50.0, "passed": True}} and r["passed"] is True
    code, r = _score(root, "--min-portability", "50.1", "--min-cleanliness", "100")
    assert code == 1 and r["passed"] is False
    assert r["gates"]["portability"]["passed"] is False and r["gates"]["cleanliness"]["passed"] is True
    p = cli(["score", "--min-portability", "90"], root)
    assert p.returncode == 1
    assert p.stdout.startswith("portability 50.0/100  (1/2 files clean, 1 violation(s))")
    assert "gate portability >= 90.0: FAIL" in p.stdout
    assert cli(["score", "--min-cleanliness", "101"], root).returncode == 3


def test_score_empty_tree_is_100(tmp_path):
    code, r = _score(make_repo(tmp_path, {}))
    assert code == 0 and r["files_analyzed"] == 0
    assert r["scores"]["portability"]["score"] == 100.0 and r["scores"]["cleanliness"]["score"] == 100.0


def _assert_sarif(log: dict) -> dict:
    assert log["version"] == "2.1.0" and log["$schema"].endswith("sarif-2.1.0.json")
    (run,) = log["runs"]
    driver = run["tool"]["driver"]
    assert driver["name"] == "archguard" and driver["version"] == ag.VERSION
    ids = [r["id"] for r in driver["rules"]]
    assert ids == sorted(set(ids))
    for res in run["results"]:
        assert driver["rules"][res["ruleIndex"]]["id"] == res["ruleId"]
        assert res["level"] in {"error", "warning", "note"}
        loc = res["locations"][0]["physicalLocation"]
        assert not loc["artifactLocation"]["uri"].startswith("/")
        assert loc.get("region", {"startLine": 1})["startLine"] >= 1
        assert len(res["partialFingerprints"]["archguardFindingId/v1"]) == 16
    return run


def test_check_and_score_sarif():
    p = cli(["check", "--format", "sarif"], FIX / "violating")
    assert p.returncode == 1
    run = _assert_sarif(json.loads(p.stdout))
    assert {r["id"] for r in run["tool"]["driver"]["rules"]} == {"AG000", "AG001", "AG002", "AG003"}
    parse = next(r for r in run["results"] if r["ruleId"] == "AG000")
    assert parse["level"] == "warning"
    p = cli(["score", "--format", "sarif"], FIX / "violating")
    run = _assert_sarif(json.loads(p.stdout))
    assert run["properties"]["scores"] == {"portability": 42.9, "cleanliness": 100.0}
    _, r = _score(FIX / "violating")
    assert sorted(x["partialFingerprints"]["archguardFindingId/v1"] for x in run["results"]) == \
        sorted(f["id"] for f in r["findings"] + r["warnings"])
