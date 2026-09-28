#!/usr/bin/env python3
"""Minimal MCP stdio server that exposes archguard as two tools.

Transport: JSON-RPC 2.0, one JSON message per line on stdin/stdout.
Standard library only. Logs go to stderr; stdout carries protocol messages only.

Tools:
  archguard_scan   brownfield analysis of a directory (no config needed)
  archguard_check  boundary check against archguard.toml
  archguard_score  portability and cleanliness scores with findings (json or sarif)
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import archguard  # noqa: E402

DEFAULT_PROTOCOL = "2025-06-18"

TOOLS = [
    {
        "name": "archguard_scan",
        "description": (
            "Analyze a Python source tree without configuration: classify it (hexagonal-clean, "
            "layered, mvc, monolith, mixed), compute a coupling score, and return a boundary map, "
            "a Mermaid dependency graph, git churn and a six-move refactor plan as Markdown. "
            "Use an absolute path."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path of the directory to scan."},
                "churn_days": {"type": "integer", "description": "Git churn window in days (default 90).",
                               "minimum": 1},
                "format": {"type": "string", "enum": ["md", "json"], "description": "Output format (default md)."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "archguard_check",
        "description": (
            "Check Python files against archguard.toml layer rules (AG001 layer imports, AG002 config "
            "reads outside the composition root, AG003 adapter imports outside the composition root). "
            "Pass config, or root to search for archguard.toml upward from that directory."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "paths": {"type": "array", "items": {"type": "string"},
                          "description": "Files or directories to check. Default: all governed files."},
                "config": {"type": "string", "description": "Path to archguard.toml."},
                "root": {"type": "string",
                         "description": "Directory used to find archguard.toml and to resolve relative paths."},
                "format": {"type": "string", "enum": ["text", "json", "sarif"]},
            },
        },
    },
    {
        "name": "archguard_score",
        "description": (
            "Score governed Python files against archguard.toml: portability (percent of files with "
            "no AG001-AG003 violation) and cleanliness (percent of per-function checks within the "
            "complexity, length and parameter limits). Returns JSON with stable finding ids, or "
            "SARIF 2.1.0. Pass config, or root to search for archguard.toml upward."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "paths": {"type": "array", "items": {"type": "string"},
                          "description": "Files or directories to score. Default: all governed files."},
                "config": {"type": "string", "description": "Path to archguard.toml."},
                "root": {"type": "string",
                         "description": "Directory used to find archguard.toml and to resolve relative paths."},
                "format": {"type": "string", "enum": ["json", "sarif"],
                           "description": "Output format (default json)."},
                "min_portability": {"type": "number", "minimum": 0, "maximum": 100},
                "min_cleanliness": {"type": "number", "minimum": 0, "maximum": 100},
            },
        },
    },
]


def _text(text: str, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def call_tool(name: str, args: dict) -> dict:
    try:
        if name == "archguard_scan":
            path = args.get("path")
            if not isinstance(path, str) or not path:
                return _text("archguard_scan requires 'path'", True)
            days = int(args.get("churn_days") or 90)
            result = archguard.run_scan(path, days)
            if args.get("format") == "json":
                return _text(json.dumps(result, indent=2))
            return _text(archguard.format_scan_md(result))
        if name in ("archguard_check", "archguard_score"):
            root = Path(args["root"]).expanduser() if args.get("root") else Path.cwd()
            config = args.get("config")
            if config and not Path(config).is_absolute():
                config = str(root / config)
            cfg = archguard.resolve_config(config, root)
            paths = args.get("paths") or None
            if paths is not None and (not isinstance(paths, list) or not all(isinstance(p, str) for p in paths)):
                return _text(f"{name} 'paths' must be a list of strings", True)
            cwd = archguard._realpath(root)
        if name == "archguard_score":
            gates = []
            for key in ("min_portability", "min_cleanliness"):
                value = args.get(key)
                if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                          or not 0 <= value <= 100):
                    return _text(f"archguard_score '{key}' must be a number from 0 to 100", True)
                gates.append(value)
            r = archguard.run_score(cfg, paths, cwd, *gates)
            if args.get("format") == "sarif":
                return _text(json.dumps(archguard.format_sarif(r["findings"] + r["warnings"],
                                                               r["scores"]), indent=2))
            return _text(json.dumps(r, indent=2))
        if name == "archguard_check":
            report = archguard.run_check(cfg, paths, cwd)
            text = archguard.format_report(report, args.get("format") or "text", cfg, cwd)
            status = "violations found" if report.violations else "clean"
            return _text(f"{text}\n\nstatus: {status}")
        return _text(f"unknown tool: {name}", True)
    except (archguard.ConfigError, archguard.UsageError) as exc:
        return _text(f"archguard: {exc}", True)
    except Exception as exc:  # report, do not crash the server
        return _text(f"archguard internal error: {exc!r}", True)


def handle(msg: dict) -> dict | None:
    method = msg.get("method")
    mid = msg.get("id")
    is_request = "id" in msg
    params = msg.get("params") or {}
    if method == "initialize":
        version = params.get("protocolVersion") if isinstance(params, dict) else None
        result = {
            "protocolVersion": version or DEFAULT_PROTOCOL,
            "serverInfo": {"name": "archguard", "version": archguard.VERSION},
            "capabilities": {"tools": {}},
        }
    elif method in ("notifications/initialized", "initialized") or (method or "").startswith("notifications/"):
        return None
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return _error(mid, -32602, "arguments must be an object")
        # Keep stray prints from ever reaching the protocol stream.
        with contextlib.redirect_stdout(io.StringIO()):
            result = call_tool(name, arguments)
    else:
        return _error(mid, -32601, f"method not found: {method}") if is_request else None
    return {"jsonrpc": "2.0", "id": mid, "result": result} if is_request else None


def _error(mid, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def main() -> int:
    out = sys.stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            out.write(json.dumps(_error(None, -32700, "parse error")) + "\n")
            out.flush()
            continue
        if not isinstance(msg, dict):
            out.write(json.dumps(_error(None, -32600, "invalid request")) + "\n")
            out.flush()
            continue
        response = handle(msg)
        if response is not None:
            out.write(json.dumps(response) + "\n")
            out.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
