"""
services/update_task_factories.py - 数据更新任务工厂函数

新旧接口共用，消除重复 runner 代码。
"""
import threading
from typing import Optional

from services.update_task_service import update_task_service, UpdateTask
from core.cache import get_cache


def _result_detail(result) -> dict:
    """压缩 result 写入 task.detail，避免过大。"""
    if not isinstance(result, dict):
        return {'raw': str(result)[:200]}
    return {k: str(v)[:200] for k, v in result.items()}


def _compress_debt(debt) -> dict:
    """压缩 debt 写入 task.detail：summary + 各桶 total/lagging/max_lag。"""
    if not isinstance(debt, dict):
        return {'summary': str(debt)[:200]}
    out = {'summary': str(debt.get('summary') or '')[:300]}
    if 'needs_catchup' in debt:
        out['needs_catchup'] = bool(debt.get('needs_catchup'))
    for key in ('stocks', 'indices', 'boards'):
        bucket = debt.get(key)
        if not isinstance(bucket, dict):
            continue
        out[key] = {
            'total': bucket.get('total'),
            'lagging': bucket.get('lagging'),
            'max_lag': bucket.get('max_lag'),
            'available': bucket.get('available', True),
            'error': str(bucket.get('error') or '')[:160],
        }
    return out


_FORCE_AREA_META = {
    'indices': ('指数日线', 'QMT / Tushare / 全球指数数据源'),
    'stocks': ('个股日线', 'QMT'),
    'boards': ('板块日线', 'Tushare'),
    'higher_periods': ('个股/指数高周期', '本地日线物化'),
    'weekly_monthly': ('板块周月线', '本地日线重采样'),
}


def _debt_area_statuses(debt) -> dict:
    """Build structured per-area state without merging source outcomes."""
    areas = {}
    for key in ('indices', 'stocks', 'boards'):
        label, source = _FORCE_AREA_META[key]
        bucket = debt.get(key) if isinstance(debt, dict) else None
        if not isinstance(bucket, dict):
            areas[key] = {
                'label': label,
                'source': source,
                'status': 'unavailable',
                'message': '欠更状态不可用',
            }
            continue
        if bucket.get('available') is False or bucket.get('error'):
            areas[key] = {
                'label': label,
                'source': source,
                'status': 'unavailable',
                'message': str(bucket.get('error') or '数据范围状态不可用')[:200],
            }
            continue
        lagging = int(bucket.get('lagging') or 0)
        total = int(bucket.get('total') or 0)
        areas[key] = {
            'label': label,
            'source': source,
            'status': 'pending' if lagging else 'fresh',
            'message': f'{lagging}/{total} 个标的欠更' if lagging else '已是最新',
        }
    return areas


def _result_area_statuses(result) -> dict:
    """Expose every daily area independently for the force-refresh UI."""
    result = result if isinstance(result, dict) else {}
    pending = set(result.get('pending_stages') or [])
    areas = {}
    for key, (label, source) in _FORCE_AREA_META.items():
        stage = result.get(key)
        if not isinstance(stage, dict):
            overall_message = str(result.get('message') or '')[:200]
            if result.get('deferred'):
                status = 'deferred'
            elif result.get('running'):
                status = 'pending'
            else:
                status = 'pending' if key in pending else 'unavailable'
            areas[key] = {
                'label': label,
                'source': source,
                'status': status,
                'message': overall_message or '本轮未返回更新结果',
            }
            continue

        message = str(stage.get('message') or stage.get('error') or '')[:200]
        if stage.get('canceled'):
            status = 'canceled'
            message = message or '更新已取消'
        elif stage.get('error'):
            status = 'failed'
        elif stage.get('deferred'):
            status = 'deferred'
        elif key in pending:
            status = 'pending'
        else:
            status = 'fresh'
            message = message or '更新完成'
        areas[key] = {
            'label': label,
            'source': source,
            'status': status,
            'message': message,
        }
    return areas


def _scan_update_debt_safe():
    """优先真实 scan_update_debt；依赖未落地时返回最小 stub。"""
    try:
        from data_update_manager import scan_update_debt
        return scan_update_debt()
    except Exception:
        return {
            'summary': '欠更扫描不可用',
            # Unknown freshness must not be reported as "no debt".  Force a
            # best-effort update; each source still reports its own outcome.
            'needs_catchup': True,
        }


def _full_update_already_running() -> bool:
    """检测全量日更是否已在进行。

    只读内存标志，禁止调用 get_update_status（其会扫欠更，秒级阻塞）。
    """
    try:
        import data_update_manager as dum
        # data_update_manager owns the guard.  Keep the attribute fallback for
        # old deployments/tests that provide only the legacy seam.
        reader = getattr(dum, 'is_full_update_in_progress', None)
        if callable(reader):
            return bool(reader())
        return bool(getattr(dum, '_full_update_in_progress', False))
    except Exception:
        return False


def _quick_spot_refresh():
    """轻量即时刷新：只清 spot 缓存，不发起网络请求（避免 force 被 Tushare/HTTP 卡住）。"""
    from services.board_spot_cache import BoardSpotCache
    BoardSpotCache.get_instance().invalidate_all()


def create_force_update_task() -> UpdateTask:
    """创建强制刷新任务：即时清缓存 + 轻量 spot；欠更时后台 fire-and-forget 全量补齐。"""
    svc = update_task_service

    # runner 的即时部分会很快 success，但后台 debt/catchup 仍可能运行。
    # 在后台真正结束前复用同一任务，避免连续点击创建多个扫描线程。
    active_background_states = {'scheduled', 'scanning', 'running'}
    existing = next(
        (
            t for t in svc.list_tasks()
            if t.type == 'force' and (
                t.status in ('pending', 'running')
                or (t.detail or {}).get('background_state') in active_background_states
            )
        ),
        None,
    )
    if existing:
        return existing

    def runner(task: UpdateTask, cancel_check):
        # 同步路径只做内存级操作，必须 <100ms（欠更扫描/全量补齐全部丢后台）
        if cancel_check and cancel_check():
            task.status = 'canceled'
            task.message = '用户已取消'
            return

        get_cache().clear()
        _quick_spot_refresh()

        if cancel_check and cancel_check():
            task.status = 'canceled'
            task.message = '用户已取消'
            return

        full_running = _full_update_already_running()
        task.detail['full_already_running'] = bool(full_running)
        # 后台线程会回写；先给默认值
        task.detail['background_catchup'] = False
        task.detail['background_state'] = 'scheduled'
        task.detail['debt_before'] = None
        task.detail['areas'] = {}

        def _bg_debt_and_catchup():
            """扫欠更 + 必要时 force 全量；不阻塞 force 任务成功。

            进度写入 task.detail（后台线程回写，不阻塞 ↻ 同步路径）：
              stage / stocks_done / stocks_pending / catchup_message
            """
            try:
                task.detail['background_state'] = 'scanning'
                debt = _scan_update_debt_safe()
                task.detail['debt_before'] = _compress_debt(debt)
                task.detail['areas'] = _debt_area_statuses(debt)
            except Exception:
                debt = {'needs_catchup': True, 'summary': ''}
                task.detail['areas'] = _debt_area_statuses(debt)
            needs = bool(debt.get('needs_catchup'))
            if _full_update_already_running():
                task.detail['full_already_running'] = True
                task.detail['background_catchup'] = False
                task.detail['stage'] = 'skipped_full_running'
                task.detail['background_state'] = 'running_elsewhere'
                return
            if not needs:
                task.detail['background_catchup'] = False
                task.detail['stage'] = 'no_debt'
                task.detail['background_state'] = 'complete'
                return
            try:
                from data_update_manager import update_all_today
                task.detail['background_catchup'] = True
                task.detail['stage'] = 'catchup_start'
                task.detail['background_state'] = 'running'
                task.detail['catchup_message'] = '后台全量补齐开始'

                def _progress(stage, current, total, message):
                    try:
                        task.detail['stage'] = str(stage or '')
                        task.detail['catchup_message'] = str(message or '')[:300]
                        # stocks 阶段：current/total 为 0-4 步；细粒度在 stocks result
                        if stage == 'stocks':
                            task.detail['stocks_step'] = current
                            task.detail['stocks_steps_total'] = total
                        elif stage == 'indices':
                            task.detail['indices_step'] = current
                        elif stage == 'boards':
                            task.detail['boards_step'] = current
                    except Exception:
                        pass

                res = update_all_today(force=True, progress_callback=_progress)
                task.detail['catchup_result'] = _result_detail(res) if res else {}
                # 从子结果提取 pending/success 便于 UI
                if isinstance(res, dict):
                    task.detail['areas'] = _result_area_statuses(res)
                    st = res.get('stocks') if isinstance(res.get('stocks'), dict) else {}
                    bd = res.get('boards') if isinstance(res.get('boards'), dict) else {}
                    ix = res.get('indices') if isinstance(res.get('indices'), dict) else {}
                    if st:
                        task.detail['stocks_done'] = st.get('success')
                        task.detail['stocks_pending'] = st.get('pending')
                        task.detail['stocks_skipped_up_to_date'] = st.get('skipped_up_to_date')
                    if bd:
                        task.detail['boards_done'] = bd.get('success') or bd.get('sqlite_written')
                        task.detail['boards_pending_lagging'] = bd.get('pending_lagging')
                    if ix:
                        task.detail['indices_done'] = ix.get('success')
                task.detail['stage'] = 'catchup_done'
                task.detail['background_state'] = 'complete'
                task.detail['catchup_message'] = '后台全量补齐结束'
                try:
                    debt_after = _scan_update_debt_safe()
                    task.detail['debt_after'] = _compress_debt(debt_after)
                except Exception:
                    pass
            except Exception as e:
                task.detail['stage'] = 'catchup_error'
                task.detail['background_state'] = 'error'
                task.detail['catchup_message'] = str(e)[:200]
                if not task.detail.get('areas'):
                    task.detail['areas'] = _debt_area_statuses({})

        # 无论是否已有全量，都起后台扫欠更（已有全量时 bg 函数会早退不重复跑）
        threading.Thread(
            target=_bg_debt_and_catchup,
            name='force-bg-debt-catchup',
            daemon=True,
        ).start()

        task.progress = 1.0
        if full_running:
            task.message = '界面已即时刷新（后台日更进行中）'
        else:
            task.message = '界面已即时刷新'

    return svc.create_task(
        'force', runner,
        detail={'description': '即时刷新 + 可选后台补齐'},
    )


def create_boards_update_task() -> UpdateTask:
    """创建板块更新任务（仅 update_all_boards，不跑全量）。"""
    svc = update_task_service

    if svc.has_running('boards'):
        running = next(
            (t for t in svc.list_tasks() if t.type == 'boards' and t.status == 'running'),
            None
        )
        if running:
            return running

    def runner(task: UpdateTask, cancel_check):
        from data_update_manager import update_all_boards

        task.detail['stage'] = 'boards'
        task.message = '正在更新板块数据...'
        task.progress = 0.05
        result = update_all_boards(cancel_check=cancel_check)
        task.detail['result'] = _result_detail(result)

        if result and result.get('canceled'):
            task.status = 'canceled'
            task.message = '用户已取消'
            return

        if cancel_check and cancel_check():
            task.status = 'canceled'
            task.message = '用户已取消'
            return

        if not result:
            task.status = 'failed'
            task.message = '板块更新无返回结果'
            return

        err = result.get('error')
        success_n = result.get('success', 0)
        try:
            success_n = int(success_n or 0)
        except (TypeError, ValueError):
            success_n = 0

        if err or success_n <= 0:
            task.status = 'failed'
            if err:
                task.message = f'板块更新失败: {err}'
            else:
                task.message = (
                    f"板块更新失败: success={success_n}, "
                    f"failed={result.get('failed', 0)}, "
                    f"total={result.get('total', 0)}"
                )
            return

        task.progress = 1.0
        task.message = f"板块更新完成: success={success_n}"

    return svc.create_task('boards', runner, detail={'description': '板块更新'})


def create_stock_update_task(code: str) -> UpdateTask:
    """创建单只个股更新任务。"""
    svc = update_task_service
    task_key = f'stock:{code}'

    if svc.has_running(task_key):
        running = next(
            (t for t in svc.list_tasks() if t.type == task_key and t.status == 'running'),
            None
        )
        if running:
            return running

    def runner(task: UpdateTask, cancel_check):
        from data_update_manager import fetch_qmt_kline
        from data.sqlite_repo import get_sqlite_repo
        from core.cache import get_cache

        if cancel_check and cancel_check():
            task.status = 'canceled'
            task.message = '用户已取消'
            return

        task.message = f'正在更新个股 {code}...'
        task.progress = 0.1

        rows = fetch_qmt_kline(code, '20200101')
        if rows:
            import pandas as pd
            df = pd.DataFrame(rows)
            db = get_sqlite_repo()
            db.save_kline(code, 'daily', df)
            get_cache().delete(f'stock:{code}:daily')
            task.detail['success'] = True
            task.detail['rows'] = len(rows)
        else:
            task.detail['success'] = False

        if cancel_check and cancel_check():
            task.status = 'canceled'
            task.message = '用户已取消'
            return

        task.progress = 1.0

    return svc.create_task(task_key, runner, detail={'code': code, 'description': f'个股 {code} 更新'})
