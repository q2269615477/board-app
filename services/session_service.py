# -*- coding: utf-8 -*-
"""
会话 / 图表 / 因果链业务逻辑。

领域模型（列式因果链）：
- 因果链(cause)：一列叙事单元；顶端是因、底端是果；中间可嵌套子链并穿插事件
- 点「因」/「果」只切换焦点与相位，绝不自动创建事件
- 事件仅显式 start_event 产生，依附于某条链（cause_id），写入该链的 children_order
- 果 phase：idle → collecting（第1次）→ closed（第2次）；闭父前子孙果须全闭
- 采集：有 active_event 则写事件（并累加到因/果汇总）；无事件则只写因/果汇总
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from data.session_repo import get_session_repo
from services import session_vault


class RevisionConflictError(Exception):
    """会话版本过期：save/commit 检测到 base_rev != 服务端 rev。

    - current_rev: 服务端当前 rev
    - current_session: 服务端当前整包会话（供前端做 diff / 强制覆盖）
    """

    def __init__(self, current_rev: int, current_session: Dict[str, Any]):
        self.current_rev = current_rev
        self.current_session = current_session
        super().__init__(
            f"会话版本过期：base_rev 不匹配，服务端当前 rev={current_rev}"
        )


class ReadOnlySessionError(Exception):
    """committed 会话不可修改：请先 clone 创建新会话。"""

    def __init__(self, session_id: str, status: str = "committed"):
        self.session_id = session_id
        self.status = status
        super().__init__(
            f"会话已定稿（status={status}），不可修改；请先克隆为新会话"
        )


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _nid(prefix: str) -> str:
    return f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"


def empty_session(title: str = None) -> Dict[str, Any]:
    sid = _nid("sess")
    return {
        "id": sid,
        "title": title or f"会话 {datetime.now().strftime('%m-%d %H:%M')}",
        "status": "drafting",
        "created_at": _now(),
        "updated_at": _now(),
        # 乐观锁版本号：每次写成功 +1；旧会话经 normalize_session 补 0
        "rev": 0,
        "charts": [],
        "current_chart_id": None,
        "causes": [],
        "effects": [],
        # 事件：依附因果链（cause_id = chain id），在链的 children_order 中穿插
        "events": [],
        # 根层因果链顺序
        "root_order": [],
        "ui": {
            "tool": "browse",  # browse | pick_k
            "side": "cause",  # cause | effect — 无事件时写因/果汇总
            "active_cause_id": None,  # 当前因果链
            "active_effect_id": None,
            "active_event_id": None,  # 显式选中的事件；null = 写因/果汇总
            "active_element_id": None,  # 事件内当前高亮元素
        },
        "vault": None,
    }


class SessionService:
    def __init__(self):
        self.repo = get_session_repo()

    # ---- list / get ----

    def list_sessions(self, limit: int = 50) -> List[Dict[str, Any]]:
        return self.repo.list_summaries(limit=limit)

    @staticmethod
    def _check_writable(session: Dict[str, Any]) -> None:
        """committed 会话不可修改：抛 ReadOnlySessionError。"""
        if (session.get("status") or "").lower() == "committed":
            raise ReadOnlySessionError(
                session_id=session.get("id") or "",
                status="committed",
            )

    def clone_session(self, session_id: str) -> Dict[str, Any]:
        """基于已有会话（含 committed）克隆为新 drafting 会话。

        - 复制 charts / causes / effects / events 结构（重置 id 字段）
        - 重置 ui 焦点、active_event_id
        - 标题加 "（副本）"
        - 状态强制 drafting；rev=0
        """
        import copy

        src = self.repo.get(session_id)
        if not src:
            raise KeyError(session_id)
        self.normalize_session(src)
        cloned = copy.deepcopy(src)
        # 重新生成 id
        new_id = _nid("sess")
        cloned["id"] = new_id
        cloned["title"] = (src.get("title") or "会话") + "（副本）"
        cloned["status"] = "drafting"
        cloned["rev"] = 0
        cloned["created_at"] = _now()
        cloned["updated_at"] = _now()
        cloned["vault"] = None
        # 重置 ui
        ui = cloned.setdefault("ui", {})
        for k in (
            "active_cause_id",
            "active_effect_id",
            "active_event_id",
            "active_element_id",
        ):
            ui[k] = None
        ui["tool"] = "browse"
        ui["side"] = "cause"
        # 重新生成所有内部 id（cause / effect / event / element / chart），
        # 否则与原会话混淆
        id_map: Dict[str, str] = {}

        def _map(old_id: str) -> str:
            if old_id in id_map:
                return id_map[old_id]
            new = _nid(old_id.split("_")[0] if "_" in old_id else "x")
            id_map[old_id] = new
            return new

        # charts
        new_charts = []
        for ch in cloned.get("charts") or []:
            new_ch = dict(ch)
            new_ch["id"] = _nid("chart")
            new_charts.append(new_ch)
        cloned["charts"] = new_charts
        # causes
        cause_map: Dict[str, str] = {}
        new_causes = []
        for c in cloned.get("causes") or []:
            new_c = dict(c)
            new_c["id"] = _nid("cause")
            cause_map[c.get("id")] = new_c["id"]
            # parent_id 重映射
            if new_c.get("parent_id"):
                new_c["parent_id"] = cause_map.get(new_c["parent_id"], new_c["parent_id"])
            # 重置 state 为 open（克隆出来要可继续编辑）
            new_c["state"] = "open"
            new_causes.append(new_c)
        cloned["causes"] = new_causes
        # effects
        new_effects = []
        for e in cloned.get("effects") or []:
            new_e = dict(e)
            new_e["id"] = _nid("effect")
            if new_e.get("cause_id"):
                new_e["cause_id"] = cause_map.get(new_e["cause_id"], new_e["cause_id"])
            # 果重置为 idle
            new_e["phase"] = "idle"
            new_effects.append(new_e)
        cloned["effects"] = new_effects
        # events
        new_events = []
        for ev in cloned.get("events") or []:
            new_ev = dict(ev)
            new_ev["id"] = _nid("evt")
            if new_ev.get("cause_id"):
                new_ev["cause_id"] = cause_map.get(
                    new_ev["cause_id"], new_ev["cause_id"]
                )
            # chart_id 重映射
            if new_ev.get("chart_id") and cloned.get("charts"):
                # 简单用第一个 chart id（用户在 clone 后应重新切换/重放）
                new_ev["chart_id"] = cloned["charts"][0]["id"]
            # elements 重置 id（保留数据）
            new_els = []
            for el in new_ev.get("elements") or []:
                new_el = dict(el)
                new_el["id"] = _nid("el")
                new_els.append(new_el)
            new_ev["elements"] = new_els
            new_events.append(new_ev)
        cloned["events"] = new_events
        # root_order / children_order 重映射
        def _remap_order(order):
            out = []
            for x in order or []:
                if not isinstance(x, dict):
                    continue
                t = x.get("type")
                old_id = x.get("id")
                if t == "chain" and old_id in cause_map:
                    out.append({"type": "chain", "id": cause_map[old_id]})
                elif t == "event":
                    new_eid = next(
                        (ne["id"] for ne in new_events if ne.get("id") != old_id and False),
                        None,
                    )
                    # 简化：events 的 id 全部新生成，但 children_order 用的是新 id；
                    # 这里用 cause_map 的反查不够，改为先收集 ev 映射
                    pass
            return out

        # 单独处理 children_order（因为 events 也需要映射）
        ev_map: Dict[str, str] = {}
        for src_ev, new_ev in zip(
            src.get("events") or [], cloned.get("events") or []
        ):
            ev_map[src_ev.get("id")] = new_ev.get("id")

        for c in cloned.get("causes") or []:
            src_c = next(
                (
                    sc
                    for sc in src.get("causes") or []
                    if sc.get("id") in cause_map
                    and cause_map[sc["id"]] == c["id"]
                ),
                None,
            )
            new_order = []
            for x in (src_c.get("children_order") if src_c else []) or []:
                if not isinstance(x, dict):
                    continue
                t = x.get("type")
                old = x.get("id")
                if t == "chain" and old in cause_map:
                    new_order.append({"type": "chain", "id": cause_map[old]})
                elif t == "event" and old in ev_map:
                    new_order.append({"type": "event", "id": ev_map[old]})
            c["children_order"] = new_order

        # root_order
        new_root = []
        for x in cloned.get("root_order") or []:
            if not isinstance(x, dict):
                continue
            t = x.get("type")
            old = x.get("id")
            if t == "chain" and old in cause_map:
                new_root.append({"type": "chain", "id": cause_map[old]})
        cloned["root_order"] = new_root

        self.normalize_session(cloned)
        self.repo.upsert(cloned)
        return cloned

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        sess = self.repo.get(session_id)
        if sess:
            self.normalize_session(sess)
        return sess

    def get_active(self) -> Optional[Dict[str, Any]]:
        aid = self.repo.get_active_id()
        if not aid:
            return None
        return self.get_session(aid)

    # ---- create / switch / save ----

    def create_session(self, title: str = None) -> Dict[str, Any]:
        """新建会话：先保存并 pause 当前活跃会话。"""
        cur_id = self.repo.get_active_id()
        if cur_id:
            cur = self.repo.get(cur_id)
            if cur and cur.get("status") == "drafting":
                cur["status"] = "paused"
                cur["updated_at"] = _now()
                self.repo.upsert(cur)
                try:
                    paths = session_vault.write_session_to_vault(cur)
                    cur["vault"] = paths
                    self.repo.upsert(cur)
                except Exception:
                    pass

        sess = empty_session(title=title)
        self.repo.upsert(sess)
        self.repo.set_active_id(sess["id"])
        return sess

    def activate_session(self, session_id: str) -> Dict[str, Any]:
        """切换到已有会话：pause 当前并保存进度。"""
        target = self.repo.get(session_id)
        if not target:
            raise KeyError(session_id)

        cur_id = self.repo.get_active_id()
        if cur_id and cur_id != session_id:
            cur = self.repo.get(cur_id)
            if cur and cur.get("status") == "drafting":
                cur["status"] = "paused"
                cur["updated_at"] = _now()
                self.repo.upsert(cur)
                try:
                    paths = session_vault.write_session_to_vault(cur)
                    cur["vault"] = paths
                    self.repo.upsert(cur)
                except Exception:
                    pass

        if target.get("status") == "paused":
            target["status"] = "drafting"
        target["updated_at"] = _now()
        self.normalize_session(target)
        self.repo.upsert(target)
        self.repo.set_active_id(session_id)
        return target

    def save_progress(
        self,
        session: Dict[str, Any],
        write_vault: bool = True,
        expected_rev: Optional[int] = None,
    ) -> Dict[str, Any]:
        """保存进度（完整 payload 由前端合并后传入，或服务端已有）。

        乐观锁：
        - expected_rev 不为 None 时，比对服务端 current.rev；不等 → RevisionConflictError
        - 写成功后将 session.rev = current_rev + 1

        只读：committed 状态的会话不可保存（payload 整体回写会污染已定稿内容）。
        检查的是持久化状态（existing.rev/existing.status），不是 in-flight payload。
        """
        if not session.get("id"):
            raise ValueError("缺少 session.id")
        existing = self.repo.get(session["id"])
        if existing:
            # 只读守护：基于持久化状态判断
            self._check_writable(existing)
            existing_rev = int(existing.get("rev") or 0)
            # 乐观锁校验
            if expected_rev is not None and existing_rev != int(expected_rev):
                raise RevisionConflictError(
                    current_rev=existing_rev,
                    current_session=existing,
                )
            merged = dict(existing)
            merged.update(session)
            session = merged
        if session.get("status") not in ("committed",):
            if session.get("status") != "paused":
                session["status"] = session.get("status") or "drafting"
        session["updated_at"] = _now()
        self.normalize_session(session)
        if write_vault:
            try:
                paths = session_vault.write_session_to_vault(session)
                session["vault"] = paths
            except Exception as e:
                session.setdefault("vault_error", str(e)[:200])
        # rev 自增
        session["rev"] = existing_rev + 1
        self.repo.upsert(session)
        if self.repo.get_active_id() is None:
            self.repo.set_active_id(session["id"])
        return session

    def commit_session(
        self,
        session_id: str,
        payload: Dict[str, Any] = None,
        expected_rev: Optional[int] = None,
    ) -> Dict[str, Any]:
        """定稿会话：status=committed + 写 vault + rev++。

        幂等：已 committed 的会话再次 commit 不会报错，仅重新写 vault + bump rev。
        payload 传入时若已 committed → 忽略 payload 变更（保持只读）。
        """
        existing = self.repo.get(session_id)
        if not existing:
            raise KeyError(session_id)
        existing_rev = int(existing.get("rev") or 0)
        if expected_rev is not None and existing_rev != int(expected_rev):
            raise RevisionConflictError(
                current_rev=existing_rev,
                current_session=existing,
            )
        # 幂等：已 committed 时忽略 payload 变更
        if (existing.get("status") or "").lower() == "committed":
            sess = existing
        else:
            sess = existing
            if payload:
                sess = dict(existing)
                sess.update(payload)
        sess["status"] = "committed"
        sess["updated_at"] = _now()
        self.normalize_session(sess)
        paths = session_vault.write_session_to_vault(sess)
        sess["vault"] = paths
        # commit 也 bump rev
        sess["rev"] = existing_rev + 1
        self.repo.upsert(sess)
        return sess

    # ---- chart ----

    def ensure_chart(
        self,
        session: Dict[str, Any],
        symbol: str,
        period: str,
        symbol_name: str = "",
        asset_type: str = "",
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        self._check_writable(session)
        charts = session.setdefault("charts", [])
        for ch in charts:
            if ch.get("symbol") == symbol and ch.get("period") == period:
                session["current_chart_id"] = ch["id"]
                return session, ch
        ch = {
            "id": _nid("chart"),
            "symbol": symbol,
            "symbol_name": symbol_name or symbol,
            "period": period,
            "asset_type": asset_type or "",
            "visible_range": None,
            "overlays": [],
            "kbars": [],
        }
        charts.append(ch)
        session["current_chart_id"] = ch["id"]
        return session, ch

    def update_chart(
        self, session: Dict[str, Any], chart_id: str, patch: Dict[str, Any]
    ) -> Dict[str, Any]:
        self._check_writable(session)
        for ch in session.get("charts") or []:
            if ch.get("id") == chart_id:
                for k, v in patch.items():
                    if k == "id":
                        continue
                    ch[k] = v
                return session
        raise KeyError(chart_id)

    # ---- normalize / order ----

    def normalize_session(self, session: Dict[str, Any]) -> Dict[str, Any]:
        """补齐 root_order / children_order；兼容旧会话。"""
        # 旧会话回填：rev 默认 0
        session.setdefault("rev", 0)
        session.setdefault("events", [])
        session.setdefault("causes", [])
        session.setdefault("effects", [])
        session.setdefault("root_order", [])
        session.setdefault("ui", {})
        ui = session["ui"]
        for k, default in (
            ("tool", "browse"),
            ("side", "cause"),
            ("active_cause_id", None),
            ("active_effect_id", None),
            ("active_event_id", None),
        ):
            ui.setdefault(k, default)

        causes = session["causes"]
        cause_ids = {c["id"] for c in causes if c.get("id")}
        for c in causes:
            c.setdefault("children_order", [])
            c.setdefault("kbars", [])
            c.setdefault("overlays", [])
            c.setdefault("notes", [])
            c.setdefault("contexts", [])
            c.setdefault("chart_ids", [])

        # 迁移：若 root_order 空但有根因，按 causes 出现顺序建
        roots = [c for c in causes if not c.get("parent_id")]
        known_root = {
            x.get("id")
            for x in session["root_order"]
            if isinstance(x, dict) and x.get("type") == "chain"
        }
        for c in roots:
            if c["id"] not in known_root:
                session["root_order"].append({"type": "chain", "id": c["id"]})
                known_root.add(c["id"])
        # 清理失效
        session["root_order"] = [
            x
            for x in session["root_order"]
            if isinstance(x, dict)
            and x.get("type") == "chain"
            and x.get("id") in cause_ids
        ]

        # 子链：写入父 children_order（若缺失）
        for c in causes:
            pid = c.get("parent_id")
            if not pid or pid not in cause_ids:
                continue
            parent = next(x for x in causes if x["id"] == pid)
            order = parent.setdefault("children_order", [])
            if not any(
                isinstance(x, dict) and x.get("type") == "chain" and x.get("id") == c["id"]
                for x in order
            ):
                order.append({"type": "chain", "id": c["id"]})

        # 事件：写入所属链 children_order（若缺失）
        events = session["events"]
        ev_ids = {e["id"] for e in events if e.get("id")}
        for ev in events:
            cid = ev.get("cause_id")
            if not cid or cid not in cause_ids:
                continue
            ca = next(x for x in causes if x["id"] == cid)
            order = ca.setdefault("children_order", [])
            if not any(
                isinstance(x, dict) and x.get("type") == "event" and x.get("id") == ev["id"]
                for x in order
            ):
                order.append({"type": "event", "id": ev["id"]})

        # 清理每条链 order 中失效项
        for c in causes:
            order = c.get("children_order") or []
            cleaned = []
            for x in order:
                if not isinstance(x, dict):
                    continue
                t, i = x.get("type"), x.get("id")
                if t == "chain" and i in cause_ids:
                    cleaned.append(x)
                elif t == "event" and i in ev_ids:
                    cleaned.append(x)
            c["children_order"] = cleaned

        # 事件 elements 迁移
        for ev in events:
            self._migrate_event_elements(ev)

        # 失效焦点清理
        if ui.get("active_cause_id") and ui["active_cause_id"] not in cause_ids:
            ui["active_cause_id"] = None
            ui["active_effect_id"] = None
        if ui.get("active_event_id") and ui["active_event_id"] not in ev_ids:
            ui["active_event_id"] = None
            ui["active_element_id"] = None
        ui.setdefault("active_element_id", None)
        return session

    def _append_order(
        self,
        session: Dict[str, Any],
        parent_id: Optional[str],
        item_type: str,
        item_id: str,
    ) -> None:
        entry = {"type": item_type, "id": item_id}
        if parent_id:
            ca = next(
                (c for c in session.get("causes") or [] if c["id"] == parent_id), None
            )
            if not ca:
                raise ValueError("父链不存在")
            ca.setdefault("children_order", []).append(entry)
        else:
            session.setdefault("root_order", []).append(entry)

    def _cause_depth(self, session: Dict[str, Any], cause_id: str) -> int:
        causes = {c["id"]: c for c in (session.get("causes") or [])}
        d = 0
        cur = causes.get(cause_id)
        seen = set()
        while cur and cur.get("parent_id"):
            if cur["id"] in seen:
                break
            seen.add(cur["id"])
            cur = causes.get(cur["parent_id"])
            d += 1
            if d > 64:
                break
        return d

    def _child_causes(self, session: Dict[str, Any], parent_id: str) -> List[Dict[str, Any]]:
        return [
            c for c in (session.get("causes") or []) if c.get("parent_id") == parent_id
        ]

    def _open_descendant_effects(
        self, session: Dict[str, Any], cause_id: str
    ) -> List[str]:
        open_ids: List[str] = []
        stack = [cause_id]
        while stack:
            pid = stack.pop()
            for ch in self._child_causes(session, pid):
                stack.append(ch["id"])
                ef = next(
                    (
                        e
                        for e in (session.get("effects") or [])
                        if e.get("cause_id") == ch["id"]
                    ),
                    None,
                )
                if not ef or ef.get("phase") != "closed":
                    open_ids.append(ch["id"])
        return open_ids

    # ---- cause / effect / event ----

    def create_cause(
        self,
        session: Dict[str, Any],
        parent_id: str = None,
        title: str = "",
        as_root: bool = False,
    ) -> Dict[str, Any]:
        """新建因果链（因+果配对）。不创建事件。

        - as_root=True → 根层链
        - parent_id 指定 → 挂到该链下（子链），写入 parent.children_order
        """
        self._check_writable(session)
        self.normalize_session(session)
        ui = session.setdefault("ui", {})
        if as_root:
            parent_id = None
        elif parent_id is None and not as_root:
            parent_id = ui.get("active_cause_id")

        if parent_id:
            parent = next(
                (c for c in session.get("causes") or [] if c["id"] == parent_id), None
            )
            if not parent:
                raise ValueError("父因果链不存在")
            if parent.get("state") == "closed":
                raise ValueError("父链已闭合，不能再嵌套子链")

        depth = 0
        if parent_id:
            depth = self._cause_depth(session, parent_id) + 1

        cause = {
            "id": _nid("cause"),
            "depth": depth,
            "level": depth,
            "parent_id": parent_id,
            "state": "open",
            "title": title or (f"因-L{depth}" if parent_id else "因"),
            "notes": [],
            "chart_ids": [],
            "contexts": [],
            "kbars": [],
            "overlays": [],
            "children_order": [],
        }
        effect = {
            "id": _nid("effect"),
            "cause_id": cause["id"],
            "depth": depth,
            "level": depth,
            "phase": "idle",
            "notes": [],
            "kbars": [],
            "overlays": [],
            "chart_ids": [],
        }
        session.setdefault("causes", []).append(cause)
        session.setdefault("effects", []).append(effect)
        self._append_order(session, parent_id, "chain", cause["id"])

        ui["active_cause_id"] = cause["id"]
        ui["active_effect_id"] = effect["id"]
        ui["side"] = "cause"
        ui["active_event_id"] = None  # 点因建链 ≠ 事件
        return session

    def create_major_cause(self, session: Dict[str, Any], title: str = "") -> Dict[str, Any]:
        return self.create_cause(session, parent_id=None, title=title or "因", as_root=True)

    def create_minor_cause(
        self, session: Dict[str, Any], parent_id: str = None, title: str = ""
    ) -> Dict[str, Any]:
        return self.create_cause(
            session, parent_id=parent_id, title=title or "因", as_root=False
        )

    def focus_cause(self, session: Dict[str, Any], cause_id: str) -> Dict[str, Any]:
        """点击因：聚焦该因果链的因侧。不创建事件。"""
        self.normalize_session(session)
        ca = next((c for c in session.get("causes") or [] if c["id"] == cause_id), None)
        if not ca:
            raise ValueError("因果链不存在")
        ef = next(
            (e for e in session.get("effects") or [] if e.get("cause_id") == cause_id),
            None,
        )
        ui = session.setdefault("ui", {})
        ui["active_cause_id"] = cause_id
        ui["active_effect_id"] = ef["id"] if ef else None
        ui["side"] = "cause"
        ui["active_event_id"] = None
        return session

    def focus_event(self, session: Dict[str, Any], event_id: str) -> Dict[str, Any]:
        """点击事件行：聚焦该事件所在链，并设为 active_event。"""
        self.normalize_session(session)
        ev = next((e for e in session.get("events") or [] if e["id"] == event_id), None)
        if not ev:
            raise ValueError("事件不存在")
        cause_id = ev.get("cause_id")
        ca = next((c for c in session.get("causes") or [] if c["id"] == cause_id), None)
        if not ca:
            raise ValueError("事件所属因果链不存在")
        ef = next(
            (e for e in session.get("effects") or [] if e.get("cause_id") == cause_id),
            None,
        )
        ui = session.setdefault("ui", {})
        ui["active_cause_id"] = cause_id
        ui["active_effect_id"] = ef["id"] if ef else None
        ui["active_event_id"] = event_id
        # 保持 side；若事件带 capture_side 可参考
        if ev.get("capture_side") in ("cause", "effect"):
            ui["side"] = ev["capture_side"]
        return session

    def start_event(
        self,
        session: Dict[str, Any],
        cause_id: str = None,
        side: str = None,
        title: str = "",
    ) -> Dict[str, Any]:
        """显式在指定因果链下新增事件（写入 children_order）。"""
        self._check_writable(session)
        self.normalize_session(session)
        ui = session.setdefault("ui", {})
        cause_id = cause_id or ui.get("active_cause_id")
        if not cause_id:
            raise ValueError("无活跃因果链，无法创建事件（请先点「因」选中链）")
        ca = next((c for c in session.get("causes") or [] if c["id"] == cause_id), None)
        if not ca:
            raise ValueError("因果链不存在")
        if ca.get("state") == "closed":
            raise ValueError("链已闭合，不能再添加事件")

        capture_side = side or ui.get("side") or "cause"
        ev = {
            "id": _nid("evt"),
            "cause_id": cause_id,
            "capture_side": capture_side,
            "side": capture_side,  # 兼容旧字段
            "title": title or "",
            "created_at": _now(),
            "chart_id": session.get("current_chart_id"),
            "symbol": "",
            "period": "",
            # 元素：并列可重复（多次选K / 多次画线 / 多条备注各自独立）
            "elements": [],
            # 兼容旧字段（由 elements 同步派生）
            "kbars": [],
            "overlays": [],
            "notes": [],
        }
        ch = next(
            (
                c
                for c in session.get("charts") or []
                if c.get("id") == session.get("current_chart_id")
            ),
            None,
        )
        if ch:
            ev["symbol"] = ch.get("symbol") or ""
            ev["period"] = ch.get("period") or ""
            ev["chart_id"] = ch.get("id")

        session.setdefault("events", []).append(ev)
        # 事件插在该链 children_order 末尾（果始终在列底由 UI 渲染，不进 order）
        ca.setdefault("children_order", []).append({"type": "event", "id": ev["id"]})
        ui["active_cause_id"] = cause_id
        ui["active_event_id"] = ev["id"]
        return session

    def _active_event(self, session: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """当前可写事件：以 active_event_id 为准；自动对齐 active_cause_id。"""
        ui = session.setdefault("ui", {})
        eid = ui.get("active_event_id")
        if not eid:
            return None
        ev = next((e for e in session.get("events") or [] if e["id"] == eid), None)
        if not ev:
            ui["active_event_id"] = None
            return None
        # 对齐链焦点到事件所属链（避免误判导致采集丢事件）
        if ev.get("cause_id") and ui.get("active_cause_id") != ev.get("cause_id"):
            ui["active_cause_id"] = ev["cause_id"]
            ef = next(
                (
                    e
                    for e in (session.get("effects") or [])
                    if e.get("cause_id") == ev["cause_id"]
                ),
                None,
            )
            ui["active_effect_id"] = ef["id"] if ef else ui.get("active_effect_id")
        return ev

    @staticmethod
    def _normalize_kbar(kb: Dict[str, Any], chart_id: str = None) -> Dict[str, Any]:
        """规范 K 线字段（含成交量 volume / amount）。"""
        out = dict(kb or {})
        if chart_id and not out.get("chart_id"):
            out["chart_id"] = chart_id
        # 成交量兼容 volume / vol / 成交量
        vol = out.get("volume")
        if vol is None:
            vol = out.get("vol")
        if vol is None:
            vol = out.get("成交量")
        try:
            out["volume"] = float(vol) if vol is not None and vol != "" else None
        except (TypeError, ValueError):
            out["volume"] = vol
        amt = out.get("amount")
        if amt is None:
            amt = out.get("turnover")
        if amt is None:
            amt = out.get("成交额")
        try:
            out["amount"] = float(amt) if amt is not None and amt != "" else out.get("amount")
        except (TypeError, ValueError):
            pass
        for k in ("open", "high", "low", "close", "price"):
            if out.get(k) is not None:
                try:
                    out[k] = float(out[k])
                except (TypeError, ValueError):
                    pass
        return out

    def _new_element(self, kind: str, data: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": _nid("el"),
            "kind": kind,  # kbar | overlay | note
            "created_at": _now(),
            "data": data or {},
        }

    def _sync_event_legacy_fields(self, ev: Dict[str, Any]) -> None:
        """由 elements 派生 kbars/overlays/notes，兼容旧 UI/vault。"""
        els = ev.get("elements") or []
        ev["kbars"] = [
            dict(e.get("data") or {}) for e in els if e.get("kind") == "kbar"
        ]
        ev["overlays"] = [
            dict(e.get("data") or {}) for e in els if e.get("kind") == "overlay"
        ]
        ev["notes"] = [
            {
                "at": e.get("created_at") or (e.get("data") or {}).get("at"),
                "text": (e.get("data") or {}).get("text") or "",
            }
            for e in els
            if e.get("kind") == "note"
        ]

    def _migrate_event_elements(self, ev: Dict[str, Any]) -> None:
        """旧事件：kbars/overlays/notes → 并列 elements（仅当 elements 为空）。"""
        if ev.get("elements"):
            self._sync_event_legacy_fields(ev)
            return
        els: List[Dict[str, Any]] = []
        for kb in ev.get("kbars") or []:
            els.append(self._new_element("kbar", dict(kb)))
        for ov in ev.get("overlays") or []:
            els.append(self._new_element("overlay", dict(ov)))
        for n in ev.get("notes") or []:
            if isinstance(n, str):
                els.append(self._new_element("note", {"text": n}))
            else:
                els.append(
                    self._new_element(
                        "note",
                        {"text": (n or {}).get("text") or "", "at": (n or {}).get("at")},
                    )
                )
        ev["elements"] = els
        self._sync_event_legacy_fields(ev)

    def delete_element(
        self, session: Dict[str, Any], event_id: str = None, element_id: str = None
    ) -> Dict[str, Any]:
        """删除事件内某个元素。"""
        self._check_writable(session)
        self.normalize_session(session)
        ui = session.setdefault("ui", {})
        event_id = event_id or ui.get("active_event_id")
        if not event_id or not element_id:
            raise ValueError("缺少 event_id 或 element_id")
        ev = next((e for e in session.get("events") or [] if e["id"] == event_id), None)
        if not ev:
            raise ValueError("事件不存在")
        before = len(ev.get("elements") or [])
        ev["elements"] = [
            e for e in (ev.get("elements") or []) if e.get("id") != element_id
        ]
        if len(ev["elements"]) == before:
            raise ValueError("元素不存在")
        self._sync_event_legacy_fields(ev)
        if ui.get("active_element_id") == element_id:
            ui["active_element_id"] = None
        return session

    def delete_event(self, session: Dict[str, Any], event_id: str) -> Dict[str, Any]:
        """删除事件：从 events 与所属链 children_order 移除。"""
        self._check_writable(session)
        self.normalize_session(session)
        if not event_id:
            raise ValueError("缺少 event_id")
        events = session.setdefault("events", [])
        ev = next((e for e in events if e.get("id") == event_id), None)
        if not ev:
            raise ValueError("事件不存在")
        cause_id = ev.get("cause_id")
        session["events"] = [e for e in events if e.get("id") != event_id]
        if cause_id:
            ca = next(
                (c for c in session.get("causes") or [] if c["id"] == cause_id), None
            )
            if ca:
                ca["children_order"] = [
                    x
                    for x in (ca.get("children_order") or [])
                    if not (
                        isinstance(x, dict)
                        and x.get("type") == "event"
                        and x.get("id") == event_id
                    )
                ]
        ui = session.setdefault("ui", {})
        if ui.get("active_event_id") == event_id:
            ui["active_event_id"] = None
        return session

    def delete_cause(
        self, session: Dict[str, Any], cause_id: str, recursive: bool = True
    ) -> Dict[str, Any]:
        """删除因果链：默认递归删除子链；并删除挂在这些链上的事件与配对果。"""
        self._check_writable(session)
        self.normalize_session(session)
        if not cause_id:
            raise ValueError("缺少 cause_id")
        causes = session.get("causes") or []
        target = next((c for c in causes if c.get("id") == cause_id), None)
        if not target:
            raise ValueError("因果链不存在")

        # 收集待删链 id
        to_del = set()
        stack = [cause_id]
        while stack:
            cid = stack.pop()
            if cid in to_del:
                continue
            to_del.add(cid)
            if recursive:
                for ch in self._child_causes(session, cid):
                    stack.append(ch["id"])

        # 若非递归且仍有子链 → 禁止
        if not recursive:
            kids = self._child_causes(session, cause_id)
            if kids:
                raise ValueError("该链下仍有子链，请先删除子链或使用递归删除")

        parent_id = target.get("parent_id")

        # 删事件
        session["events"] = [
            e
            for e in (session.get("events") or [])
            if e.get("cause_id") not in to_del
        ]
        # 删果
        session["effects"] = [
            e
            for e in (session.get("effects") or [])
            if e.get("cause_id") not in to_del
        ]
        # 删链
        session["causes"] = [c for c in causes if c.get("id") not in to_del]

        # 从父 order / root_order 移除
        if parent_id:
            parent = next(
                (c for c in session["causes"] if c["id"] == parent_id), None
            )
            if parent:
                parent["children_order"] = [
                    x
                    for x in (parent.get("children_order") or [])
                    if not (
                        isinstance(x, dict)
                        and x.get("type") == "chain"
                        and x.get("id") in to_del
                    )
                ]
        session["root_order"] = [
            x
            for x in (session.get("root_order") or [])
            if not (
                isinstance(x, dict)
                and x.get("type") == "chain"
                and x.get("id") in to_del
            )
        ]
        # 清理各剩余链 order 中的失效引用
        for c in session["causes"]:
            c["children_order"] = [
                x
                for x in (c.get("children_order") or [])
                if isinstance(x, dict)
                and (
                    (x.get("type") == "chain" and x.get("id") not in to_del)
                    or (
                        x.get("type") == "event"
                        and any(
                            e.get("id") == x.get("id")
                            for e in (session.get("events") or [])
                        )
                    )
                )
            ]

        ui = session.setdefault("ui", {})
        if ui.get("active_cause_id") in to_del:
            ui["active_cause_id"] = parent_id
            if parent_id:
                ef = next(
                    (
                        e
                        for e in (session.get("effects") or [])
                        if e.get("cause_id") == parent_id
                    ),
                    None,
                )
                ui["active_effect_id"] = ef["id"] if ef else None
                ui["side"] = "cause"
            else:
                ui["active_effect_id"] = None
                ui["side"] = "cause"
            ui["active_event_id"] = None
        if ui.get("active_event_id"):
            still = any(
                e.get("id") == ui["active_event_id"]
                for e in (session.get("events") or [])
            )
            if not still:
                ui["active_event_id"] = None
        return session

    def click_effect(self, session: Dict[str, Any], effect_id: str = None) -> Dict[str, Any]:
        """策略乙：第1次 → collecting；第2次 → closed。点果 ≠ 事件。"""
        self._check_writable(session)
        self.normalize_session(session)
        ui = session.setdefault("ui", {})
        effect_id = effect_id or ui.get("active_effect_id")
        if not effect_id:
            raise ValueError("无活跃果")
        ef = next((e for e in session.get("effects") or [] if e["id"] == effect_id), None)
        if not ef:
            raise ValueError("果不存在")
        phase = ef.get("phase") or "idle"

        if phase == "closed":
            raise ValueError("该果已闭合，不可再操作")

        if phase == "idle":
            ef["phase"] = "collecting"
            ui["side"] = "effect"
            ui["active_effect_id"] = ef["id"]
            ui["active_cause_id"] = ef.get("cause_id")
            ui["active_event_id"] = None  # 点果不是事件
            return session

        open_desc = self._open_descendant_effects(session, ef.get("cause_id"))
        if open_desc:
            raise ValueError(
                "存在未闭合的子级果，请先闭合所有子因果链，再闭合当前果。"
                f" 未闭合: {', '.join(open_desc)}"
            )

        ef["phase"] = "closed"
        ca = next(
            (c for c in session.get("causes") or [] if c["id"] == ef.get("cause_id")),
            None,
        )
        if ca:
            ca["state"] = "closed"
        ui["side"] = "cause"
        ui["active_event_id"] = None
        return session

    def append_note(
        self, session: Dict[str, Any], text: str, target: str = None
    ) -> Dict[str, Any]:
        """有 active 事件 → 追加 note 元素（可重复）；否则写因/果汇总。"""
        self._check_writable(session)
        self.normalize_session(session)
        ui = session.setdefault("ui", {})
        side = target or ui.get("side") or "cause"
        text = (text or "").strip()
        if not text:
            raise ValueError("备注为空")
        note = {"at": _now(), "text": text}

        ev = self._active_event(session)
        if ev:
            el = self._new_element("note", note)
            ev.setdefault("elements", []).append(el)
            self._sync_event_legacy_fields(ev)
            ui["active_element_id"] = el["id"]
            return session

        if side == "effect":
            eid = ui.get("active_effect_id")
            ef = next((e for e in session.get("effects") or [] if e["id"] == eid), None)
            if not ef:
                raise ValueError("无当前果")
            if ef.get("phase") == "closed":
                raise ValueError("果已闭合，不可再写备注")
            ef.setdefault("notes", []).append(note)
        else:
            cid = ui.get("active_cause_id")
            ca = next((c for c in session.get("causes") or [] if c["id"] == cid), None)
            if not ca:
                raise ValueError("无当前因果链")
            if ca.get("state") == "closed":
                raise ValueError("链已闭合，不可再写备注")
            ca.setdefault("notes", []).append(note)
        return session

    def append_kbars(
        self,
        session: Dict[str, Any],
        kbars: List[Dict[str, Any]],
        side: str = None,
    ) -> Dict[str, Any]:
        """有 active 事件 → 每次选K追加独立 kbar 元素（可重复并列）；否则写因/果汇总。"""
        self._check_writable(session)
        self.normalize_session(session)
        ui = session.setdefault("ui", {})
        side = side or ui.get("side") or "cause"
        if not kbars:
            return session
        chart_id = session.get("current_chart_id")
        normed = [self._normalize_kbar(kb, chart_id) for kb in kbars]

        ev = self._active_event(session)
        if ev:
            last_el_id = None
            for kb in normed:
                # 每次选K都是独立元素，即使同一根K也可重复
                el = self._new_element("kbar", kb)
                ev.setdefault("elements", []).append(el)
                last_el_id = el["id"]
            ch = next(
                (c for c in session.get("charts") or [] if c.get("id") == chart_id),
                None,
            )
            if ch:
                ev["symbol"] = ch.get("symbol") or ev.get("symbol")
                ev["period"] = ch.get("period") or ev.get("period")
                ev["chart_id"] = chart_id
            self._sync_event_legacy_fields(ev)
            if last_el_id:
                ui["active_element_id"] = last_el_id
            return session

        if side == "effect":
            eid = ui.get("active_effect_id")
            ef = next((e for e in session.get("effects") or [] if e["id"] == eid), None)
            if not ef:
                raise ValueError("无当前果，请先点「果」进入果侧")
            if ef.get("phase") == "closed":
                raise ValueError("果已闭合")
            if ef.get("phase") == "idle":
                ef["phase"] = "collecting"
            ef.setdefault("kbars", []).extend(normed)
            if chart_id and chart_id not in (ef.get("chart_ids") or []):
                ef.setdefault("chart_ids", []).append(chart_id)
        else:
            cid = ui.get("active_cause_id")
            ca = next((c for c in session.get("causes") or [] if c["id"] == cid), None)
            if not ca:
                raise ValueError("无当前因果链，请先点「因」")
            if ca.get("state") == "closed":
                raise ValueError("链已闭合")
            ca.setdefault("kbars", []).extend(normed)
            if chart_id and chart_id not in (ca.get("chart_ids") or []):
                ca.setdefault("chart_ids", []).append(chart_id)
        return session

    def set_overlays_on_active(
        self,
        session: Dict[str, Any],
        overlays: List[Dict[str, Any]],
        chart_id: str = None,
        side: str = None,
    ) -> Dict[str, Any]:
        """画线：更新图表；有 active 事件时，每条新画线 = 独立 overlay 元素（可重复）。"""
        self._check_writable(session)
        self.normalize_session(session)
        ui = session.setdefault("ui", {})
        side = side or ui.get("side") or "cause"
        chart_id = chart_id or session.get("current_chart_id")
        overlays = list(overlays or [])

        if chart_id:
            for ch in session.get("charts") or []:
                if ch["id"] == chart_id:
                    ch["overlays"] = overlays
                    break

        ev = self._active_event(session)
        if ev:
            els = ev.setdefault("elements", [])
            # 已记录的 chart overlay id → 元素（用于更新几何，不合并成一个）
            oid_to_el = {}
            for e in els:
                if e.get("kind") != "overlay":
                    continue
                oid = str((e.get("data") or {}).get("id") or "")
                if oid:
                    oid_to_el[oid] = e

            last_new = None
            for o in overlays:
                oid = str(o.get("id") or "")
                if not oid:
                    # 无 id 的线也作为新元素追加
                    el = self._new_element("overlay", dict(o))
                    els.append(el)
                    last_new = el
                    continue
                if oid in oid_to_el:
                    # 同图上同一条线：只更新该元素的 data（几何变化），仍是独立元素
                    oid_to_el[oid]["data"] = dict(o)
                else:
                    el = self._new_element("overlay", dict(o))
                    els.append(el)
                    oid_to_el[oid] = el
                    last_new = el

            ch = next(
                (c for c in session.get("charts") or [] if c.get("id") == chart_id),
                None,
            )
            if ch:
                ev["symbol"] = ch.get("symbol") or ev.get("symbol")
                ev["period"] = ch.get("period") or ev.get("period")
                ev["chart_id"] = chart_id
                if ch.get("visible_range") is not None:
                    ev["visible_range"] = ch.get("visible_range")
            self._sync_event_legacy_fields(ev)
            if last_new:
                ui["active_element_id"] = last_new["id"]
            return session

        if side == "effect":
            eid = ui.get("active_effect_id")
            ef = next((e for e in session.get("effects") or [] if e["id"] == eid), None)
            if ef and ef.get("phase") != "closed":
                ef["overlays"] = overlays
                if chart_id and chart_id not in (ef.get("chart_ids") or []):
                    ef.setdefault("chart_ids", []).append(chart_id)
        else:
            cid = ui.get("active_cause_id")
            ca = next((c for c in session.get("causes") or [] if c["id"] == cid), None)
            if ca and ca.get("state") != "closed":
                ca["overlays"] = overlays
                if chart_id and chart_id not in (ca.get("chart_ids") or []):
                    ca.setdefault("chart_ids", []).append(chart_id)
                ch = next(
                    (c for c in session.get("charts") or [] if c["id"] == chart_id),
                    None,
                )
                if ch:
                    ctxs = ca.setdefault("contexts", [])
                    ctxs[:] = [x for x in ctxs if x.get("chart_id") != chart_id]
                    ctxs.append(
                        {
                            "chart_id": chart_id,
                            "symbol": ch.get("symbol"),
                            "period": ch.get("period"),
                            "visible_range": ch.get("visible_range"),
                            "overlays": overlays,
                            "kbars": list(ch.get("kbars") or []),
                        }
                    )
        return session


_svc: Optional[SessionService] = None


def get_session_service() -> SessionService:
    global _svc
    if _svc is None:
        _svc = SessionService()
    return _svc
