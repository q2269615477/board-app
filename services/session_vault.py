# -*- coding: utf-8 -*-
"""会话 → Obsidian vault 写入（按 会话/图表/因/果 组织）。"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import quote


def _vault_root() -> Path:
    from core.config import ANNOTATION_VAULT_PATH

    root = Path(ANNOTATION_VAULT_PATH)
    (root / "sessions").mkdir(parents=True, exist_ok=True)
    return root


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _yaml_esc(s: Any) -> str:
    if s is None:
        return '""'
    s = str(s)
    if any(c in s for c in (":", "#", "\n", '"', "'")):
        return json.dumps(s, ensure_ascii=False)
    return s


def build_obsidian_uri(rel_path: str) -> str:
    from core.config import OBSIDIAN_VAULT_NAME

    vault = OBSIDIAN_VAULT_NAME or _vault_root().name
    file_no_ext = rel_path.replace("\\", "/").removesuffix(".md")
    return f"obsidian://open?vault={quote(vault)}&file={quote(file_no_ext)}"


def write_session_to_vault(session: Dict[str, Any]) -> Dict[str, str]:
    """写入完整会话树，返回路径信息。"""
    root = _vault_root()
    sid = session["id"]
    day = (session.get("created_at") or "")[:10].replace("-", "")
    if len(day) != 8:
        from datetime import datetime

        day = datetime.now().strftime("%Y%m%d")
    base_rel = f"sessions/{day[:4]}-{day[4:6]}-{day[6:8]}/{sid}"
    base = root / base_rel
    (base / "charts").mkdir(parents=True, exist_ok=True)
    (base / "causes").mkdir(parents=True, exist_ok=True)
    (base / "effects").mkdir(parents=True, exist_ok=True)
    (base / "attachments").mkdir(parents=True, exist_ok=True)

    # snapshot
    snap_rel = f"{base_rel}/session.snapshot.json"
    _atomic_write(
        root / snap_rel,
        json.dumps(session, ensure_ascii=False, indent=2),
    )

    # charts
    chart_links: List[str] = []
    for ch in session.get("charts") or []:
        cid = ch["id"]
        md_rel = f"{base_rel}/charts/{cid}.md"
        ov_rel = f"{base_rel}/attachments/{cid}.overlays.json"
        _atomic_write(
            root / ov_rel,
            json.dumps(
                {
                    "chart_id": cid,
                    "symbol": ch.get("symbol"),
                    "period": ch.get("period"),
                    "visible_range": ch.get("visible_range"),
                    "overlays": ch.get("overlays") or [],
                    "kbars": ch.get("kbars") or [],
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
        lines = [
            "---",
            f"id: {cid}",
            "type: chart",
            f"session_id: {sid}",
            f"symbol: {ch.get('symbol')}",
            f"symbol_name: {_yaml_esc(ch.get('symbol_name') or '')}",
            f"period: {ch.get('period')}",
            f"asset_type: {ch.get('asset_type') or ''}",
            "---",
            "",
            f"# 图表 {ch.get('symbol_name') or ch.get('symbol')} · {ch.get('period')}",
            "",
            f"- 可视范围：{json.dumps(ch.get('visible_range') or {}, ensure_ascii=False)}",
            f"- 画线数：{len(ch.get('overlays') or [])}",
            f"- 选K数：{len(ch.get('kbars') or [])}",
            "",
            f"## 附件",
            f"- `{ov_rel}`",
            "",
        ]
        _atomic_write(root / md_rel, "\n".join(lines))
        chart_links.append(f"- [[charts/{cid}|{ch.get('symbol')} {ch.get('period')}]]")

    # causes
    cause_lines_index: List[str] = []
    for ca in session.get("causes") or []:
        cid = ca["id"]
        md_rel = f"{base_rel}/causes/{cid}.md"
        att_rel = f"{base_rel}/attachments/{cid}.json"
        _atomic_write(root / att_rel, json.dumps(ca, ensure_ascii=False, indent=2))
        notes = ca.get("notes") or []
        if isinstance(notes, str):
            notes = [notes]
        body = [
            "---",
            f"id: {cid}",
            "type: cause",
            f"level: {ca.get('level')}",
            f"parent_id: {ca.get('parent_id') or ''}",
            f"state: {ca.get('state')}",
            f"session_id: {sid}",
            "---",
            "",
            f"# 因 {cid} · {ca.get('level')} · {ca.get('state')}",
            "",
            f"- parent: {ca.get('parent_id') or '（根/大因）'}",
            f"- 涉及图表: {', '.join(ca.get('chart_ids') or [])}",
            "",
            "## 备注",
            *([f"- {n}" for n in notes] if notes else ["- （无）"]),
            "",
            "## 上下文/几何",
            f"- 见 `{att_rel}`",
            "",
            "## Agent",
            "- 仅复述用户原文与结构，禁止自动判定因果真伪。",
            "",
        ]
        _atomic_write(root / md_rel, "\n".join(body))
        indent = "  " if ca.get("parent_id") else ""
        cause_lines_index.append(
            f"{indent}- [[causes/{cid}|{cid} {ca.get('level')} {ca.get('state')}]]"
        )

    # effects
    effect_index: List[str] = []
    for ef in session.get("effects") or []:
        eid = ef["id"]
        md_rel = f"{base_rel}/effects/{eid}.md"
        att_rel = f"{base_rel}/attachments/{eid}.json"
        _atomic_write(root / att_rel, json.dumps(ef, ensure_ascii=False, indent=2))
        notes = ef.get("notes") or []
        if isinstance(notes, str):
            notes = [notes]
        kbars = ef.get("kbars") or []
        body = [
            "---",
            f"id: {eid}",
            "type: effect",
            f"cause_id: {ef.get('cause_id')}",
            f"phase: {ef.get('phase')}",
            f"session_id: {sid}",
            "---",
            "",
            f"# 果 {eid} · phase={ef.get('phase')}",
            "",
            f"- 归因因: {ef.get('cause_id')}",
            "",
            "## 选中的 K",
        ]
        for kb in kbars:
            pe = kb.get("price_element") or ""
            body.append(
                f"- {kb.get('date') or kb.get('timestamp')} "
                f"{pe} @ {kb.get('price', '')} "
                f"({kb.get('symbol') or ''} {kb.get('period') or ''})"
            )
        if not kbars:
            body.append("- （无）")
        body += [
            "",
            "## 备注",
            *([f"- {n}" for n in notes] if notes else ["- （无）"]),
            "",
            f"## 附件",
            f"- `{att_rel}`",
            "",
        ]
        _atomic_write(root / md_rel, "\n".join(body))
        effect_index.append(
            f"- [[effects/{eid}|{eid} → {ef.get('cause_id')} · {ef.get('phase')}]]"
        )

    # causal edges for human read
    edges: List[str] = []
    causes_by_id = {c["id"]: c for c in (session.get("causes") or [])}
    for ef in session.get("effects") or []:
        ca = causes_by_id.get(ef.get("cause_id") or "")
        level = (ca or {}).get("level") or ""
        edges.append(
            f"- {ef.get('cause_id')} → {ef.get('id')} "
            f"({level}, phase={ef.get('phase')})"
        )
    for ca in session.get("causes") or []:
        if ca.get("parent_id"):
            edges.append(f"- {ca.get('parent_id')} ⊃ {ca.get('id')} (父子因)")

    session_md = [
        "---",
        f"id: {sid}",
        "type: session",
        f"status: {session.get('status')}",
        f"title: {_yaml_esc(session.get('title') or '')}",
        f"created_at: {_yaml_esc(session.get('created_at') or '')}",
        f"updated_at: {_yaml_esc(session.get('updated_at') or '')}",
        "---",
        "",
        f"# 会话 {session.get('title') or sid}",
        "",
        "## 图表",
        *(chart_links or ["- （无）"]),
        "",
        "## 因",
        *(cause_lines_index or ["- （无）"]),
        "",
        "## 果",
        *(effect_index or ["- （无）"]),
        "",
        "## 因果箭头",
        *(edges or ["- （无）"]),
        "",
        "## 机读",
        f"- snapshot: `{snap_rel}`",
        "",
        "## Agent 学习说明",
        "- 会话彼此独立；仅复述用户 notes / 选K / 几何 / 箭头声明。",
        "- 禁止自动判定因果真伪或共振。",
        "",
    ]
    md_rel = f"{base_rel}/session.md"
    _atomic_write(root / md_rel, "\n".join(session_md))

    return {
        "base_relpath": base_rel.replace("\\", "/"),
        "md_relpath": md_rel.replace("\\", "/"),
        "snapshot_relpath": snap_rel.replace("\\", "/"),
        "abs_md": str(root / md_rel),
        "obsidian_uri": build_obsidian_uri(md_rel.replace("\\", "/")),
    }
