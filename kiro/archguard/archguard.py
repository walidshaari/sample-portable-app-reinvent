#!/usr/bin/env python3
"""archguard: enforce clean-architecture import boundaries in Python code.

Single file, standard library only, Python 3.11+.

Subcommands:
  check   check files or directories against archguard.toml
  staged  check the staged (index) content of .py files
  hook    evaluate one agent tool call read from stdin (Kiro, Claude Code, Codex)
  score   portability and cleanliness scores with findings (text, json, sarif)
  scan    brownfield analysis of any Python tree, no config needed
  init    write a starter archguard.toml

Rule ids:
  AG000 parse-error (warning in check/staged; regex fallback in hook)
  AG001 layer-import
  AG002 config-read-outside-composition-root
  AG003 adapter-import-outside-composition-root
  AGQ101 function-complexity (score only)
  AGQ102 function-length (score only)
  AGQ103 parameter-count (score only)

Exit codes: 0 clean or allowed, 1 violations (check/staged) or a failed score
gate (score), 2 blocked (hook), 3 configuration or usage error.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

VERSION = "0.2.0"
SCORE_SCHEMA_VERSION = "1.0"
CONFIG_NAME = "archguard.toml"

EXIT_OK = 0
EXIT_VIOLATIONS = 1
EXIT_BLOCK = 2
EXIT_CONFIG = 3

SKIP_DIRS = {
    "__pycache__", "node_modules", "venv", "env", "site-packages", "build",
    "dist", "cdk.out", "htmlcov",
}

ENV_ATTRS = {"environ", "environb", "getenv", "getenvb", "putenv", "unsetenv"}

HINT_PORT = (
    "Define a port in application/ports and implement it in infrastructure/; "
    "inject it from the composition root."
)
HINT_INWARD = (
    "Dependencies must point inward. Move the shared code into the inner layer, "
    "or depend on a port owned by the inner layer."
)
HINT_DYNAMIC = (
    "Replace the dynamic import with a static import of an allowed module, or move "
    "the dynamic loading into infrastructure/ behind a port."
)
HINT_CONFIG = "Read configuration only in the composition root and pass values in as parameters."
HINT_ADAPTER = (
    "Depend on the port in application/ports and receive the adapter as a parameter; "
    "construct and wire the adapter only in the composition root."
)
BLOCK_SENTENCE = "The change was blocked before it was written."

# Rule metadata for JSON and SARIF output: id -> (name, dimension, level, description, help).
RULES = {
    "AG000": ("parse-error", "portability", "warning",
              "The file could not be parsed, so its imports were not checked.",
              "Fix the syntax error so archguard can check the file."),
    "AG001": ("layer-import", "portability", "error",
              "A layer imports a module that is not in its allow list.",
              HINT_PORT + " " + HINT_INWARD),
    "AG002": ("config-read-outside-composition-root", "portability", "error",
              "Configuration is read outside the composition root.", HINT_CONFIG),
    "AG003": ("adapter-import-outside-composition-root", "portability", "error",
              "A concrete adapter is imported outside the composition root.", HINT_ADAPTER),
    "AGQ101": ("function-complexity", "cleanliness", "warning",
               "A function's cyclomatic complexity is above quality.max_complexity.",
               "Split the function, extract helpers, or replace branching with a lookup."),
    "AGQ102": ("function-length", "cleanliness", "warning",
               "A function is longer than quality.max_function_lines.",
               "Extract cohesive steps into named helper functions."),
    "AGQ103": ("parameter-count", "cleanliness", "warning",
               "A function takes more parameters than quality.max_parameters.",
               "Group related parameters into a dataclass or split the function."),
}
QUALITY_DEFAULTS = {"max_complexity": 10, "max_function_lines": 50, "max_parameters": 5}
_SELF_DIR = Path(os.path.realpath(__file__)).parent


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class ConfigError(Exception):
    """Invalid or missing configuration (exit code 3)."""


class UsageError(Exception):
    """Invalid command line (exit code 3)."""


@dataclass
class Layer:
    name: str
    paths: list[str]
    allow: list[str]

    def allows(self, dotted: str) -> bool:
        if "*" in self.allow:
            return True
        return any(dotted == a or dotted.startswith(a + ".") for a in self.allow)


@dataclass
class Config:
    path: Path
    base: Path
    source_roots: list[Path]
    layers: list[Layer]
    composition_roots: list[str]
    adapter_packages: list[str]
    config_read_modules: list[str]
    quality: dict = field(default_factory=lambda: dict(QUALITY_DEFAULTS))


def _realpath(p: Path | str) -> Path:
    return Path(os.path.realpath(os.path.expanduser(str(p))))


def _str_list(table: dict, key: str, where: str, default: list[str] | None = None) -> list[str]:
    if key not in table:
        if default is None:
            raise ConfigError(f"{where}.{key} is required")
        return list(default)
    value = table[key]
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{where}.{key} must be a list of strings")
    return list(value)


def load_config(path: Path) -> Config:
    path = _realpath(path)
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {path}") from None
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: invalid TOML: {exc}") from None
    base = path.parent
    project = data.get("project")
    if not isinstance(project, dict):
        raise ConfigError(f"{path}: missing [project] table")
    roots = _str_list(project, "source_roots", "project")
    if not roots:
        raise ConfigError(f"{path}: project.source_roots must not be empty")
    source_roots = []
    for r in roots:
        rp = _realpath(base / r)
        if not rp.is_dir():
            raise ConfigError(f"{path}: source root '{r}' does not exist (resolved to {rp})")
        source_roots.append(rp)
    layers_t = data.get("layers")
    if not isinstance(layers_t, dict) or not layers_t:
        raise ConfigError(f"{path}: at least one [layers.<name>] table is required")
    layers = []
    for name, spec in layers_t.items():
        if not isinstance(spec, dict):
            raise ConfigError(f"{path}: layers.{name} must be a table")
        paths = [p.strip("/") for p in _str_list(spec, "paths", f"layers.{name}", [name])]
        allow = _str_list(spec, "allow", f"layers.{name}", [name])
        layers.append(Layer(name, paths, allow))
    rules = data.get("rules", {})
    if not isinstance(rules, dict):
        raise ConfigError(f"{path}: [rules] must be a table")
    quality_t = data.get("quality", {})
    if not isinstance(quality_t, dict):
        raise ConfigError(f"{path}: [quality] must be a table")
    quality = dict(QUALITY_DEFAULTS)
    for key, value in quality_t.items():
        if key not in QUALITY_DEFAULTS:
            raise ConfigError(f"{path}: unknown key quality.{key} "
                              f"(expected one of {', '.join(QUALITY_DEFAULTS)})")
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ConfigError(f"{path}: quality.{key} must be a positive integer")
        quality[key] = value
    return Config(
        path=path,
        base=base,
        source_roots=sorted(source_roots, key=lambda p: len(p.parts), reverse=True),
        layers=layers,
        composition_roots=[
            c.strip("/") for c in _str_list(
                rules, "composition_roots", "rules",
                ["infrastructure/composition.py", "infrastructure/config.py"],
            )
        ],
        adapter_packages=_str_list(rules, "adapter_packages", "rules", []),
        config_read_modules=_str_list(
            rules, "config_read_modules", "rules", ["dotenv", "decouple", "environs"]
        ),
        quality=quality,
    )


def find_config(start: Path) -> Path | None:
    cur = _realpath(start)
    if cur.is_file():
        cur = cur.parent
    for d in (cur, *cur.parents):
        candidate = d / CONFIG_NAME
        if candidate.is_file():
            return candidate
    return None


def resolve_config(option: str | None, start: Path) -> Config:
    if option:
        return load_config(Path(option))
    found = find_config(start)
    if found is None:
        raise ConfigError(
            f"no {CONFIG_NAME} found in {start} or its parents; pass --config or run 'archguard init'"
        )
    return load_config(found)


# ---------------------------------------------------------------------------
# File classification
# ---------------------------------------------------------------------------


def is_test_path(parts: tuple[str, ...]) -> bool:
    if not parts:
        return False
    name = parts[-1]
    if name == "conftest.py" or (name.startswith("test_") and name.endswith(".py")):
        return True
    return "tests" in parts[:-1]


@dataclass
class FileInfo:
    path: Path
    root: Path
    rel: str
    module: str
    is_package: bool
    layer: Layer | None
    composition_root: bool
    in_adapter_package: bool


def classify_file(cfg: Config, path: Path) -> FileInfo | None:
    """Return governance info for a path, or None when no rule applies to it."""
    if Path(path).suffix != ".py":
        return None
    p = _realpath(path)
    if p.parent == _SELF_DIR:  # the vendored archguard tool itself is never governed
        return None
    for root in cfg.source_roots:
        try:
            rel_parts = p.relative_to(root).parts
        except ValueError:
            continue
        if is_test_path(rel_parts):
            return None
        if any(part in SKIP_DIRS or part.startswith(".") for part in rel_parts[:-1]):
            return None
        rel = "/".join(rel_parts)
        mod_parts = list(rel_parts[:-1]) + [rel_parts[-1][:-3]]
        is_package = mod_parts[-1] == "__init__"
        if is_package:
            mod_parts = mod_parts[:-1]
        module = ".".join(mod_parts)
        layer = None
        best = -1
        for lyr in cfg.layers:
            for lp in lyr.paths:
                if rel == lp or rel.startswith(lp + "/") or lp in ("", "."):
                    score = len(lp)
                    if score > best:
                        best, layer = score, lyr
        try:
            rel_to_base = p.relative_to(cfg.base).as_posix()
        except ValueError:
            rel_to_base = None
        comp = any(rel == c or rel_to_base == c for c in cfg.composition_roots)
        in_adapter = any(module == a or module.startswith(a + ".") for a in cfg.adapter_packages)
        return FileInfo(p, root, rel, module, is_package, layer, comp, in_adapter)
    return None


def resolve_relative(module: str, is_package: bool, level: int, name: str | None) -> str:
    """Resolve 'from ..x import y' to an absolute dotted name relative to the source root."""
    pkg = module.split(".") if module else []
    if not is_package:
        pkg = pkg[:-1]
    if level - 1 > len(pkg):
        return "." * level + (name or "")
    base = pkg[: len(pkg) - (level - 1)]
    if name:
        base = base + name.split(".")
    return ".".join(base)


# ---------------------------------------------------------------------------
# Import and config-read collection
# ---------------------------------------------------------------------------


@dataclass
class ImportRef:
    line: int
    target: str
    candidates: tuple[str, ...]
    kind: str  # import | from | dynamic
    via: str = ""


@dataclass
class Violation:
    rule: str
    path: str
    line: int
    message: str
    hint: str
    why: str = ""
    target: str = ""
    text: str = ""
    severity: str = "error"


def _from_refs(line: int, target: str, names: list[str]) -> list[ImportRef]:
    if target == "":
        return [ImportRef(line, n, (n,), "from") for n in names]
    cands = (target,) + tuple(f"{target}.{n}" for n in names)
    return [ImportRef(line, target, cands, "from")]


def collect_ast(tree: ast.AST, module: str, is_package: bool):
    refs: list[ImportRef] = []
    env: list[tuple[int, str]] = []
    os_aliases = {"os"}
    importlib_aliases = {"importlib"}
    import_module_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                refs.append(ImportRef(node.lineno, a.name, (a.name,), "import"))
                if a.name == "os":
                    os_aliases.add(a.asname or "os")
                elif a.name == "importlib":
                    importlib_aliases.add(a.asname or "importlib")
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                target = resolve_relative(module, is_package, node.level, node.module)
            else:
                target = node.module or ""
            names = [a.name for a in node.names if a.name != "*"]
            refs.extend(_from_refs(node.lineno, target, names))
            if target == "os":
                for a in node.names:
                    if a.name in ENV_ATTRS:
                        env.append((node.lineno, f"from os import {a.name}"))
            elif target == "importlib":
                for a in node.names:
                    if a.name in ("import_module", "__import__"):
                        import_module_names.add(a.asname or a.name)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id in os_aliases and node.attr in ENV_ATTRS:
                env.append((node.lineno, f"os.{node.attr}"))
        elif isinstance(node, ast.Call):
            f = node.func
            via = ""
            if (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                    and f.value.id in importlib_aliases and f.attr in ("import_module", "__import__")):
                via = f"importlib.{f.attr}"
            elif isinstance(f, ast.Name) and f.id in import_module_names:
                via = "importlib.import_module"
            elif isinstance(f, ast.Name) and f.id == "__import__":
                via = "__import__"
            if via:
                tgt = ""
                if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    tgt = node.args[0].value
                    if tgt.startswith("."):
                        lvl = len(tgt) - len(tgt.lstrip("."))
                        tgt = resolve_relative(module, is_package, lvl, tgt.lstrip(".") or None)
                refs.append(ImportRef(node.lineno, tgt, (tgt,) if tgt else (), "dynamic", via))
    return refs, env


_IMPORT_LINE = re.compile(r"^\s*import\s+(.+)$")
_FROM_LINE = re.compile(r"^\s*from\s+(\.*[\w.]*)\s+import\s+(.+)$")
_ENV_RE = re.compile(r"\bos\.(environ|getenv|putenv)\b")
_DYN_RE = re.compile(r"(importlib\.import_module|__import__)\s*\(\s*['\"]([\w.]+)['\"]")


def collect_regex(lines: list[tuple[int, str]], module: str, is_package: bool):
    """Line-based fallback used when proposed content does not parse."""
    refs: list[ImportRef] = []
    env: list[tuple[int, str]] = []
    for ln, raw in lines:
        code = raw.split("#", 1)[0]
        m = _FROM_LINE.match(code)
        if m:
            modtxt, names_txt = m.groups()
            level = len(modtxt) - len(modtxt.lstrip("."))
            target = (resolve_relative(module, is_package, level, modtxt.lstrip(".") or None)
                      if level else modtxt)
            names = []
            for n in names_txt.replace("(", " ").replace(")", " ").replace("\\", " ").split(","):
                n = n.strip().split(" as ")[0].strip()
                if re.fullmatch(r"\w+", n):
                    names.append(n)
            refs.extend(_from_refs(ln, target, names))
            if target == "os":
                for n in names:
                    if n in ENV_ATTRS:
                        env.append((ln, f"from os import {n}"))
        else:
            m = _IMPORT_LINE.match(code)
            if m:
                for part in m.group(1).split(","):
                    nm = part.strip().split(" as ")[0].strip()
                    if re.fullmatch(r"[\w.]+", nm):
                        refs.append(ImportRef(ln, nm, (nm,), "import"))
        for em in _ENV_RE.finditer(code):
            env.append((ln, f"os.{em.group(1)}"))
        for dm in _DYN_RE.finditer(code):
            refs.append(ImportRef(ln, dm.group(2), (dm.group(2),), "dynamic", dm.group(1)))
    return refs, env


# ---------------------------------------------------------------------------
# Rule evaluation
# ---------------------------------------------------------------------------


def display_path(p: Path, cwd: Path | None = None) -> str:
    cwd = _realpath(cwd or Path.cwd())
    try:
        return p.relative_to(cwd).as_posix()
    except ValueError:
        return str(p)


def _layer_by_top(cfg: Config) -> dict[str, str]:
    out = {}
    for lyr in cfg.layers:
        for lp in lyr.paths:
            if lp and lp != ".":
                out.setdefault(lp.split("/")[0], lyr.name)
    return out


def evaluate(cfg: Config, fi: FileInfo, refs: list[ImportRef], env: list[tuple[int, str]],
             lines: dict[int, str], disp: str) -> list[Violation]:
    out: list[Violation] = []
    seen: set[tuple] = set()

    def add(v: Violation) -> None:
        key = (v.rule, v.line, v.target, v.message)
        if key not in seen:
            seen.add(key)
            v.text = lines.get(v.line, "").strip()
            out.append(v)

    layer = fi.layer
    if layer is not None and "*" not in layer.allow:
        tops = _layer_by_top(cfg)
        why = (f"Layer '{layer.name}' may only import: {', '.join(layer.allow)}. "
               "Everything else is denied by default.")
        for r in refs:
            if r.kind == "dynamic":
                shown = r.target or "<non-literal>"
                if not layer.allows("importlib") or not r.target or not layer.allows(r.target):
                    add(Violation(
                        "AG001", disp, r.line,
                        f"{layer.name} imports '{shown}' dynamically via {r.via} "
                        f"(dynamic imports are not allowed in layer '{layer.name}')",
                        HINT_DYNAMIC, why, shown))
                continue
            if layer.allows(r.target):
                continue
            other = tops.get(r.target.split(".")[0])
            hint = HINT_INWARD if other and other != layer.name else HINT_PORT
            add(Violation(
                "AG001", disp, r.line,
                f"{layer.name} imports '{r.target}' (not in allow list for layer '{layer.name}')",
                hint, why, r.target))

    if not fi.composition_root:
        why = ("Configuration is read only in the composition root ("
               + ", ".join(cfg.composition_roots)
               + ") so the core and the adapters stay independent of the runtime.")
        for ln, what in env:
            add(Violation("AG002", disp, ln,
                          f"reads configuration via '{what}' outside the composition root",
                          HINT_CONFIG, why, what))
        for r in refs:
            for m in cfg.config_read_modules:
                if r.target == m or r.target.startswith(m + "."):
                    add(Violation("AG002", disp, r.line,
                                  f"imports config loader '{r.target}' outside the composition root",
                                  HINT_CONFIG, why, r.target))
                    break

    if not fi.composition_root and not fi.in_adapter_package and cfg.adapter_packages:
        why = ("Concrete adapters under " + ", ".join(cfg.adapter_packages)
               + " are chosen only in the composition root; everything else depends on ports.")
        for r in refs:
            hit = next((c for c in r.candidates for a in cfg.adapter_packages
                        if c == a or c.startswith(a + ".")), None)
            if hit:
                add(Violation("AG003", disp, r.line,
                              f"imports adapter '{hit}' outside the composition root",
                              HINT_ADAPTER, why, hit))
    out.sort(key=lambda v: (v.line, v.rule))
    return out


def analyze_source(cfg: Config, fi: FileInfo, source: str, disp: str,
                   fallback: bool = False) -> tuple[list[Violation], list[Violation]]:
    """Return (violations, warnings) for one governed file's content."""
    lines = {i + 1: t for i, t in enumerate(source.splitlines())}
    try:
        tree = ast.parse(source, filename=str(fi.path))
    except (SyntaxError, ValueError) as exc:
        lineno = getattr(exc, "lineno", 0) or 0
        msg = getattr(exc, "msg", str(exc))
        if not fallback:
            return [], [Violation("AG000", disp, lineno, f"cannot parse file: {msg}",
                                  "Fix the syntax error; archguard skipped this file.",
                                  severity="warning")]
        refs, env = collect_regex(sorted(lines.items()), fi.module, fi.is_package)
        return evaluate(cfg, fi, refs, env, lines, disp), []
    refs, env = collect_ast(tree, fi.module, fi.is_package)
    return evaluate(cfg, fi, refs, env, lines, disp), []


# ---------------------------------------------------------------------------
# check / staged
# ---------------------------------------------------------------------------


@dataclass
class Report:
    violations: list[Violation] = field(default_factory=list)
    warnings: list[Violation] = field(default_factory=list)
    files_checked: int = 0
    config: str = ""


def iter_py_files(path: Path):
    if path.is_file():
        if path.suffix == ".py":
            yield path
        return
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        for f in sorted(filenames):
            if f.endswith(".py"):
                yield Path(dirpath) / f


def _iter_governed(cfg: Config, paths: list[str] | None, cwd: Path):
    """Yield (path, FileInfo, source or None, error or None) for each governed .py file."""
    targets = [(_realpath(cwd / p) if not os.path.isabs(p) else _realpath(p)) for p in (paths or [])]
    for t in targets:
        if not t.exists():
            raise UsageError(f"path not found: {t}")
    if not targets:
        targets = list(cfg.source_roots)
    seen: set[Path] = set()
    for t in targets:
        for f in iter_py_files(t):
            rf = _realpath(f)
            if rf in seen:
                continue
            seen.add(rf)
            fi = classify_file(cfg, rf)
            if fi is None:
                continue
            try:
                yield rf, fi, rf.read_text(encoding="utf-8", errors="replace"), None
            except OSError as exc:
                yield rf, fi, None, exc


def run_check(cfg: Config, paths: list[str] | None, cwd: Path) -> Report:
    report = Report(config=display_path(cfg.path, cwd))
    for rf, fi, src, err in _iter_governed(cfg, paths, cwd):
        if err is not None:
            report.warnings.append(Violation("AG000", display_path(rf, cwd), 0,
                                             f"cannot read file: {err}", "", severity="warning"))
            continue
        report.files_checked += 1
        v, w = analyze_source(cfg, fi, src, display_path(rf, cwd))
        report.violations.extend(v)
        report.warnings.extend(w)
    return report


def _git(args: list[str], cwd: Path, binary: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=not binary)


def run_staged(cfg: Config, cwd: Path, include_worktree: bool = False) -> Report:
    top = _git(["rev-parse", "--show-toplevel"], cwd)
    if top.returncode != 0:
        raise ConfigError(f"not a git repository: {cwd}")
    toplevel = _realpath(top.stdout.strip())
    report = Report(config=display_path(cfg.path, cwd))
    listed = _git(["diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"], toplevel)
    entries = [(p, "index") for p in listed.stdout.split("\0") if p]
    if include_worktree:
        wt = _git(["diff", "--name-only", "--diff-filter=ACMR", "-z"], toplevel)
        entries += [(p, "worktree") for p in wt.stdout.split("\0") if p]
    seen = set()
    for rel, source in entries:
        if not rel.endswith(".py") or (rel, source) in seen:
            continue
        seen.add((rel, source))
        absolute = toplevel / rel
        fi = classify_file(cfg, absolute)
        if fi is None:
            continue
        if source == "index":
            blob = _git(["show", f":{rel}"], toplevel, binary=True)
            if blob.returncode != 0:
                continue
            content = blob.stdout.decode("utf-8", errors="replace")
        else:
            try:
                content = absolute.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
        report.files_checked += 1
        v, w = analyze_source(cfg, fi, content, display_path(_realpath(absolute), cwd))
        report.violations.extend(v)
        report.warnings.extend(w)
    return report


def format_report(report: Report, fmt: str, cfg: Config | None = None,
                  cwd: Path | None = None) -> str:
    if fmt == "sarif":
        base = cfg.base if cfg else _realpath(cwd or Path.cwd())
        findings = [_finding(v, base, cwd) for v in report.violations + report.warnings]
        return json.dumps(format_sarif(findings), indent=2)
    if fmt == "json":
        return json.dumps({
            "violations": [asdict(v) for v in report.violations],
            "warnings": [asdict(w) for w in report.warnings],
            "files_checked": report.files_checked,
            "config": report.config,
        }, indent=2)
    lines = []
    for v in report.violations + report.warnings:
        loc = f"{v.path}:{v.line if v.line else '?'}"
        prefix = "warning " if v.severity == "warning" else ""
        lines.append(f"{prefix}{v.rule} {loc} {v.message}")
        if v.hint:
            lines.append(f"    fix: {v.hint}")
    n = len(report.violations)
    if n:
        lines.append(f"archguard: {n} violation{'s' if n != 1 else ''}, {len(report.warnings)} "
                     f"warning(s) in {report.files_checked} files (config: {report.config})")
    else:
        lines.append(f"archguard: no violations in {report.files_checked} files, "
                     f"{len(report.warnings)} warning(s) (config: {report.config})")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# score (portability + cleanliness) and SARIF
# ---------------------------------------------------------------------------

_BRANCH_NODES = (ast.If, ast.IfExp, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler,
                 ast.match_case)
_SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _complexity(fn: ast.AST) -> int:
    """McCabe-style count: 1 + branches + boolean operators + comprehension clauses.

    Nested functions and classes are scored on their own, so they are not descended into.
    """
    c = 1
    stack = list(fn.body)
    while stack:
        n = stack.pop()
        if isinstance(n, _SCOPE_NODES):
            continue
        if isinstance(n, _BRANCH_NODES):
            c += 1
        elif isinstance(n, ast.BoolOp):
            c += len(n.values) - 1
        elif isinstance(n, ast.comprehension):
            c += 1 + len(n.ifs)
        stack.extend(ast.iter_child_nodes(n))
    return c


def _param_count(fn: ast.FunctionDef | ast.AsyncFunctionDef, method: bool) -> int:
    a = fn.args
    positional = a.posonlyargs + a.args
    n = len(positional) + len(a.kwonlyargs) + (a.vararg is not None) + (a.kwarg is not None)
    if method and positional and positional[0].arg in ("self", "cls"):
        n -= 1
    return n


@dataclass
class FunctionMetric:
    name: str
    line: int
    complexity: int
    length: int
    parameters: int


def function_metrics(tree: ast.AST) -> list[FunctionMetric]:
    out: list[FunctionMetric] = []

    def visit(node: ast.AST, prefix: str, in_class: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = prefix + child.name
                length = (child.end_lineno or child.lineno) - child.lineno + 1
                out.append(FunctionMetric(qual, child.lineno, _complexity(child), length,
                                          _param_count(child, in_class)))
                visit(child, qual + ".", False)
            elif isinstance(child, ast.ClassDef):
                visit(child, prefix + child.name + ".", True)
            elif isinstance(child, (ast.stmt, ast.excepthandler, ast.match_case)):
                visit(child, prefix, in_class)

    visit(tree, "", False)
    return out


def _base_rel(path: str, base: Path, cwd: Path | None) -> str:
    p = Path(path)
    if not p.is_absolute():
        p = _realpath(cwd or Path.cwd()) / p
    try:
        return _realpath(p).relative_to(base).as_posix()
    except ValueError:
        return p.as_posix()


def _fingerprint(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]


def _finding(v: Violation, base: Path, cwd: Path | None, symbol: str = "",
             value: int | None = None, threshold: int | None = None) -> dict:
    """Stable, machine-readable form of one finding. Paths are relative to the config file."""
    name, dimension, _, _, _ = RULES.get(v.rule, (v.rule, "portability", v.severity, "", ""))
    path = _base_rel(v.path, base, cwd)
    key = symbol or f"{v.target}\x1f{v.text}"
    d = {
        "id": _fingerprint(v.rule, path, key),
        "rule": v.rule,
        "name": name,
        "dimension": dimension,
        "severity": v.severity,
        "path": path,
        "line": v.line,
        "message": v.message,
        "hint": v.hint,
    }
    if symbol:
        d["symbol"] = symbol
    if value is not None:
        d["value"] = value
        d["threshold"] = threshold
    return d


def _pct(part: int, whole: int) -> float:
    return 100.0 if whole == 0 else round(100.0 * part / whole, 1)


def run_score(cfg: Config, paths: list[str] | None, cwd: Path,
              min_portability: float | None = None,
              min_cleanliness: float | None = None) -> dict:
    """Deterministic scores. Same files and config give the same result; no timestamps."""
    q = cfg.quality
    checks = (("AGQ101", "complexity", "max_complexity", "cyclomatic complexity"),
              ("AGQ102", "length", "max_function_lines", "lines"),
              ("AGQ103", "parameters", "max_parameters", "parameters"))
    findings: list[dict] = []
    warnings: list[dict] = []
    files = violating = functions = passed = 0
    port_rules: dict[str, int] = {}
    clean_rules: dict[str, int] = {}
    for rf, fi, src, err in _iter_governed(cfg, paths, cwd):
        disp = display_path(rf, cwd)
        if err is not None:
            warnings.append(_finding(Violation("AG000", disp, 0, f"cannot read file: {err}", "",
                                               severity="warning"), cfg.base, cwd))
            continue
        files += 1
        v, w = analyze_source(cfg, fi, src, disp)
        warnings.extend(_finding(x, cfg.base, cwd) for x in w)
        if v:
            violating += 1
        for x in v:
            port_rules[x.rule] = port_rules.get(x.rule, 0) + 1
            findings.append(_finding(x, cfg.base, cwd))
        if w:
            continue  # AG000: the file does not parse, so it has no function metrics
        for m in function_metrics(ast.parse(src)):
            functions += 1
            for rule, attr, limit_key, unit in checks:
                value, limit = getattr(m, attr), q[limit_key]
                if value <= limit:
                    passed += 1
                    continue
                clean_rules[rule] = clean_rules.get(rule, 0) + 1
                viol = Violation(rule, disp, m.line,
                                 f"'{m.name}' has {value} {unit} (limit {limit})",
                                 RULES[rule][4], severity="warning")
                findings.append(_finding(viol, cfg.base, cwd, symbol=m.name,
                                         value=value, threshold=limit))
    findings.sort(key=lambda f: (f["path"], f["line"], f["rule"], f.get("symbol", "")))
    warnings.sort(key=lambda f: (f["path"], f["line"]))
    total_checks = functions * len(checks)
    result = {
        "schema_version": SCORE_SCHEMA_VERSION,
        "tool": {"name": "archguard", "version": VERSION},
        "config": _base_rel(str(cfg.path), cfg.base, cwd),
        "files_analyzed": files,
        "scores": {
            "portability": {
                "score": _pct(files - violating, files),
                "files": files,
                "clean_files": files - violating,
                "violations": sum(port_rules.values()),
                "by_rule": dict(sorted(port_rules.items())),
                "method": "percent of governed files with no AG001, AG002 or AG003 violation",
            },
            "cleanliness": {
                "score": _pct(passed, total_checks),
                "functions": functions,
                "checks": total_checks,
                "passed": passed,
                "thresholds": dict(q),
                "by_rule": dict(sorted(clean_rules.items())),
                "method": ("percent of per-function checks within thresholds "
                           "(complexity, length, parameters)"),
            },
        },
        "gates": {},
        "passed": True,
        "findings": findings,
        "warnings": warnings,
    }
    for dim, minimum in (("portability", min_portability), ("cleanliness", min_cleanliness)):
        if minimum is not None:
            ok = result["scores"][dim]["score"] >= minimum
            result["gates"][dim] = {"min": minimum, "passed": ok}
            result["passed"] = result["passed"] and ok
    return result


def format_score_text(r: dict) -> str:
    p, c = r["scores"]["portability"], r["scores"]["cleanliness"]
    lines = [
        f"portability {p['score']:.1f}/100  ({p['clean_files']}/{p['files']} files clean, "
        f"{p['violations']} violation(s))",
        f"cleanliness {c['score']:.1f}/100  ({c['passed']}/{c['checks']} checks over "
        f"{c['functions']} functions; limits: complexity {c['thresholds']['max_complexity']}, "
        f"lines {c['thresholds']['max_function_lines']}, "
        f"parameters {c['thresholds']['max_parameters']})",
    ]
    for f in r["findings"] + r["warnings"]:
        lines.append(f"{f['rule']} {f['path']}:{f['line'] or '?'} {f['message']}")
    for dim, g in r["gates"].items():
        lines.append(f"gate {dim} >= {g['min']}: {'pass' if g['passed'] else 'FAIL'}")
    return "\n".join(lines)


_SARIF_LEVEL = {"error": "error", "warning": "warning"}


def format_sarif(findings: list[dict], scores: dict | None = None) -> dict:
    """SARIF 2.1.0 log with one run. URIs are relative to the directory of archguard.toml."""
    used = sorted({f["rule"] for f in findings})
    rules = []
    for rid in used:
        name, dimension, level, desc, help_text = RULES.get(rid, (rid, "", "warning", rid, ""))
        rules.append({
            "id": rid,
            "name": name,
            "shortDescription": {"text": desc},
            "help": {"text": help_text},
            "defaultConfiguration": {"level": level},
            "properties": {"dimension": dimension},
        })
    index = {rid: i for i, rid in enumerate(used)}
    results = []
    for f in findings:
        loc = {"artifactLocation": {"uri": f["path"], "uriBaseId": "%SRCROOT%"}}
        if f["line"] and f["line"] > 0:
            loc["region"] = {"startLine": f["line"]}
        text = f["message"] + (f" Fix: {f['hint']}" if f["hint"] else "")
        results.append({
            "ruleId": f["rule"],
            "ruleIndex": index[f["rule"]],
            "level": _SARIF_LEVEL.get(f["severity"], "note"),
            "message": {"text": text},
            "locations": [{"physicalLocation": loc}],
            "partialFingerprints": {"archguardFindingId/v1": f["id"]},
            "properties": {"dimension": f["dimension"]},
        })
    run = {
        "tool": {"driver": {"name": "archguard", "version": VERSION, "rules": rules}},
        "columnKind": "unicodeCodePoints",
        "results": results,
    }
    if scores is not None:
        run["properties"] = {"scores": {k: v["score"] for k, v in scores.items()}}
    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [run],
    }


# ---------------------------------------------------------------------------
# hook
# ---------------------------------------------------------------------------


@dataclass
class Change:
    path: Path
    content: str | None = None
    fallback: list[tuple[int, str]] | None = None
    undeterminable: str = ""


WRITE_TOOLS = {"fs_write", "fsWrite", "write", "Write", "create", "create_file", "write_file"}
APPEND_TOOLS = {"fs_append", "fsAppend", "append"}
REPLACE_TOOLS = {"str_replace", "strReplace", "Edit", "edit", "replace"}
MULTI_EDIT_TOOLS = {"MultiEdit"}
SHELL_TOOLS = {"execute_bash", "executeBash", "shell", "Bash", "bash", "exec_command",
               "local_shell", "container.exec", "execute_cmd"}
PATCH_TOOLS = {"apply_patch", "applyPatch"}
_GIT_COMMIT_RE = re.compile(r"\bgit\s+(?:(?:-C|-c)\s+\S+\s+|--?[\w-]+(?:=\S+)?\s+)*commit\b")


def _first(d: dict, *keys: str):
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def _read(p: Path) -> str | None:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _resolve(p: str, cwd: Path) -> Path:
    q = Path(os.path.expanduser(p))
    return _realpath(q if q.is_absolute() else cwd / q)


def _replace(current: str | None, old, new, replace_all: bool) -> str | None:
    """Apply a str_replace/Edit. None means the edit cannot apply (the tool will fail)."""
    if not isinstance(old, str) or not isinstance(new, str):
        return None
    if current is None:
        return new if old == "" else None
    if old == "":
        return None
    if old not in current:
        return None
    return current.replace(old, new) if replace_all else current.replace(old, new, 1)


def parse_apply_patch(text: str) -> list[dict]:
    """Parse the Codex apply_patch envelope into file operations."""
    ops: list[dict] = []
    cur: dict | None = None
    in_patch = False
    for raw in text.splitlines():
        line = raw.rstrip("\r")
        if line.strip() == "*** Begin Patch":
            in_patch = True
            continue
        if not in_patch:
            continue
        if line.strip() == "*** End Patch":
            in_patch = False
            cur = None
            continue
        m = re.match(r"^\*\*\* (Add|Update|Delete) File: (.+)$", line)
        if m:
            cur = {"op": m.group(1).lower(), "path": m.group(2).strip(), "lines": [], "move_to": None}
            ops.append(cur)
            continue
        m = re.match(r"^\*\*\* Move to: (.+)$", line)
        if m and cur is not None:
            cur["move_to"] = m.group(1).strip()
            continue
        if cur is not None:
            cur["lines"].append(line)
    return ops


def _apply_hunks(current: str, lines: list[str]) -> str | None:
    """Apply Codex Update File hunks. Returns None if any hunk does not match."""
    src = current.splitlines()
    hunks: list[dict] = []
    h = None
    for line in lines:
        if line.startswith("@@"):
            h = {"ctx": line[2:].strip(), "old": [], "new": [], "eof": False}
            hunks.append(h)
            continue
        if line.strip() == "*** End of File":
            if h is not None:
                h["eof"] = True
            continue
        if h is None:
            h = {"ctx": "", "old": [], "new": [], "eof": False}
            hunks.append(h)
        if line.startswith("+"):
            h["new"].append(line[1:])
        elif line.startswith("-"):
            h["old"].append(line[1:])
        elif line.startswith(" "):
            h["old"].append(line[1:])
            h["new"].append(line[1:])
        elif line == "":
            h["old"].append("")
            h["new"].append("")
        else:
            return None
    cursor = 0
    for h in hunks:
        if h["ctx"]:
            idx = next((i for i in range(cursor, len(src)) if src[i].strip() == h["ctx"].strip()), None)
            if idx is None:
                return None
            cursor = idx + 1
        old, new = h["old"], h["new"]
        if not old:
            pos = len(src) if (h["eof"] or not h["ctx"]) else cursor
            src[pos:pos] = new
            cursor = pos + len(new)
            continue
        pos = None
        for strip in (False, True):
            for i in range(cursor, len(src) - len(old) + 1):
                window = src[i:i + len(old)]
                if (window == old) if not strip else ([w.rstrip() for w in window] == [o.rstrip() for o in old]):
                    pos = i
                    break
            if pos is not None:
                break
        if pos is None:
            return None
        src[pos:pos + len(old)] = new
        cursor = pos + len(new)
    return "\n".join(src) + ("\n" if src else "")


def _patch_changes(text: str, cwd: Path) -> list[Change]:
    changes = []
    for op in parse_apply_patch(text):
        path = _resolve(op["path"], cwd)
        if op["op"] == "delete":
            continue
        if op["op"] == "add":
            body = [ln[1:] if ln.startswith("+") else ln for ln in op["lines"]]
            changes.append(Change(path, content="\n".join(body) + ("\n" if body else "")))
            continue
        target = _resolve(op["move_to"], cwd) if op["move_to"] else path
        current = _read(path)
        content = _apply_hunks(current, op["lines"]) if current is not None else None
        if content is not None:
            changes.append(Change(target, content=content))
        else:
            added = [(0, ln[1:]) for ln in op["lines"] if ln.startswith("+")]
            changes.append(Change(target, fallback=added))
    return changes


def _strings(obj) -> list[str]:
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in _strings(v)]
    if isinstance(obj, list):
        return [s for v in obj for s in _strings(v)]
    return []


def extract_changes(payload: dict) -> tuple[list[Change], list[tuple[Path, bool]]]:
    """Return (file changes, git commit requests) implied by one tool call."""
    tool = str(payload.get("tool_name") or payload.get("toolName") or "")
    ti = payload.get("tool_input", payload.get("toolInput", {}))
    if isinstance(ti, str):
        try:
            ti = json.loads(ti)
        except ValueError:
            ti = {"command": ti}
    if not isinstance(ti, dict):
        ti = {}
    cwd_s = ti.get("cwd") if isinstance(ti.get("cwd"), str) else None
    cwd = _realpath(cwd_s or payload.get("cwd") or os.getcwd())
    changes: list[Change] = []
    commits: list[tuple[Path, bool]] = []

    path_v = _first(ti, "path", "file_path", "filePath")
    path = _resolve(path_v, cwd) if isinstance(path_v, str) and path_v else None

    patch_texts: list[str] = []
    if tool in PATCH_TOOLS | SHELL_TOOLS or (tool in {"Edit", "Write", "edit", "write"} and path is None):
        patch_texts = [s for s in _strings(ti) if "*** Begin Patch" in s]
    for s in patch_texts:
        changes.extend(_patch_changes(s, cwd))
    sub = ti.get("command") if isinstance(ti.get("command"), str) else None

    if tool in SHELL_TOOLS or (tool not in WRITE_TOOLS | APPEND_TOOLS | REPLACE_TOOLS
                               | MULTI_EDIT_TOOLS | PATCH_TOOLS and "command" in ti and path is None):
        cmd = ti.get("command")
        if isinstance(cmd, list):
            cmd = " ".join(str(c) for c in cmd)
        if isinstance(cmd, str) and _GIT_COMMIT_RE.search(cmd):
            m = re.search(r"\bgit\s+-C\s+(\S+)", cmd)
            gcwd = _resolve(m.group(1).strip("'\""), cwd) if m else cwd
            all_flag = bool(re.search(r"\bcommit\b.*(\s--all\b|\s-[a-zA-Z]*a[a-zA-Z]*\b)", cmd))
            commits.append((gcwd, all_flag))
        return changes, commits

    if patch_texts or path is None:
        if path is None and not patch_texts and tool in WRITE_TOOLS | APPEND_TOOLS | REPLACE_TOOLS | PATCH_TOOLS:
            changes.append(Change(Path(""), undeterminable="no target path in tool input"))
        return changes, commits

    current = _read(path)
    if tool in WRITE_TOOLS and sub in ("str_replace", "insert", "append", "create"):
        # Kiro CLI style write tool with a sub-command.
        if sub == "create":
            text = _first(ti, "file_text", "content", "text")
            changes.append(Change(path, content=text) if isinstance(text, str)
                           else Change(path, undeterminable="no file content in tool input"))
        elif sub == "str_replace":
            new = _replace(current, _first(ti, "old_str", "oldStr"), _first(ti, "new_str", "newStr"), False)
            if new is not None:
                changes.append(Change(path, content=new))
        elif sub == "append":
            text = _first(ti, "new_str", "text", "content")
            if isinstance(text, str):
                base = current or ""
                if base and not base.endswith("\n"):
                    base += "\n"
                changes.append(Change(path, content=base + text))
            else:
                changes.append(Change(path, undeterminable="no appended text in tool input"))
        else:  # insert
            text = _first(ti, "new_str", "text")
            n = ti.get("insert_line")
            if isinstance(text, str) and isinstance(n, int):
                lines = (current or "").splitlines()
                lines[n:n] = text.splitlines()
                changes.append(Change(path, content="\n".join(lines) + "\n"))
            else:
                changes.append(Change(path, undeterminable="cannot determine inserted text"))
    elif tool in WRITE_TOOLS:
        text = _first(ti, "text", "content", "file_text", "fileText")
        changes.append(Change(path, content=text) if isinstance(text, str)
                       else Change(path, undeterminable="no file content in tool input"))
    elif tool in APPEND_TOOLS:
        text = _first(ti, "text", "content", "new_str")
        if isinstance(text, str):
            base = current or ""
            if base and not base.endswith("\n"):
                base += "\n"
            changes.append(Change(path, content=base + text))
        else:
            changes.append(Change(path, undeterminable="no appended text in tool input"))
    elif tool in REPLACE_TOOLS:
        old = _first(ti, "oldStr", "old_string", "old_str")
        new = _first(ti, "newStr", "new_string", "new_str")
        if not isinstance(old, str) or not isinstance(new, str):
            changes.append(Change(path, undeterminable="missing old/new text in tool input"))
        else:
            result = _replace(current, old, new, bool(_first(ti, "replace_all", "replaceAll")))
            if result is not None:
                changes.append(Change(path, content=result))
    elif tool in MULTI_EDIT_TOOLS:
        content = current
        edits = ti.get("edits")
        if not isinstance(edits, list):
            changes.append(Change(path, undeterminable="missing edits in tool input"))
        else:
            for e in edits:
                if not isinstance(e, dict):
                    content = None
                    break
                content = _replace(content, e.get("old_string"), e.get("new_string"),
                                   bool(e.get("replace_all")))
                if content is None:
                    break
            if content is not None:
                changes.append(Change(path, content=content))
    return changes, commits


def _hook_config(option: str | None, payload: dict, changes: list[Change]) -> Config | None:
    if option:
        return load_config(Path(option))
    starts = []
    if isinstance(payload.get("cwd"), str):
        starts.append(Path(payload["cwd"]))
    starts += [c.path.parent for c in changes if str(c.path)]
    starts.append(Path.cwd())
    for s in starts:
        found = find_config(s)
        if found:
            return load_config(found)
    return None


def _format_block(violations: list[Violation], commit: bool, extra: list[str]) -> str:
    n = len(violations)
    head = ("archguard blocked this commit: " if commit else "archguard blocked this change: ")
    out = [head + (f"{n} boundary violation{'s' if n != 1 else ''}." if n else "the change could not be evaluated."), ""]
    for v in violations:
        out.append(f"{v.rule} {v.path}:{v.line if v.line else '?'} {v.message}")
        if v.text:
            out.append(f"  offending: {v.text}")
        if v.why:
            out.append(f"  why: {v.why}")
        out.append(f"  fix: {v.hint}")
        out.append("")
    out.extend(extra)
    out.append(BLOCK_SENTENCE + (" No commit was created." if commit else ""))
    out.append("Revise the change so it respects the layer rules, then retry.")
    return "\n".join(out)


def run_hook(agent: str, config_opt: str | None, stdin_text: str) -> tuple[int, str]:
    """Return (exit code, stderr text). Exit 0 allows, exit 2 blocks."""
    try:
        payload = json.loads(stdin_text)
    except ValueError:
        return EXIT_OK, ""
    if not isinstance(payload, dict):
        return EXIT_OK, ""
    changes, commits = extract_changes(payload)
    if not changes and not commits:
        return EXIT_OK, ""
    py_targets = [c for c in changes if c.path.suffix == ".py" or not str(c.path) or c.path == Path("")]
    try:
        cfg = _hook_config(config_opt, payload, changes)
    except ConfigError as exc:
        if py_targets:
            return EXIT_BLOCK, (f"archguard could not evaluate the change: {exc}\n"
                                f"Fix {CONFIG_NAME} first. {BLOCK_SENTENCE}")
        return EXIT_OK, ""
    if cfg is None:
        return EXIT_OK, ""
    cwd = _realpath(payload.get("cwd") or os.getcwd())
    violations: list[Violation] = []
    extra: list[str] = []
    governed = False
    try:
        for ch in changes:
            if not str(ch.path) or ch.path == Path(""):
                continue
            fi = classify_file(cfg, ch.path)
            if fi is None:
                continue
            governed = True
            disp = display_path(fi.path, cwd)
            if ch.undeterminable:
                extra.append(f"archguard could not evaluate the change to {disp}: {ch.undeterminable}. "
                             "Governed files are checked before every write, so this write was refused.")
                continue
            if ch.content is not None:
                v, _ = analyze_source(cfg, fi, ch.content, disp, fallback=True)
            elif ch.fallback is not None:
                refs, env = collect_regex(ch.fallback, fi.module, fi.is_package)
                v = evaluate(cfg, fi, refs, env, {}, disp)
                for item in v:
                    item.text = item.text or item.target
            else:
                extra.append(f"archguard could not evaluate the change to {disp}.")
                continue
            violations.extend(v)
        if violations or extra:
            return EXIT_BLOCK, _format_block(violations, False, extra)
        for gcwd, include_worktree in commits:
            try:
                rep = run_staged(cfg, gcwd, include_worktree)
            except ConfigError:
                continue
            if rep.violations:
                return EXIT_BLOCK, _format_block(rep.violations, True, [])
    except Exception as exc:  # fail closed only for governed files
        if governed:
            return EXIT_BLOCK, f"archguard could not evaluate the change: internal error {exc!r}. {BLOCK_SENTENCE}"
        return EXIT_OK, ""
    return EXIT_OK, ""


# ---------------------------------------------------------------------------
# scan (brownfield analysis, no config)
# ---------------------------------------------------------------------------

DOMAIN_NAMES = {"domain", "entities", "models", "core"}
APP_NAMES = {"application", "use_cases", "usecases", "services"}
PORT_NAMES = {"ports", "interfaces"}
INFRA_NAMES = {"infrastructure", "adapters", "repositories", "controllers", "api", "handlers"}
FRAMEWORKS = {"fastapi", "flask", "django", "starlette", "uvicorn", "mangum"}
SDKS = {"boto3", "botocore", "psycopg", "psycopg2", "sqlalchemy", "pymongo", "redis",
        "requests", "httpx", "aiohttp"}
SDK_CTORS = {
    "boto3.resource", "boto3.client", "boto3.Session", "boto3.session.Session",
    "psycopg.connect", "psycopg2.connect", "sqlalchemy.create_engine", "pymongo.MongoClient",
    "redis.Redis", "redis.StrictRedis", "redis.from_url", "httpx.Client", "httpx.AsyncClient",
    "requests.Session", "aiohttp.ClientSession",
}
ROUTE_ATTRS = {"get", "post", "put", "delete", "patch", "route", "api_route", "websocket"}
VALIDATION_EXC = {"ValueError", "TypeError", "ValidationError", "HTTPException"}
RESOURCE_CALLS = {"Table", "Bucket", "Queue", "Topic", "get_queue_url"}
RESOURCE_KWARGS = {"TableName", "Bucket", "QueueUrl", "QueueName", "TopicArn", "table_name",
                   "bucket_name", "queue_url"}
ADAPTER_DIRS = {"repositories", "adapters"}
BUSINESS = {"domain", "application", "ports"}
LAYER_RANK = {"domain": 0, "ports": 1, "application": 1, "infrastructure": 2}
_ADAPTER_CTOR = re.compile(r"^[A-Z]\w*(Repository|Adapter|Gateway|Store|Client)$")
_RESOURCE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{1,254}$")


@dataclass
class ModInfo:
    rel: str
    dotted: str
    layer: str
    imports: list = field(default_factory=list)       # (line, target, category, relpath|None)
    sdk_inst: list = field(default_factory=list)      # (line, call, import_time)
    env_reads: list = field(default_factory=list)     # (line, what)
    abc_classes: list = field(default_factory=list)
    routes: list = field(default_factory=list)        # (line, method, path, func)
    validation: list = field(default_factory=list)    # lines
    adapter_ctors: list = field(default_factory=list) # (line, name)
    resources: list = field(default_factory=list)     # (line, literal)
    classes: list = field(default_factory=list)
    parse_error: str = ""

    def deps(self, cat: str) -> list[str]:
        return sorted({t.split(".")[0] for _, t, c, _ in self.imports if c == cat})

    @property
    def package(self) -> str:
        parent = self.rel.rsplit("/", 1)[0] if "/" in self.rel else ""
        return parent or self.rel[:-3]


def _layer_guess(parts: tuple[str, ...]) -> str:
    for d in reversed(parts[:-1]):
        if d in PORT_NAMES:
            return "ports"
        if d in DOMAIN_NAMES:
            return "domain"
        if d in APP_NAMES:
            return "application"
        if d in INFRA_NAMES:
            return "infrastructure"
    stem = parts[-1][:-3]
    if stem in DOMAIN_NAMES:
        return "domain"
    if stem in PORT_NAMES:
        return "ports"
    if stem in APP_NAMES:
        return "application"
    if stem in INFRA_NAMES:
        return "infrastructure"
    return "unknown"


def _is_trivial(tree: ast.Module, name: str) -> bool:
    if name != "__init__.py":
        return False
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.Pass)):
            continue
        if isinstance(node, ast.Assign) and all(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets):
            continue
        return False
    return True


def _dotted(expr) -> str | None:
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        base = _dotted(expr.value)
        return f"{base}.{expr.attr}" if base else None
    return None


def _category(target: str, relative: bool, internal: set[str]) -> str:
    if relative:
        return "internal"
    top = target.split(".")[0]
    if top in FRAMEWORKS:
        return "framework"
    if top in SDKS:
        return "sdk"
    if top in sys.stdlib_module_names:
        return "stdlib"
    if top in internal:
        return "internal"
    return "third-party"


def _scan_module(path: Path, parts: tuple[str, ...], internal: set[str]) -> ModInfo | None:
    rel = "/".join(parts)
    mod_parts = list(parts[:-1]) + [parts[-1][:-3]]
    is_pkg = mod_parts[-1] == "__init__"
    if is_pkg:
        mod_parts = mod_parts[:-1]
    dotted = ".".join(mod_parts)
    mi = ModInfo(rel, dotted, _layer_guess(parts))
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(src)
    except (SyntaxError, ValueError, OSError) as exc:
        mi.parse_error = str(exc)
        return mi
    if _is_trivial(tree, parts[-1]):
        return None
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                mi.imports.append((node.lineno, a.name, _category(a.name, False, internal), None))
                if a.asname:
                    aliases[a.asname] = a.name
                else:
                    aliases[a.name.split(".")[0]] = a.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            relative = bool(node.level)
            target = (resolve_relative(dotted, is_pkg, node.level, node.module) if relative
                      else node.module or "")
            names = [a.name for a in node.names if a.name != "*"]
            if target:
                cat = _category(target, relative, internal)
                mi.imports.append((node.lineno, target, cat, "|".join(names)))
            else:
                for n in names:
                    mi.imports.append((node.lineno, n, _category(n, relative, internal), None))
            for a in node.names:
                aliases[a.asname or a.name] = f"{target}.{a.name}" if target else a.name
        elif isinstance(node, ast.ClassDef):
            mi.classes.append(node.name)
            bases = {(_dotted(b) or "").split(".")[-1] for b in node.bases}
            meta = any(k.arg == "metaclass" and (_dotted(k.value) or "").endswith("ABCMeta") for k in node.keywords)
            abstract = any(
                isinstance(it, (ast.FunctionDef, ast.AsyncFunctionDef))
                and any((_dotted(d) or "").endswith("abstractmethod") for d in it.decorator_list)
                for it in node.body)
            if bases & {"ABC", "Protocol"} or meta or abstract:
                mi.abc_classes.append(node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in node.decorator_list:
                if (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                        and d.func.attr in ROUTE_ATTRS and d.args
                        and isinstance(d.args[0], ast.Constant) and isinstance(d.args[0].value, str)
                        and d.args[0].value.startswith("/")):
                    mi.routes.append((node.lineno, d.func.attr, d.args[0].value, node.name))
        elif isinstance(node, ast.Raise) and node.exc is not None:
            exc = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
            if (_dotted(exc) or "").split(".")[-1] in VALIDATION_EXC:
                mi.validation.append(node.lineno)
    _, env = collect_ast(tree, dotted, is_pkg)
    mi.env_reads = sorted(set(env))
    for ln, t, c, _ in mi.imports:
        if t.split(".")[0] in ("dotenv", "decouple", "environs"):
            mi.env_reads.append((ln, t))

    def resolve_call(expr) -> str:
        name = _dotted(expr) or ""
        if not name:
            return ""
        first, _, rest = name.partition(".")
        base = aliases.get(first, first)
        return f"{base}.{rest}" if rest else base

    def visit(node, in_func: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Call):
                resolved = resolve_call(child.func)
                last = resolved.split(".")[-1] if resolved else ""
                first_str = (child.args[0].value if child.args and isinstance(child.args[0], ast.Constant)
                             and isinstance(child.args[0].value, str) else None)
                if resolved in SDK_CTORS:
                    shown = f"{resolved}({first_str!r})" if first_str else f"{resolved}()"
                    mi.sdk_inst.append((child.lineno, shown, not in_func))
                elif last and _ADAPTER_CTOR.match(last):
                    mi.adapter_ctors.append((child.lineno, last))
                if resolved in ("re.match", "re.fullmatch", "re.search"):
                    mi.validation.append(child.lineno)
                if first_str and resolved not in SDK_CTORS and _RESOURCE_NAME.match(first_str):
                    low = resolved.lower()
                    if last in RESOURCE_CALLS or any(k in low for k in ("dynamodb", "table", "bucket", "queue", "s3", "sqs", "sns")):
                        mi.resources.append((child.lineno, first_str))
                for kw in child.keywords:
                    if (kw.arg in RESOURCE_KWARGS and isinstance(kw.value, ast.Constant)
                            and isinstance(kw.value.value, str)):
                        mi.resources.append((child.lineno, kw.value.value))
            nested = in_func or isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
            visit(child, nested)

    visit(tree, False)
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id.isupper() and re.search(r"TABLE|BUCKET|QUEUE|TOPIC", t.id):
                    mi.resources.append((node.lineno, node.value.value))
    mi.resources = sorted(set(mi.resources))
    mi.validation = sorted(set(mi.validation))
    return mi


def _singular(word: str) -> str:
    w = word.lower().replace("-", "_")
    if w.endswith("ies"):
        return w[:-3] + "y"
    if w.endswith(("ses", "xes")):
        return w[:-2]
    if w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def _camel(snake: str) -> str:
    return "".join(p.capitalize() for p in snake.split("_") if p)


def _churn(path: Path, days: int) -> dict:
    top = _git(["rev-parse", "--show-toplevel"], path)
    if top.returncode != 0:
        return {"available": False, "reason": "not a git repository", "files": []}
    toplevel = _realpath(top.stdout.strip())
    log = _git(["log", f"--since={days}.days", "--numstat", "--format=", "--", "."], path)
    if log.returncode != 0:
        return {"available": False, "reason": log.stderr.strip() or "git log failed", "files": []}
    totals: dict[str, list[int]] = {}
    for line in log.stdout.splitlines():
        bits = line.split("\t")
        if len(bits) != 3:
            continue
        a, d, p = bits
        if "=>" in p:
            continue
        absolute = toplevel / p
        try:
            rel = absolute.relative_to(path).as_posix()
        except ValueError:
            continue
        t = totals.setdefault(rel, [0, 0, 0])
        t[0] += int(a) if a.isdigit() else 0
        t[1] += int(d) if d.isdigit() else 0
        t[2] += 1
    ranked = sorted(totals.items(), key=lambda kv: (-(kv[1][0] + kv[1][1]), kv[0]))[:10]
    return {"available": True, "days": days, "files": [
        {"file": f, "added": a, "deleted": d, "changed": a + d, "commits": c} for f, (a, d, c) in ranked]}


CLASSIFICATION_RULES = [
    "monolith: one module mixes route decorators, SDK client usage and business validation "
    "(checked first), or the tree has 3 or fewer non-test modules and no layer directories.",
    "hexagonal-clean: domain-like, ports-like (with ABC or Protocol classes) and "
    "infrastructure-like modules are present and there are zero inward violations.",
    "mvc: controllers, models and views (or templates) are all present.",
    "layered: layer directories are present but ports are missing or inward violations exist.",
    "mixed: anything else.",
    "Layer guess uses the innermost directory name: domain|entities|models|core = domain; "
    "application|use_cases|usecases|services = application; ports|interfaces = ports; "
    "infrastructure|adapters|repositories|controllers|api|handlers = infrastructure.",
    "Inward violation: a domain, application or ports module imports a framework or SDK, or "
    "imports an outer-layer module.",
    "Coupling score = round(100 * (business modules with at least one framework/SDK import + "
    "modules that create SDK clients at import time) / max(1, non-test modules)), capped at 100. "
    "0 means fully decoupled.",
    "Test files (tests/ directories, test_*.py, conftest.py) and __init__.py files without "
    "logic are excluded from module counts.",
]


def run_scan(path: str, churn_days: int = 90, cwd: Path | None = None) -> dict:
    cwd = _realpath(cwd or Path.cwd())
    root = _realpath(Path(path) if os.path.isabs(path) else cwd / path)
    if not root.is_dir():
        raise UsageError(f"scan path is not a directory: {root}")
    files = []
    tests = 0
    names: set[str] = set()
    for f in iter_py_files(root):
        parts = f.relative_to(root).parts
        names.update(parts[:-1])
        names.add(parts[-1][:-3])
        if is_test_path(parts):
            tests += 1
            continue
        files.append((f, parts))
    mods: list[ModInfo] = []
    for f, parts in files:
        mi = _scan_module(f, parts, names)
        if mi is not None:
            mods.append(mi)
    mods.sort(key=lambda m: m.rel)
    index: dict[str, str] = {}
    by_rel = {m.rel: m for m in mods}
    for m in sorted(mods, key=lambda m: (m.rel.count("/"), m.rel)):
        segs = m.dotted.split(".") if m.dotted else []
        for i in range(len(segs)):
            index.setdefault(".".join(segs[i:]), m.rel)

    def resolve(target: str, names_s: str | None) -> str | None:
        cands = [f"{target}.{n}" for n in (names_s or "").split("|") if n] + [target]
        for c in cands:
            segs = c.split(".")
            for n in range(len(segs), 0, -1):
                hit = index.get(".".join(segs[:n]))
                if hit:
                    return hit
        return None

    for m in mods:
        m.imports = [(ln, t, c, resolve(t, extra) if c == "internal" else None)
                     for ln, t, c, extra in m.imports]

    comp_roots = [m.rel for m in mods if m.env_reads and (m.adapter_ctors or m.sdk_inst)]
    inward = []
    outward_internal = []
    for m in mods:
        if m.layer in BUSINESS:
            ext = m.deps("framework") + m.deps("sdk")
            if ext:
                inward.append({"module": m.rel, "layer": m.layer, "imports": ext})
            for ln, t, c, r in m.imports:
                if r and r in by_rel:
                    tl = by_rel[r].layer
                    if tl in LAYER_RANK and LAYER_RANK[tl] > LAYER_RANK[m.layer]:
                        outward_internal.append({"module": m.rel, "line": ln, "imports": t, "target_layer": tl})
    adapter_imports = []
    for m in mods:
        if m.rel in comp_roots:
            continue
        for ln, t, c, r in m.imports:
            if not r:
                continue
            rdirs = set(r.split("/")[:-1])
            if rdirs & ADAPTER_DIRS and not (set(m.rel.split("/")[:-1]) & ADAPTER_DIRS):
                adapter_imports.append({"module": m.rel, "line": ln, "imports": t})
    env_outside = [{"module": m.rel, "line": ln, "read": w}
                   for m in mods if m.rel not in comp_roots for ln, w in m.env_reads]
    import_time = [{"module": m.rel, "line": ln, "call": call}
                   for m in mods for ln, call, top in m.sdk_inst if top]
    god = [m.rel for m in mods if m.routes and (m.deps("sdk") or m.sdk_inst) and m.validation]
    resources = [{"module": m.rel, "line": ln, "name": n} for m in mods for ln, n in m.resources]

    total = len(mods)
    coupled_business = sum(1 for m in mods if m.layer in BUSINESS and (m.deps("framework") or m.deps("sdk")))
    import_time_mods = len({x["module"] for x in import_time})
    score = min(100, round(100 * (coupled_business + import_time_mods) / max(1, total)))

    layers_present = {m.layer for m in mods}
    has_ports = any(m.layer == "ports" and m.abc_classes for m in mods)
    all_names = names
    mvc = (bool(all_names & {"controllers", "controller"}) and bool(all_names & {"models", "model"})
           and bool(all_names & {"views", "view", "templates"}))
    if god:
        cls, why = "monolith", f"{', '.join(god)} mixes route decorators, SDK client usage and business validation"
    elif {"domain", "infrastructure"} <= layers_present and has_ports and not inward and not outward_internal:
        cls, why = "hexagonal-clean", "domain, ports with ABC/Protocol and infrastructure present; no inward violations"
    elif mvc:
        cls, why = "mvc", "controllers, models and views/templates present"
    elif layers_present - {"unknown"}:
        missing = [] if has_ports else ["no ports with ABC/Protocol classes"]
        if inward or outward_internal:
            missing.append(f"{len(inward) + len(outward_internal)} inward violation(s)")
        cls, why = "layered", "layer directories present; " + "; ".join(missing or ["incomplete layer set"])
    elif total <= 3:
        cls, why = "monolith", f"{total} non-test module(s) and no layer directories"
    else:
        cls, why = "mixed", "no consistent layer structure detected"

    edges: dict[tuple[str, str], bool] = {}
    for m in mods:
        for ln, t, c, r in m.imports:
            if c in ("framework", "sdk"):
                key = (m.package, "ext:" + t.split(".")[0])
                bad = m.layer in BUSINESS or (c == "sdk" and (m.rel in god or any(top for _, _, top in m.sdk_inst)))
                edges[key] = edges.get(key, False) or bad
            elif r and r in by_rel and by_rel[r].package != m.package:
                tgt = by_rel[r]
                bad = (m.layer in LAYER_RANK and tgt.layer in LAYER_RANK
                       and LAYER_RANK[tgt.layer] > LAYER_RANK[m.layer])
                key = (m.package, tgt.package)
                edges[key] = edges.get(key, False) or bad

    result = {
        "path": display_path(root, cwd),
        "classification": cls,
        "classification_reason": why,
        "coupling_score": score,
        "modules_total": total,
        "test_modules": tests,
        "layers_detected": sorted(layers_present - {"unknown"}),
        "composition_roots": comp_roots,
        "rules": CLASSIFICATION_RULES,
        "findings": {
            "inward_violations": inward,
            "outward_internal_imports": outward_internal,
            "sdk_clients_at_import_time": import_time,
            "env_reads_outside_composition_root": env_outside,
            "adapter_imports_outside_composition_root": adapter_imports,
            "hard_coded_resource_names": resources,
            "god_modules": god,
            "parse_errors": [{"module": m.rel, "error": m.parse_error} for m in mods if m.parse_error],
        },
        "modules": [{
            "module": m.rel, "layer": m.layer,
            "framework": m.deps("framework"), "sdk": m.deps("sdk"),
            "third_party": m.deps("third-party"),
            "internal": sorted({by_rel[r].package for _, _, _, r in m.imports if r and r in by_rel and r != m.rel}),
            "stdlib": m.deps("stdlib"),
            "sdk_clients": [{"line": ln, "call": c, "import_time": t} for ln, c, t in m.sdk_inst],
            "env_reads": [{"line": ln, "read": w} for ln, w in m.env_reads],
            "abc_classes": m.abc_classes,
            "routes": [{"line": ln, "method": me, "path": p, "function": fn} for ln, me, p, fn in m.routes],
        } for m in mods],
        "edges": [{"from": a, "to": b, "violation": v} for (a, b), v in sorted(edges.items())],
        "churn": _churn(root, churn_days),
    }
    result["plan"] = _plan(result, mods)
    return result


def _entities(mods: list[ModInfo]) -> list[str]:
    found: list[str] = []
    for m in mods:
        if m.layer == "domain":
            found += [re.sub(r"(?<!^)(?=[A-Z])", "_", c).lower() for c in m.classes if not c.endswith(("Error", "Exception"))]
    for m in mods:
        for _, _, p, _ in m.routes:
            segs = [s for s in p.strip("/").split("/") if s and not s.startswith("{") and s not in ("api", "v1", "v2", "health")]
            if segs:
                found.append(_singular(segs[0]))
    for m in mods:
        for _, n in m.resources:
            found.append(_singular(re.sub(r"(?<!^)(?=[A-Z])", "_", n.split(".")[0]).lower()))
    out = []
    for e in found:
        e = re.sub(r"\W", "_", e).strip("_")
        if e and e not in out:
            out.append(e)
    return out or ["item"]


def _plan(r: dict, mods: list[ModInfo]) -> list[dict]:
    ag = display_path(_realpath(__file__))
    p = r["path"]
    f = r["findings"]
    ents = _entities(mods)
    domain = [m for m in mods if m.layer == "domain"]
    ports = [m for m in mods if m.layer == "ports" and m.abc_classes]
    app = [m for m in mods if m.layer == "application"]
    infra = [m for m in mods if m.layer == "infrastructure"]
    coupled = {x["module"]: x["imports"] for x in f["inward_violations"]}
    route_mods = [m for m in mods if m.routes]
    moves = []

    def move(n, title, status, reason, actions, verify):
        moves.append({"move": n, "title": title, "status": status, "reason": reason,
                      "actions": actions, "verify": verify})

    base_actions = [f"{m.rel}: {len(m.routes)} route(s) call the SDK directly ({', '.join(m.deps('sdk') or [c for _, c, _ in m.sdk_inst])})"
                    for m in route_mods if m.deps("sdk") or m.sdk_inst]
    base_actions += [f"{x['module']}:{x['line']} {x['call']} runs at import time" for x in f["sdk_clients_at_import_time"]]
    base_actions += [f"{x['module']}:{x['line']} hard-coded resource name '{x['name']}'" for x in f["hard_coded_resource_names"]]
    if base_actions:
        move(0, "Monolith (baseline)", "baseline", "Record the current coupling before moving code.", base_actions,
             [f"python3 {ag} scan {p} --format json > archguard-baseline.json",
              "Run the existing tests or one smoke request per route and keep the output as the behavior baseline."])
    else:
        move(0, "Monolith (baseline)", "skip",
             "No module calls an SDK from a route handler, creates SDK clients at import time, or hard-codes resource names.",
             [], [])

    if domain and not any(m.rel in coupled for m in domain):
        move(1, "Extract entities", "done",
             f"Domain modules exist and import no framework or SDK: {', '.join(m.rel for m in domain)}.", [], [])
    else:
        acts = [f"Create domain/{e}.py with the {_camel(e)} entity; it validates its own fields." for e in ents]
        acts += [f"Move validation out of {m.rel} (lines {', '.join(map(str, m.validation[:8]))}) into the entities."
                 for m in route_mods if m.validation]
        acts += [f"Remove {', '.join(coupled[m.rel])} from {m.rel}." for m in domain if m.rel in coupled]
        move(1, "Extract entities", "todo", "Validation still lives next to routes or SDK calls.", acts,
             ["Unit test: constructing each entity with invalid input raises ValueError (no network, no credentials).",
              f"python3 {ag} scan {p} :: domain modules show no framework/SDK deps in the boundary map."])

    if ports:
        move(2, "Define ports", "done",
             f"Ports with ABC/Protocol classes exist: {', '.join(m.rel for m in ports)}.", [], [])
    else:
        acts = [f"Create application/ports/{e}_repository.py: an ABC {_camel(e)}Repository with abstract "
                "create, find_by_id, find_all and delete (no implementation)." for e in ents]
        sdk_users = [m.rel for m in mods if m.deps("sdk")]
        if sdk_users:
            acts.append(f"Port methods mirror the data access calls currently made in: {', '.join(sdk_users)}.")
        move(2, "Define ports", "todo", "No ports-like module with ABC or Protocol classes was found.", acts,
             ["The port modules import only domain, typing and abc.",
              f"python3 {ag} check :: after init, no AG001 findings for application/ports."])
    extra_inward = [f"{x['module']} imports {', '.join(x['imports'])}: move that call behind a port."
                    for x in f["inward_violations"] if x["layer"] != "domain"]
    extra_inward += [f"{x['module']}:{x['line']} imports outer-layer module {x['imports']} ({x['target_layer']})."
                     for x in f["outward_internal_imports"]]
    if extra_inward:
        moves[-1]["status"] = "todo"
        moves[-1]["reason"] += " Inward violations remain."
        moves[-1]["actions"] += extra_inward

    app_clean = [m for m in app if m.rel not in coupled]
    if app_clean and len(app_clean) == len(app):
        move(3, "Extract use cases", "done",
             f"Application modules exist without framework/SDK imports: {', '.join(m.rel for m in app)}.", [], [])
    else:
        acts = []
        for m in route_mods:
            for ln, meth, rp, fn in m.routes:
                if "health" in rp:
                    continue
                acts.append(f"{m.rel}:{ln} {meth.upper()} {rp} ({fn}) -> application/use_cases/{fn}.py "
                            f"({_camel(fn)}UseCase takes and returns plain data or entities).")
        if not acts:
            acts = [f"Create application/use_cases/create_{e}.py orchestrating the {_camel(e)}Repository port." for e in ents]
        acts.append("ID generation (uuid4) and orchestration move into the create use cases.")
        acts += [f"Remove {', '.join(coupled[m.rel])} from {m.rel}." for m in app if m.rel in coupled]
        move(3, "Extract use cases", "todo", "Route handlers still orchestrate business work.", acts,
             ["Unit tests call each use case with an in-memory port; they pass with socket.socket blocked."])

    mem = [m for m in infra if re.search(r"memory|fake", m.rel.split("/")[-1])]
    if mem:
        move(4, "Write the first adapter", "done",
             f"In-memory adapters exist: {', '.join(m.rel for m in mem)}.", [], [])
    else:
        move(4, "Write the first adapter", "todo", "No in-memory adapter was found.",
             [f"Create infrastructure/repositories/in_memory_{e}_repository.py implementing {_camel(e)}Repository with a dict."
              for e in ents] + ["Add one contract test per port that runs against every adapter."],
             ["python -m pytest -q :: passes with no cloud credentials and no network."])

    comp_ok = r["composition_roots"] and not f["env_reads_outside_composition_root"] and not f["adapter_imports_outside_composition_root"]
    if comp_ok:
        move(5, "Add a composition root", "done",
             f"Composition root: {', '.join(r['composition_roots'])}; no config reads or adapter imports elsewhere.", [], [])
    else:
        acts = []
        if not r["composition_roots"]:
            acts.append("Create infrastructure/composition.py: the only module that reads configuration and selects adapters from a registry.")
        acts += [f"Move {x['read']} at {x['module']}:{x['line']} into the composition root and pass the value in."
                 for x in f["env_reads_outside_composition_root"]]
        acts += [f"Remove adapter import {x['imports']} at {x['module']}:{x['line']}; receive the port from the composition root."
                 for x in f["adapter_imports_outside_composition_root"]]
        acts += [f"Move {x['call']} at {x['module']}:{x['line']} into a factory called by the composition root."
                 for x in f["sdk_clients_at_import_time"]]
        move(5, "Add a composition root", "todo",
             "Configuration or adapter selection happens outside a single composition root.", acts,
             [f"python3 {ag} init && python3 {ag} check :: no AG002 or AG003 findings."])

    sdk_outside = [m.rel for m in mods if m.deps("sdk") and m.layer != "infrastructure"]
    entry = [m for m in infra if m.routes or re.search(r"handler|lambda|server|main", m.rel)]
    if not sdk_outside and not f["sdk_clients_at_import_time"] and entry:
        move(6, "Add real adapters and entrypoints", "done",
             f"SDK use is confined to infrastructure and entrypoints exist: {', '.join(m.rel for m in entry)}.", [], [])
    else:
        sdks = sorted({d for m in mods for d in m.deps("sdk")})
        hay = " ".join([c for m in mods for _, c, _ in m.sdk_inst] + [m.rel for m in mods]).lower()
        backend = "dynamodb" if "dynamodb" in hay else (sdks[0] if sdks else "real")
        acts = [f"Create infrastructure/repositories/{backend}_{e}_repository.py implementing {_camel(e)}Repository." for e in ents]
        acts += [f"Move SDK usage out of {x} into those adapters." for x in sdk_outside]
        acts.append("Move HTTP routes to infrastructure/http/ (routes call use cases only) and add entrypoints "
                    "(HTTP server, Lambda handler) that call the composition root.")
        move(6, "Add real adapters and entrypoints", "todo",
             "SDK calls live outside infrastructure, run at import time, or no entrypoint exists yet.", acts,
             ["Run the port contract tests against the real adapter using a local emulator behind an opt-in flag.",
              f"python3 {ag} scan {p} :: classification hexagonal-clean, coupling score 0."])
    return moves


def _md_cell(s: str) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def _node_id(name: str) -> str:
    return "n_" + re.sub(r"\W", "_", name)


def format_scan_md(r: dict) -> str:
    f = r["findings"]
    out = [f"# archguard scan: {r['path']}", "",
           f"- Classification: {r['classification']} ({r['classification_reason']})",
           f"- Coupling score: {r['coupling_score']} (0 = fully decoupled)",
           f"- Modules: {r['modules_total']} non-test, {r['test_modules']} test",
           f"- Layers detected: {', '.join(r['layers_detected']) or 'none'}",
           f"- Composition root: {', '.join(r['composition_roots']) or 'none detected'}", "",
           "## Classification rules", ""]
    out += [f"- {rule}" for rule in r["rules"]]
    out += ["", "## Findings", ""]
    labels = [
        ("inward_violations", "Inward violations (business module imports framework/SDK)",
         lambda x: f"{x['module']} ({x['layer']}) imports {', '.join(x['imports'])}"),
        ("outward_internal_imports", "Business module imports an outer layer",
         lambda x: f"{x['module']}:{x['line']} imports {x['imports']} ({x['target_layer']})"),
        ("sdk_clients_at_import_time", "SDK clients created at import time",
         lambda x: f"{x['module']}:{x['line']} {x['call']}"),
        ("env_reads_outside_composition_root", "Config reads outside the composition root",
         lambda x: f"{x['module']}:{x['line']} {x['read']}"),
        ("adapter_imports_outside_composition_root", "Adapter imports outside the composition root",
         lambda x: f"{x['module']}:{x['line']} imports {x['imports']}"),
        ("hard_coded_resource_names", "Hard-coded resource names",
         lambda x: f"{x['module']}:{x['line']} '{x['name']}'"),
        ("god_modules", "Modules mixing routes, SDK calls and validation", lambda x: x),
        ("parse_errors", "Parse errors", lambda x: f"{x['module']}: {x['error']}"),
    ]
    for key, title, fmt in labels:
        items = f[key]
        out.append(f"- {title}: {len(items)}")
        out += [f"  - {fmt(x)}" for x in items[:25]]
        if len(items) > 25:
            out.append(f"  - ... {len(items) - 25} more")
    out += ["", "## Boundary map", "",
            "| Module | Layer guess | Framework/SDK deps | Other third-party | Internal deps | Notes |",
            "|---|---|---|---|---|---|"]
    for m in r["modules"]:
        notes = []
        if m["routes"]:
            notes.append(f"{len(m['routes'])} route(s)")
        if any(c["import_time"] for c in m["sdk_clients"]):
            notes.append("SDK client at import time")
        elif m["sdk_clients"]:
            notes.append("SDK client in function")
        if m["env_reads"]:
            notes.append("reads env")
        if m["abc_classes"]:
            notes.append("ABC/Protocol: " + ", ".join(m["abc_classes"]))
        out.append("| " + " | ".join(_md_cell(x) for x in [
            m["module"], m["layer"], ", ".join(m["framework"] + m["sdk"]) or "-",
            ", ".join(m["third_party"]) or "-", ", ".join(m["internal"]) or "-", "; ".join(notes) or "-"]) + " |")
    out += ["", "## Package dependencies", "", "Red edges point from a business package to a framework/SDK or to an outer layer.", "",
            "```mermaid", "flowchart LR"]
    nodes: dict[str, str] = {}
    for e in r["edges"]:
        for n in (e["from"], e["to"]):
            if n not in nodes:
                nodes[n] = (("ext_" + re.sub(r"\W", "_", n[4:])) if n.startswith("ext:") else _node_id(n))
    for n, nid in nodes.items():
        label = f"{n[4:]} (external)" if n.startswith("ext:") else n
        out.append(f'  {nid}["{label}"]')
    red = []
    for i, e in enumerate(r["edges"]):
        out.append(f"  {nodes[e['from']]} --> {nodes[e['to']]}")
        if e["violation"]:
            red.append(str(i))
    if red:
        out.append(f"  linkStyle {','.join(red)} stroke:#d62728,stroke-width:2px")
    out += ["```", "", f"## Churn (last {r['churn'].get('days', '?')} days)", ""]
    if not r["churn"]["available"]:
        out.append(f"Skipped: {r['churn']['reason']}.")
    elif not r["churn"]["files"]:
        out.append("No commits touched this path in the window.")
    else:
        out += ["| File | Lines changed | Added | Deleted | Commits |", "|---|---|---|---|---|"]
        out += [f"| {_md_cell(c['file'])} | {c['changed']} | {c['added']} | {c['deleted']} | {c['commits']} |"
                for c in r["churn"]["files"]]
    out += ["", "## Refactor plan (six moves)", ""]
    for mv in r["plan"]:
        out.append(f"### Move {mv['move']}: {mv['title']} [{mv['status']}]")
        out.append("")
        out.append(mv["reason"])
        if mv["actions"]:
            out.append("")
            out += [f"- {a}" for a in mv["actions"]]
        if mv["verify"]:
            out.append("")
            out.append("Verify:")
            for v in mv["verify"]:
                cmd, sep, expect = v.partition(" :: ")
                if cmd.startswith("python"):
                    out.append(f"- `{cmd}`" + (f": {expect}" if sep else ""))
                else:
                    out.append(f"- {v}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

CORE_ALLOW = ["typing", "dataclasses", "re", "uuid", "datetime", "decimal", "enum", "abc",
              "__future__", "collections", "functools", "itertools", "math", "numbers", "string"]


def detect_source_roots(root: Path, max_depth: int = 5) -> list[str]:
    found: list[Path] = []
    for dirpath, dirnames, _ in os.walk(root):
        d = Path(dirpath)
        depth = len(d.relative_to(root).parts)
        dirnames[:] = sorted(x for x in dirnames if x not in SKIP_DIRS and not x.startswith(".") and x != "tests")
        if depth >= max_depth:
            dirnames[:] = []
        names = set(dirnames)
        if names & DOMAIN_NAMES and names & (APP_NAMES | INFRA_NAMES):
            if not any(d.is_relative_to(f) for f in found):
                found.append(d)
            dirnames[:] = []
    rels = [f.relative_to(root).as_posix() or "." for f in found]
    if rels:
        return rels
    return ["src"] if (root / "src").is_dir() else ["."]


def render_init(root: Path, source_roots: list[str]) -> str:
    def present(names: list[str]) -> list[str]:
        hits = [n for n in names if any((root / s / n).is_dir() for s in source_roots)]
        return hits

    domain = present(["domain", "entities", "core"]) or ["domain"]
    app = present(["application", "use_cases", "usecases"]) or ["application"]
    infra = present(["infrastructure", "adapters"]) or ["infrastructure"]
    comp = [c for c in ["infrastructure/composition.py", "infrastructure/config.py",
                        "composition.py", "container.py", "bootstrap.py"]
            if any((root / s / c).is_file() for s in source_roots)]
    for c in ["infrastructure/composition.py", "infrastructure/config.py"]:
        if c not in comp:
            comp.append(c)
    adapters = [a for a in ["infrastructure.repositories", "infrastructure.adapters", "adapters"]
                if any((root / s / a.replace(".", "/")).is_dir() for s in source_roots)] or ["infrastructure.repositories"]

    def arr(xs: list[str]) -> str:
        return "[" + ", ".join(json.dumps(x) for x in xs) + "]"

    return "\n".join([
        "# archguard configuration. Generated by 'archguard init'; review before committing.",
        "# Paths are relative to this file; layer paths are relative to each source root.",
        "[project]",
        f"source_roots = {arr(source_roots)}",
        "",
        "[layers.domain]",
        f"paths = {arr(domain)}",
        f"allow = {arr(domain + CORE_ALLOW)}",
        "",
        "[layers.application]",
        f"paths = {arr(app)}",
        f"allow = {arr(domain + app + CORE_ALLOW)}",
        "",
        "[layers.infrastructure]",
        f"paths = {arr(infra)}",
        'allow = ["*"]',
        "",
        "[rules]",
        f"composition_roots = {arr(comp)}",
        f"adapter_packages = {arr(adapters)}",
        'config_read_modules = ["dotenv", "decouple", "environs"]',
        "",
    ])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):  # exit 3, never 2, so a bad hook command line cannot block writes
        self.print_usage(sys.stderr)
        self.exit(EXIT_CONFIG, f"{self.prog}: error: {message}\n")


def build_parser() -> argparse.ArgumentParser:
    p = _Parser(prog="archguard", description="Clean-architecture boundary checks for Python.")
    p.add_argument("--version", action="version", version=f"archguard {VERSION}")
    sub = p.add_subparsers(dest="cmd", required=True, parser_class=_Parser)
    c = sub.add_parser("check", help="check files or directories")
    c.add_argument("paths", nargs="*")
    c.add_argument("--config")
    c.add_argument("--format", choices=["text", "json", "sarif"], default="text")
    s = sub.add_parser("staged", help="check staged content of .py files")
    s.add_argument("--config")
    s.add_argument("--format", choices=["text", "json", "sarif"], default="text")
    so = sub.add_parser("score", help="portability and cleanliness scores with findings")
    so.add_argument("paths", nargs="*")
    so.add_argument("--config")
    so.add_argument("--format", choices=["text", "json", "sarif"], default="text")
    so.add_argument("--min-portability", type=float, metavar="N",
                    help="exit 1 when the portability score is below N (0-100)")
    so.add_argument("--min-cleanliness", type=float, metavar="N",
                    help="exit 1 when the cleanliness score is below N (0-100)")
    h = sub.add_parser("hook", help="evaluate one agent tool call from stdin")
    h.add_argument("--agent", choices=["kiro", "claude", "codex"], required=True)
    h.add_argument("--config")
    sc = sub.add_parser("scan", help="brownfield analysis without a config")
    sc.add_argument("path", nargs="?", default=".")
    sc.add_argument("--format", choices=["md", "json"], default="md")
    sc.add_argument("--churn-days", type=int, default=90)
    i = sub.add_parser("init", help="write a starter archguard.toml")
    i.add_argument("--root", default=".")
    i.add_argument("--source-root", action="append", dest="source_roots")
    i.add_argument("--force", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cwd = _realpath(Path.cwd())
    if args.cmd == "hook":
        code, err = run_hook(args.agent, args.config, sys.stdin.read())
        if err:
            sys.stderr.write(err.rstrip() + "\n")
        return code
    try:
        if args.cmd in ("check", "staged"):
            cfg = resolve_config(args.config, cwd)
            rep = run_check(cfg, args.paths, cwd) if args.cmd == "check" else run_staged(cfg, cwd)
            print(format_report(rep, args.format, cfg, cwd))
            return EXIT_VIOLATIONS if rep.violations else EXIT_OK
        if args.cmd == "score":
            for gate in (args.min_portability, args.min_cleanliness):
                if gate is not None and not 0 <= gate <= 100:
                    raise UsageError("score gates must be between 0 and 100")
            cfg = resolve_config(args.config, cwd)
            r = run_score(cfg, args.paths, cwd, args.min_portability, args.min_cleanliness)
            if args.format == "json":
                print(json.dumps(r, indent=2))
            elif args.format == "sarif":
                print(json.dumps(format_sarif(r["findings"] + r["warnings"], r["scores"]), indent=2))
            else:
                print(format_score_text(r))
            return EXIT_OK if r["passed"] else EXIT_VIOLATIONS
        if args.cmd == "scan":
            r = run_scan(args.path, args.churn_days, cwd)
            print(json.dumps(r, indent=2) if args.format == "json" else format_scan_md(r), end="" if args.format == "md" else "\n")
            return EXIT_OK
        if args.cmd == "init":
            root = _realpath(args.root)
            if not root.is_dir():
                raise UsageError(f"not a directory: {root}")
            target = root / CONFIG_NAME
            if target.exists() and not args.force:
                raise UsageError(f"{target} already exists (use --force to overwrite)")
            roots = args.source_roots or detect_source_roots(root)
            for sr in roots:
                if not (root / sr).is_dir():
                    raise UsageError(f"source root does not exist: {root / sr}")
            target.write_text(render_init(root, roots), encoding="utf-8")
            print(f"wrote {target} (source_roots: {', '.join(roots)})")
            return EXIT_OK
    except (ConfigError, UsageError) as exc:
        sys.stderr.write(f"archguard: {exc}\n")
        return EXIT_CONFIG
    return EXIT_CONFIG


if __name__ == "__main__":
    sys.exit(main())
