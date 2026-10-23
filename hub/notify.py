"""hub MM 通知通道——bot token 推递（add-hub-slice-4a，spec: hub-notifications）。

F14 身份与通道分离：OAuth app（登录身份）与 bot token（推通知通道）是两个独立
凭据实体。事件面钉死为 hub DB 自有 run 终态行（D2）——机械读行转发，零 pa 格式
依赖；语义事件（critic/PR/verify）与无 run 放弃归后续切片事件出口。

收件人解析（D4 值形态三代分流）：enqueued_by = mm_user_id（2b 会话入队，26 字符
十六进制）直发；username（2a env 兜底 = cfg.user_id）经 users/username/{name}
解析 mm id 后发（内存缓存，解析失败记日志放弃——可见的丢非静默吞）；
`vault-deliver`（slice-3 机机字面量）回落 cfg.user_id 走 username 路径。

fail-open（D3/D5）：通知异常绝不阻断流水线——lifecycle 挂点 try/except 全捕获；
私信与频道广播并行互不依赖。通知零持久化（不落 db 不落卷，丢失兜底是读面查询）。
"""
from __future__ import annotations

import re
import sys
from typing import Protocol

import httpx

# Mattermost user id 公开事实：26 字符十六进制（分流判据用途，非拒收校验）
_MM_ID_RE = re.compile(r"[0-9a-f]{26}")

# 与 api._DELIVER_ENQUEUED_BY 同值（机机通道来源字面量）——测试锁相等防漂移
VAULT_DELIVER_LITERAL = "vault-deliver"

# 频道广播仅失败类终态（D5 噪音控制：成功进频道会训练人忽略频道）
_CHANNEL_BROADCAST_STATUSES = ("failed", "failed_to_start")


def build_notifier(cfg, mm=None) -> "Notifier | None":
    """装配判据（D8）：bot token + MM base_url 齐才造；缺任一 = None（通道关闭零行为）。"""
    if not (cfg.mm_bot_token and cfg.mm_base_url):
        return None
    return Notifier(cfg, mm=mm)


class MMClient(Protocol):
    """MM 推递鸭子面（D7/L5）——生产 HttpMMClient 与测试 FakeMMClient 的共享契约。

    lookup/post_dm/post_channel 是 Notifier 依赖面；me_id 供通道自检
    （cli check-notify 验 token 有效性）消费——同属生产/测试共享契约，防两面漂移。
    """

    def me_id(self) -> str: ...
    def lookup_user_id(self, username: str) -> str | None: ...
    def post_dm(self, mm_user_id: str, text: str) -> None: ...
    def post_channel(self, channel_id: str, text: str) -> None: ...


class HttpMMClient:
    """生产 MM 客户端——httpx 实现 MMClient 契约（auth.D8 注入式先例）。

    凭据（bot token）只进 Authorization header，绝不进错误消息/日志/异常文本。
    DM 两步（Mattermost 事实）：先 create direct channel 拿 channel_id 再发 post。
    timeout 3s（L2 审核修正）：通知在 reconcile/tick 同步路径上——MM 挂时每终态
    最多拖 3s 而非 10s（观察面不拖执行面节奏；fail-open 兜底不崩）。
    """

    def __init__(self, cfg, client: httpx.Client | None = None):
        self._cfg = cfg
        self._c = client or httpx.Client(timeout=3)
        self._me: str | None = None  # bot 自身 user id（lazy 取一次缓存）

    def _hdr(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._cfg.mm_bot_token}"}

    def _url(self, path: str) -> str:
        return f"{self._cfg.mm_base_url}/api/v4{path}"

    def me_id(self) -> str:
        if self._me is None:
            r = self._c.get(self._url("/users/me"), headers=self._hdr())
            r.raise_for_status()
            self._me = str(r.json()["id"])
        return self._me

    def lookup_user_id(self, username: str) -> str | None:
        """username → mm_user_id；查无/失败返回 None（调用方 warn 放弃，不抛）。"""
        r = self._c.get(self._url(f"/users/username/{username}"), headers=self._hdr())
        if r.status_code != 200:
            return None
        uid = r.json().get("id")
        return str(uid) if uid else None

    def post_dm(self, mm_user_id: str, text: str) -> None:
        me = self.me_id()
        r = self._c.post(self._url("/channels/direct"), json=[me, mm_user_id], headers=self._hdr())
        r.raise_for_status()
        chan = r.json()["id"]
        r2 = self._c.post(self._url("/posts"), json={"channel_id": chan, "message": text}, headers=self._hdr())
        r2.raise_for_status()

    def post_channel(self, channel_id: str, text: str) -> None:
        r = self._c.post(self._url("/posts"), json={"channel_id": channel_id, "message": text}, headers=self._hdr())
        r.raise_for_status()


class FakeMMClient:
    """测试替身——按 (recipient, text) 记调用清单（D7：测试只锁此契约，不锁 API 序列）。

    实现 MMClient 契约（与 HttpMMClient 同判）；可编程：user_ids（username→id
    解析表）、fail_lookup（解析返回 None 的名单）、crash_on_send / crash_on_channel
    （推递抛异常注入，验 fail-open——send 面全崩 vs 仅频道崩两档）。
    """

    def __init__(self, user_ids: dict[str, str] | None = None,
                 fail_lookup: set[str] | None = None, crash_on_send: Exception | None = None,
                 crash_on_channel: Exception | None = None, crash_on_me: Exception | None = None):
        self.user_ids = dict(user_ids or {})
        self.fail_lookup = set(fail_lookup or ())
        self.crash_on_send = crash_on_send
        self.crash_on_channel = crash_on_channel
        self.crash_on_me = crash_on_me
        self.dms: list[tuple[str, str]] = []
        self.channels: list[tuple[str, str]] = []
        self.lookups: list[str] = []

    def me_id(self) -> str:
        if self.crash_on_me is not None:
            raise self.crash_on_me
        return "f" * 26  # 假 bot id（26-hex 形态同真值）

    def lookup_user_id(self, username: str) -> str | None:
        self.lookups.append(username)
        if username in self.fail_lookup:
            return None
        return self.user_ids.get(username)

    def post_dm(self, mm_user_id: str, text: str) -> None:
        if self.crash_on_send is not None:
            raise self.crash_on_send
        self.dms.append((mm_user_id, text))

    def post_channel(self, channel_id: str, text: str) -> None:
        if (self.crash_on_send or self.crash_on_channel) is not None:
            raise self.crash_on_channel or self.crash_on_send
        self.channels.append((channel_id, text))


class Notifier:
    """run 终态通知器——消息构造 + 收件人三代分流 + 私信/频道并行推递。"""

    def __init__(self, cfg, mm=None):
        self._cfg = cfg
        self._mm = mm or HttpMMClient(cfg)
        self._uid_cache: dict[str, str] = {}  # username → mm_id（解析成功才缓存，D4）

    def notify_run(self, *, run_id: int, project: str, run_dir_name: str, status: str,
                   exit_code: int | None = None, enqueued_by: str | None = None,
                   initiator: str | None = None, last_error: str | None = None) -> None:
        text = self._message(run_id=run_id, project=project, run_dir_name=run_dir_name,
                             status=status, exit_code=exit_code, initiator=initiator,
                             last_error=last_error)
        try:  # 私信与频道并行互不依赖（D5）——各自 fail-open
            self._send_dm(enqueued_by, text)
        except Exception as e:  # noqa: BLE001——观察面绝不阻断执行面
            print(f"[notify][warn] run#{run_id} 私信推递异常（fail-open）: {type(e).__name__}", file=sys.stderr)
        if status in _CHANNEL_BROADCAST_STATUSES:
            chan = self._cfg.project_channels.get(project)
            if chan:
                try:
                    self._mm.post_channel(chan, text)
                except Exception as e:  # noqa: BLE001
                    print(f"[notify][warn] run#{run_id} 频道广播异常（fail-open）: {type(e).__name__}", file=sys.stderr)

    def notify_admin(self, text: str) -> None:
        """管理面告警（D6 陈旧检测等非 run 事件）：私信配置单用户（cfg.user_id，
        username 解析路径）；fail-open 不抛（调用方为周期检测循环）。"""
        try:
            self._send_dm(None, text)  # None → 回落 cfg.user_id
        except Exception as e:  # noqa: BLE001
            print(f"[notify][warn] 管理面告警推递异常（fail-open）: {type(e).__name__}", file=sys.stderr)

    def notify_event(self, *, text: str, enqueued_by: str | None = None,
                     project: str | None = None, broadcast: bool = False) -> None:
        """语义事件转发（4b，spec: hub-notifications「语义事件转发」）：私信 run 发起人
        （三代分流同 notify_run）；broadcast=True 且 project 配了频道才广播。fail-open：
        私信与频道并行互不依赖，绝不抛（调用方 reconcile 同步路径）。"""
        try:
            self._send_dm(enqueued_by, text)
        except Exception as e:  # noqa: BLE001——观察面绝不阻断执行面
            print(f"[notify][warn] 事件私信推递异常（fail-open）: {type(e).__name__}", file=sys.stderr)
        if broadcast and project:
            chan = self._cfg.project_channels.get(project)
            if chan:
                try:
                    self._mm.post_channel(chan, text)
                except Exception as e:  # noqa: BLE001
                    print(f"[notify][warn] 事件频道广播异常（fail-open）: {type(e).__name__}", file=sys.stderr)

    def _message(self, *, run_id: int, project: str, run_dir_name: str, status: str,
                 exit_code: int | None, initiator: str | None, last_error: str | None) -> str:
        head = f"[pa-hub] {project} run #{run_id}（{run_dir_name}）→ {status}"
        if exit_code is not None:
            head += f"，exit {exit_code}"
        if initiator:
            head += f"｜发起 {initiator}"
        if last_error:
            head += f"\n原因：{last_error}"
        return head

    def _send_dm(self, enqueued_by: str | None, text: str) -> None:
        """收件人三代分流（D4）：None/机机字面量 → cfg.user_id（username 路径）。"""
        recipient = enqueued_by if (enqueued_by and enqueued_by != VAULT_DELIVER_LITERAL) else self._cfg.user_id
        if _MM_ID_RE.fullmatch(recipient):
            self._mm.post_dm(recipient, text)
            return
        mm_id = self._uid_cache.get(recipient)
        if mm_id is None:
            mm_id = self._mm.lookup_user_id(recipient)
            if mm_id is None:
                # 可见的丢（stderr 留痕）非静默吞——观察面丢失兜底是读面查询
                print(f"[notify][warn] 收件人 {recipient!r} 无法解析 mm id——放弃本条通知", file=sys.stderr)
                return
            self._uid_cache[recipient] = mm_id
        self._mm.post_dm(mm_id, text)
