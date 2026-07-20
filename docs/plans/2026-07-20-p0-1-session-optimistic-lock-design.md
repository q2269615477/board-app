# 2026-07-20 P0-1: 会话乐观锁设计

> 状态：已与用户确认（Brainstorming 阶段 → 方案 C 混合）
> 范围：仅 `save_progress` 路径 + `commit`；actions 保持 patch 不变

## 1. 背景

当前 `save_progress` 接收前端整包 payload，merge 后写库。
风险：多标签/多设备/快连点 → 后写覆盖先写（Last-Write-Wins）。

## 2. 目标

- **save_progress / commit** 路径：客户端必须带 `base_rev`；不匹配 → 409 + 返回服务端当前 `rev`。
- **actions** 路径：保持 patch 模型，不强制 `base_rev`，但仍 `rev++` 让前端可同步。
- 前端：在 save/commit 流程中维护 `S.rev`，冲突 409 时弹窗"服务端已变更"（展示 diff/force 选项，先 MVP 仅提供"刷新并合并"和"取消"）。

## 3. 数据模型

### 3.1 Session payload 新增字段

```json
{
  "id": "sess_...",
  "rev": 7,                    // 整型，从 0 起；upsert 时 +1
  "base_rev": 7,               // 仅请求体（不入库）
  ...
}
```

- `rev` 写入 `payload_json`（无需改 DB schema）。
- 旧会话读出后 `normalize_session` 补 `rev=0`。

### 3.2 错误码

- `REVISION_CONFLICT` (409) — `save_progress`/`commit` 检测到 `base_rev != current.rev`
  - 响应体：
    ```json
    {
      "ok": false,
      "code": "REVISION_CONFLICT",
      "error": "会话版本过期，请刷新后重试",
      "current_rev": 9,
      "current_session": { ... }    // 整包，前端可展示
    }
    ```

## 4. 接口契约

### 4.1 `PUT /api/sessions/<id>` (save)

请求体：
```json
{
  "session": { "id": "...", "rev": 7, "base_rev": 7, ... },
  "write_vault": true
}
```

- 头部/响应：见错误码。
- 成功 → 返 `{ ok:true, data:{ session: { ..., rev: 8 } } }`

### 4.2 `POST /api/sessions/<id>/commit`

- 同样接受 `base_rev`，冲突时 409。

### 4.3 `POST /api/sessions/<id>/actions`

- 不需要 `base_rev`（patch 永远基于服务端最新）。
- 响应 `data.session.rev` 自增。

## 5. 后端实现要点

### 5.1 `services/session_service.py`

- `save_progress(session, write_vault, expected_rev=None)`：
  - 若 `expected_rev is not None and (existing_rev or 0) != expected_rev` → raise `RevisionConflictError(current_rev, current_session)`
  - 写完后 `session["rev"] = (existing_rev or 0) + 1`
- `commit_session(session_id, payload, expected_rev=None)`：同上。
- `_bump_rev(session, existing)`：辅助方法。
- `actions` 写完统一调 `_bump_rev`。

### 5.2 `api/session_routes.py`

- `save_session` / `commit_session` 提取 `body["base_rev"]` 传给 service。
- 捕获 `RevisionConflictError` → 返 409 + `{ code:'REVISION_CONFLICT', current_rev, current_session }`。
- `session_action` 不传 `expected_rev`（保持 patch 自由）。

## 6. 前端实现要点

### 6.1 `static/js/session-ui.js`

- 全局 `S.rev` 维护。
- `onSaveCommit()`：取 `S.rev` 作为 `base_rev` 发请求。
- 409 处理：
  ```js
  if (resp.code === 'REVISION_CONFLICT') {
    showConflictDialog({
      current_rev: resp.current_rev,
      current_session: resp.current_session,
      onReload: () => { S = resp.current_session; render(); },
      onCancel: () => {},
    });
  }
  ```
- actions 响应里 `data.session.rev` 同步到 `S.rev`。

### 6.2 UI

- 顶部小标：`会话 rev: 7`（仅 drafting 显）。
- 冲突弹窗（toast 即可，MVP 不上重 modal）：
  > 会话已在另一端变更（rev 7→9）。点击「应用服务端版本」以刷新并继续；或「保留本地」则下次保存仍会冲突。

## 7. 测试用例

`tests/test_session_revision.py`：

1. `test_new_session_has_rev_zero` — 新建 `rev==0`
2. `test_save_progress_increments_rev` — 每次 save `rev += 1`
3. `test_save_with_matching_base_rev_succeeds` — base_rev=0 → 200 + rev=1
4. `test_save_with_mismatched_base_rev_raises_conflict` — base_rev=0 但服务端 rev=2 → ConflictError + 携带 current_rev=2, current_session
5. `test_actions_bump_rev_without_base_rev` — actions 不传 base_rev 也能写
6. `test_commit_also_enforces_base_rev` — 同 save
7. `test_normalize_session_backfills_rev` — 旧会话无 rev → 0

## 8. 风险与回退

- **风险**：旧客户端未带 `base_rev` → 老 PUT 会失败（因为服务端默认期望 None=skip 检查 → 不影响；只有当 body 显式传 base_rev 才校验）
  - 缓解：`save_progress` 仅在 `expected_rev is not None` 时校验；前端可逐步上线。
- **风险**：`payload_json` 中 rev 字段是后端写，前端篡改 rev 会被 bump 覆盖（无问题）。
- **回退**：rev 字段纯 payload 内；删掉校验代码即恢复原行为。

## 9. 验收

- `pytest tests/test_session_revision.py tests/test_session_causal.py -q` 全绿
- 手动：开两个标签页 A/B 同时改同一会话，A 保存成功 rev+1；B 保存 → 409 + 弹冲突。
