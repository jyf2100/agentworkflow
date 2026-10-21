"""hub 排度核心——per-project 串行 + 峰值上限 + 重派状态机（add-hub-skeleton 任务 3.1-3.3）。

spec: execution-hub「队列排度」。引擎无状态（D2）：全部排度事实在 HubDB，
hub 重启 = 新引擎实例同 DB，语义天然延续（3.3）。单线程 tick 调用
（排度器单写者），pick → start → mark_running 顺序无并发窗口。
"""
from __future__ import annotations

import db


class QueueEngine:
    """排度决策层——选谁派、容器退出后队列项何去何从（run 终态归 lifecycle）。"""

    def __init__(self, database: db.HubDB, max_containers: int, max_retries: int, image_tag_default: str):
        self._db = database
        self._max_containers = max_containers
        self._max_retries = max_retries
        self._image_tag_default = image_tag_default

    def enqueue(self, project: str, prd_path: str, enqueued_by: str, image_tag: str | None = None) -> int:
        """入队固化 tag（F13）：未显式传 → 取配置默认；写入队列项，派发与 meta 均用记录值。"""
        tag = image_tag if image_tag is not None else self._image_tag_default
        if not tag:
            raise ValueError("image tag 缺失且无配置默认——拒绝入队（MUST NOT 在派发时解析「最新」）")
        return self._db.enqueue(project, prd_path, tag, enqueued_by)

    def get_item(self, item_id: int) -> dict:
        """队列项公共读面——api/cli 回读经引擎，不穿透 _db（本体论审核 B5）。"""
        return self._db.get_item(item_id)

    def pick_next(self) -> dict | None:
        """下一个可派队列项：峰值有名额 + 无 running project 的最老 pending（FIFO）。

        只读不改状态——lifecycle 起容器成功后才 mark_running（起失败走 fail_item）。
        """
        if self._db.count_running() >= self._max_containers:
            return None  # spec scenario「峰值上限约束」：等容器终态释放名额
        item_id = self._db.oldest_pending_excluding_running_projects()
        if item_id is None:
            return None
        return self._db.get_item(item_id)

    def mark_running(self, item_id: int) -> None:
        """容器已成功启动——占住 project 串行位与峰值名额。"""
        self._db.set_item_status(item_id, "running")

    def on_exit(self, item_id: int, exit_code: int) -> None:
        """容器退出回调：rc=0 → done；否则按 attempt 判重派或放弃（A3）。

        队列项 failed 是放弃决定（attempt 达上限），run 的 failed 是容器事实——
        两个生命周期分离；重派回 pending 保留 attempt，因入队更早天然 FIFO 优先。
        """
        if exit_code == 0:
            self._db.set_item_status(item_id, "done")
            return
        self._db.bump_attempt(item_id)
        attempt = self._db.get_item(item_id)["attempt"]
        reason = f"容器非零退出 exit_code={exit_code}（attempt={attempt}）"
        if attempt >= self._max_retries:
            self._db.fail_item(item_id, reason + "——重派超限，放弃")
        else:
            self._db.set_item_error(item_id, reason)
            self._db.set_item_status(item_id, "pending")

    def note_error(self, item_id: int, error: str) -> None:
        """只记原因不改状态（如起容器基础设施异常——保持 pending 待下 tick 重试）。"""
        self._db.set_item_error(item_id, error)

    def fail_item(self, item_id: int, reason: str) -> None:
        """直接终态 failed（如 F13 镜像缺失——重派无意义，修环境后重新入队）。"""
        self._db.fail_item(item_id, reason)
