"""
services/update_task_service.py - 数据更新任务中心

提供任务跟踪、状态查询、重复启动保护、取消能力。
任务状态：pending -> running -> success/failed/canceled
"""
import logging
import threading
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Dict, List, Optional, Callable

logger = logging.getLogger('update_task')


@dataclass
class UpdateTask:
    """单个更新任务的完整状态"""
    id: str
    type: str            # force / boards / stock
    status: str = 'pending'   # pending / running / success / failed / canceled
    progress: float = 0.0     # 0.0 ~ 1.0
    message: str = ''
    started_at: str = ''
    finished_at: Optional[str] = None
    error: Optional[str] = None
    detail: Dict = field(default_factory=dict)  # 额外信息（code, board_count 等）

    def to_dict(self) -> dict:
        return asdict(self)


class UpdateTaskService:
    """数据更新任务管理服务（线程安全）"""

    MAX_HISTORY = 50  # 最多保留 50 条历史记录

    def __init__(self):
        self._tasks: Dict[str, UpdateTask] = {}
        self._lock = threading.Lock()
        self._running_types: set = set()  # 正在运行的任务类型（防重复）
        self._cancel_flags: Dict[str, bool] = {}

    def _now(self) -> str:
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    def create_task(
        self,
        task_type: str,
        runner: Callable[[UpdateTask, Callable], None],
        detail: Optional[dict] = None,
    ) -> UpdateTask:
        """创建并启动一个更新任务。

        参数:
            task_type: 任务类型（force / boards / stock）
            runner: 执行函数，签名为 (task, cancel_check) -> None
                    runner 内部应定期调用 cancel_check()，
                    返回 True 表示用户已取消。
            detail: 额外的任务详情（如 stock code）

        返回:
            创建的 UpdateTask 对象
        """
        with self._lock:
            # 防重复：同一类型任务不能同时运行
            if task_type in self._running_types:
                existing = next(
                    (t for t in self._tasks.values()
                     if t.type == task_type and t.status == 'running'),
                    None
                )
                if existing:
                    existing.message = '已有同类型任务在运行中'
                    return existing

            task = UpdateTask(
                id=str(uuid.uuid4())[:8],
                type=task_type,
                status='pending',
                message='等待启动...',
                started_at=self._now(),
                detail=detail or {},
            )
            self._tasks[task.id] = task
            self._cancel_flags[task.id] = False
            self._running_types.add(task_type)

        # 启动后台线程
        def _wrapper():
            task.status = 'running'
            task.message = '正在执行...'

            def cancel_check():
                return self._cancel_flags.get(task.id, False)

            try:
                runner(task, cancel_check)
                # 如果 runner 已自行设置终态，不覆盖
                if task.status in ('canceled', 'failed'):
                    pass
                elif cancel_check():
                    task.status = 'canceled'
                    task.message = '用户已取消'
                else:
                    task.status = 'success'
                    task.message = '完成'
                    task.progress = 1.0
            except Exception as e:
                task.status = 'failed'
                task.message = f'失败: {str(e)[:200]}'
                task.error = str(e)
                logger.warning("[UpdateTask] task %s failed: %s", task.id, e, exc_info=True)
            finally:
                task.finished_at = self._now()
                with self._lock:
                    self._running_types.discard(task_type)
                    self._cleanup_history()

        t = threading.Thread(target=_wrapper, daemon=True, name=f'update-{task_type}')
        t.start()
        return task

    def cancel_task(self, task_id: str) -> dict:
        """尝试取消任务。

        返回:
            {'ok': bool, 'reason': str}
            - pending 任务：直接标记取消，ok=True, reason='CANCELED'
            - running 任务：设置 cancel flag，ok=True, reason='CANCEL_REQUESTED'
              runner 内部通过 cancel_check() 检测后协作退出
            - 已完成/不存在：ok=False
        """
        with self._lock:
            if task_id not in self._tasks:
                return {'ok': False, 'reason': 'NOT_FOUND'}
            task = self._tasks[task_id]
            if task.status == 'pending':
                self._cancel_flags[task_id] = True
                task.status = 'canceled'
                task.message = '用户已取消（pending 阶段）'
                task.finished_at = self._now()
                self._running_types.discard(task.type)
                return {'ok': True, 'reason': 'CANCELED'}
            if task.status == 'running':
                # 协作式取消：设置 flag，由 runner 检测后退出
                self._cancel_flags[task_id] = True
                task.message = '取消请求已发送，等待当前阶段完成...'
                return {'ok': True, 'reason': 'CANCEL_REQUESTED'}
            return {'ok': False, 'reason': 'NOT_CANCELLABLE'}

    def get_task(self, task_id: str) -> Optional[UpdateTask]:
        return self._tasks.get(task_id)

    def list_tasks(self, limit: int = 20) -> List[UpdateTask]:
        """返回最近的任务列表（按创建时间倒序）。"""
        with self._lock:
            tasks = sorted(
                self._tasks.values(),
                key=lambda t: t.started_at,
                reverse=True,
            )
        return tasks[:limit]

    def has_running(self, task_type: Optional[str] = None) -> bool:
        """检查是否有正在运行的任务。"""
        with self._lock:
            if task_type:
                return task_type in self._running_types
            return len(self._running_types) > 0

    def _cleanup_history(self):
        """清理过期历史记录，保留最近的 MAX_HISTORY 条。"""
        if len(self._tasks) <= self.MAX_HISTORY:
            return
        # 按 started_at 排序，删除最旧的已完成任务
        finished = [
            (tid, t) for tid, t in self._tasks.items()
            if t.status in ('success', 'failed', 'canceled')
        ]
        finished.sort(key=lambda x: x[1].started_at)
        to_remove = len(self._tasks) - self.MAX_HISTORY
        for tid, _ in finished[:to_remove]:
            self._tasks.pop(tid, None)
            self._cancel_flags.pop(tid, None)


# 全局单例
update_task_service = UpdateTaskService()
