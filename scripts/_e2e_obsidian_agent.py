# -*- coding: utf-8 -*-
"""端到端：绘图 Case 双写 vault + Agent MCP search_cases/get_case 学习链路。"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
BASE = "http://127.0.0.1:5000"


def http(method: str, path: str, body=None):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    print("=== 1. annotations/config ===")
    cfg = http("GET", "/api/annotations/config")
    print(json.dumps(cfg.get("data"), ensure_ascii=False, indent=2))
    assert cfg["data"]["vault_writable"] is True

    print("\n=== 2. POST chart-annotation (模拟绘图采集) ===")
    payload = {
        "type": "chart-annotation",
        "symbol": "sh000001",
        "symbol_name": "上证指数",
        "asset_type": "index",
        "period": "daily",
        "notes": "e2e: 画水平线后保存到 Obsidian，供 Agent 复述学习",
        "intent": "drawing_capture",
        "tags": ["采集/图表画线", "e2e"],
        "overlays": [
            {
                "id": "e2e_hl",
                "type": "horizontalLine",
                "points": [{"timestamp": 1784246400000, "value": 3764.15}],
            }
        ],
    }
    created = http("POST", "/api/annotations", payload)
    case = created["data"]
    cid = case["id"]
    print("case_id=", cid)
    print("vault=", case.get("vault"))
    md = Path(case["vault"]["abs_md"])
    assert md.is_file(), md
    text = md.read_text(encoding="utf-8")
    assert "e2e" in text and "horizontalLine" in text
    print("md_ok lines=", len(text.splitlines()))

    print("\n=== 3. MCP tools 含 search_cases/get_case ===")
    tools = http("GET", "/mcp/tools")
    names = [t["name"] for t in tools.get("tools") or []]
    print("tools_count=", len(names))
    for need in ("search_cases", "get_case", "search_relations", "list_due_reminders"):
        assert need in names, f"missing {need} in {names}"
        print("  ok", need)

    print("\n=== 4. MCP search_cases ===")
    sc = http(
        "POST",
        "/mcp/call",
        {"tool": "search_cases", "arguments": {"q": "e2e", "limit": 10}},
    )
    print("hint=", sc.get("agent_hint", "")[:80])
    print("learning_mode=", sc.get("learning_mode"))
    assert sc.get("success") is True
    assert sc.get("count", 0) >= 1
    ids = [x.get("id") for x in sc.get("data") or []]
    assert cid in ids, ids

    print("\n=== 5. MCP get_case (vault_md for Agent) ===")
    gc = http(
        "POST",
        "/mcp/call",
        {"tool": "get_case", "arguments": {"case_id": cid}},
    )
    assert gc.get("success") is True
    data = gc["data"]
    assert data.get("vault_md")
    assert "e2e" in data["vault_md"]
    assert gc.get("learning_mode") == "retrieval_augmented_quote_only"
    print("vault_md_len=", len(data["vault_md"]))
    print("agent_hint=", gc.get("agent_hint")[:100])

    print("\n=== 6. /api/mcp/call 兼容转发 ===")
    sc2 = http(
        "POST",
        "/api/mcp/call",
        {"tool": "search_cases", "params": {"symbol": "sh000001", "limit": 5}},
    )
    assert sc2.get("success") is True
    print("compat count=", sc2.get("count"))

    print("\nALL PASS — drawing→Obsidian→Agent learning chain OK")
    print(json.dumps({"case_id": cid, "md": str(md)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
