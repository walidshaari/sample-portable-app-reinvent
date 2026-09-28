"""Drive mcp_server.py over stdio with subprocess, as an MCP client would."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERVER = HERE.parent / "mcp_server.py"
FIX = HERE / "fixtures"


def exchange(messages: list[dict], cwd: Path) -> list[dict]:
    stdin = "".join(json.dumps(m) + "\n" for m in messages) + "not json\n"
    p = subprocess.run([sys.executable, str(SERVER)], input=stdin, capture_output=True, text=True,
                       cwd=str(cwd), timeout=60,
                       env={"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")})
    assert p.returncode == 0, p.stderr
    return [json.loads(line) for line in p.stdout.splitlines() if line.strip()]


def test_mcp_session(tmp_path):
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "archguard_scan", "arguments": {"path": str(FIX / "monolith"), "churn_days": 7}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "archguard_check", "arguments": {"root": str(FIX / "violating")}}},
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
         "params": {"name": "archguard_check", "arguments": {"config": str(FIX / "clean" / "archguard.toml")}}},
        {"jsonrpc": "2.0", "id": 6, "method": "tools/call",
         "params": {"name": "archguard_check", "arguments": {"root": str(tmp_path)}}},
        {"jsonrpc": "2.0", "id": 7, "method": "does/not/exist"},
        {"jsonrpc": "2.0", "id": 8, "method": "tools/call",
         "params": {"name": "archguard_score",
                    "arguments": {"root": str(FIX / "violating"), "min_portability": 90}}},
        {"jsonrpc": "2.0", "id": 9, "method": "tools/call",
         "params": {"name": "archguard_score", "arguments": {"root": str(FIX / "violating"),
                                                             "format": "sarif"}}},
        {"jsonrpc": "2.0", "id": 10, "method": "tools/call",
         "params": {"name": "archguard_score", "arguments": {"root": str(FIX / "violating"),
                                                              "min_cleanliness": "high"}}},
    ]
    responses = exchange(msgs, tmp_path)
    by_id = {r.get("id"): r for r in responses}
    assert len(responses) == 11  # 10 requests + 1 parse error, no reply to the notification
    init = by_id[1]["result"]
    assert init["protocolVersion"] == "2025-03-26"
    assert init["serverInfo"]["name"] == "archguard"
    assert init["capabilities"] == {"tools": {}}
    assert [t["name"] for t in by_id[2]["result"]["tools"]] == ["archguard_scan", "archguard_check",
                                                                     "archguard_score"]
    scan = by_id[3]["result"]
    assert scan["isError"] is False and "Classification: monolith" in scan["content"][0]["text"]
    check = by_id[4]["result"]["content"][0]["text"]
    assert "AG001 " in check and "status: violations found" in check
    assert "status: clean" in by_id[5]["result"]["content"][0]["text"]
    assert by_id[6]["result"]["isError"] is True
    assert by_id[7]["error"]["code"] == -32601
    assert by_id[None]["error"]["code"] == -32700
    score = json.loads(by_id[8]["result"]["content"][0]["text"])
    assert score["scores"]["portability"]["score"] == 42.9 and score["passed"] is False
    sarif = json.loads(by_id[9]["result"]["content"][0]["text"])
    assert sarif["version"] == "2.1.0" and sarif["runs"][0]["results"]
    assert by_id[10]["result"]["isError"] is True


def test_mcp_default_protocol_version(tmp_path):
    r = exchange([{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}], tmp_path)
    assert r[0]["result"]["protocolVersion"] == "2025-06-18"
