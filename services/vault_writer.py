"""
vault_writer.py — 向 Obsidian vault 文件夹原子写入 md / 附件
不依赖 Obsidian 插件；库 = 目录。
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import quote

def vault_root() -> Path:
    # 每次从 config 读取，便于测试 monkeypatch 与运行时 env 覆盖
    from core.config import ANNOTATION_VAULT_PATH

    root = Path(ANNOTATION_VAULT_PATH)
    root.mkdir(parents=True, exist_ok=True)
    (root / "charts" / "cases").mkdir(parents=True, exist_ok=True)
    (root / "charts" / "relations").mkdir(parents=True, exist_ok=True)
    (root / "charts" / "attachments").mkdir(parents=True, exist_ok=True)
    (root / "charts" / "playbook").mkdir(parents=True, exist_ok=True)
    return root


def vault_writable() -> bool:
    try:
        root = vault_root()
        test = root / ".write_test"
        test.write_text("ok", encoding="utf-8")
        test.unlink(missing_ok=True)
        return True
    except Exception:
        return False


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


def _yaml_escape(s: str) -> str:
    if s is None:
        return '""'
    s = str(s)
    if any(c in s for c in (":", "#", "\n", '"', "'")):
        return json.dumps(s, ensure_ascii=False)
    return s


def build_obsidian_uri(rel_path: str) -> str:
    """rel_path: vault 内相对路径，正斜杠。"""
    from core.config import OBSIDIAN_VAULT_NAME

    vault = OBSIDIAN_VAULT_NAME or vault_root().name
    file_no_ext = rel_path.replace("\\", "/")
    if file_no_ext.endswith(".md"):
        file_no_ext = file_no_ext[:-3]
    return (
        f"obsidian://open?vault={quote(vault)}"
        f"&file={quote(file_no_ext)}"
    )


def write_case_files(case: Dict[str, Any]) -> Dict[str, str]:
    """写 case md + overlays json。返回 rel paths 与 uri。"""
    root = vault_root()
    cid = case["id"]
    symbol = case.get("symbol") or "unknown"
    safe_sym = "".join(c if c.isalnum() or c in "-_" else "_" for c in symbol)

    md_rel = f"charts/cases/{safe_sym}/{cid}.md"
    ov_rel = f"charts/attachments/{cid}.overlays.json"
    md_path = root / md_rel
    ov_path = root / ov_rel

    overlays = case.get("overlays") or []
    _atomic_write(
        ov_path,
        json.dumps(
            {
                "case_id": cid,
                "type": case.get("type"),
                "period": case.get("period"),
                "source_bar": case.get("source_bar"),
                "overlays": overlays,
            },
            ensure_ascii=False,
            indent=2,
        ),
    )

    fm = [
        "---",
        f"id: {cid}",
        f"type: {case.get('type') or 'chart-annotation'}",
        f"symbol: {case.get('symbol')}",
        f"symbol_name: {_yaml_escape(case.get('symbol_name') or '')}",
        f"asset_type: {case.get('asset_type') or ''}",
        f"period: {case.get('period') or ''}",
    ]
    if case.get("type") == "level_origin":
        sb = case.get("source_bar") or {}
        lv = case.get("level") or {}
        fm += [
            f"price_element: {sb.get('price_element') or case.get('price_element') or ''}",
            f"level_price: {lv.get('price', '')}",
            f"level_role: {lv.get('role') or ''}",
            f"source_date: {_yaml_escape(sb.get('date') or '')}",
        ]
    fm += [
        f"relation_ids: {json.dumps(case.get('relation_ids') or [], ensure_ascii=False)}",
        f"created_at: {_yaml_escape(case.get('created_at') or '')}",
        f"updated_at: {_yaml_escape(case.get('updated_at') or '')}",
        "---",
        "",
        f"# {case.get('symbol_name') or symbol} · {case.get('period')} · {case.get('type')}",
        "",
    ]
    if case.get("type") == "level_origin":
        sb = case.get("source_bar") or {}
        ohlc = sb.get("ohlc") or {}
        lv = case.get("level") or {}
        pe = sb.get("price_element") or case.get("price_element")
        fm += [
            "## 源 K",
            f"- 日期：{sb.get('date') or sb.get('timestamp')}",
            f"- 要素：{pe}",
            f"- OHLC：{json.dumps(ohlc, ensure_ascii=False)}",
            "",
            "## 水平位",
            f"- role：{lv.get('role')}",
            f"- price：{lv.get('price')}",
            "",
            "## 反应点",
        ]
        for rx in case.get("reactions") or []:
            fm.append(
                f"- {rx.get('date') or rx.get('timestamp')}: "
                f"{rx.get('kind')} @ {rx.get('price')} — {rx.get('note') or ''}"
            )
        if not case.get("reactions"):
            fm.append("- （待补）")
        fm.append("")
    # 通用画线：列出几何摘要，便于人读 + Agent 检索
    if case.get("type") == "chart-annotation" or (
        case.get("overlays") and case.get("type") != "level_origin"
    ):
        fm += ["## 画线几何", ""]
        for i, ov in enumerate(case.get("overlays") or [], 1):
            pts = ov.get("points") or []
            pts_s = ", ".join(
                f"{p.get('timestamp')}/{p.get('value')}" for p in pts[:4]
            )
            if len(pts) > 4:
                pts_s += f" …(+{len(pts)-4})"
            fm.append(
                f"- [{i}] `{ov.get('type') or ov.get('name')}` id={ov.get('id')} "
                f"points=[{pts_s}]"
            )
        if not case.get("overlays"):
            fm.append("- （无）")
        fm.append("")
    fm += [
        "## 备注",
        case.get("notes") or "",
        "",
        "## 附件",
        f"- overlays: `[[{ov_rel}]]`",
        "",
        "## Agent 学习说明",
        "- 本笔记为用户标注原文；Agent 仅可复述，不可自动判定对错或生成共振结论。",
        f"- distillable: {(case.get('agent') or {}).get('distillable', True)}",
        "",
    ]
    _atomic_write(md_path, "\n".join(fm))

    return {
        "md_relpath": md_rel.replace("\\", "/"),
        "overlays_relpath": ov_rel.replace("\\", "/"),
        "obsidian_uri": build_obsidian_uri(md_rel.replace("\\", "/")),
        "abs_md": str(md_path),
    }


def write_relation_files(rel: Dict[str, Any]) -> Dict[str, str]:
    root = vault_root()
    rid = rel["id"]
    created = (rel.get("created_at") or "")[:10].replace("-", "")
    yyyy = created[:4] if len(created) >= 4 else "0000"
    mm = created[4:6] if len(created) >= 6 else "00"
    if yyyy == "0000":
        from datetime import datetime

        now = datetime.now()
        yyyy, mm = f"{now.year:04d}", f"{now.month:02d}"

    md_rel = f"charts/relations/{yyyy}/{mm}/{rid}.md"
    md_path = root / md_rel
    note = rel.get("relation_note") or ""
    members = rel.get("members") or []
    lines = [
        "---",
        f"id: {rid}",
        "type: relation",
        f"relation_note: {_yaml_escape(note)}",
        f"user_tags: {json.dumps(rel.get('user_tags') or [], ensure_ascii=False)}",
        f"created_at: {_yaml_escape(rel.get('created_at') or '')}",
        "---",
        "",
        f"# 关联 {rid}",
        "",
        "## 经验结论（用户原文）",
        "",
        note or "（未填写）",
        "",
        "## 成员",
        "",
    ]
    for m in members:
        lines.append(
            f"- {m.get('symbol_name') or m.get('symbol')} "
            f"({m.get('asset_type')}/{m.get('period')}) "
            f"case=`{m.get('case_id') or ''}`"
        )
    lines += ["", "## 备注", rel.get("notes") or "", ""]
    _atomic_write(md_path, "\n".join(lines))
    return {
        "md_relpath": md_rel.replace("\\", "/"),
        "obsidian_uri": build_obsidian_uri(md_rel.replace("\\", "/")),
        "abs_md": str(md_path),
    }
