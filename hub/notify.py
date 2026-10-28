"""hub MM 通知通道——bot token 推递（add-hub-slice-4a，spec: hub-notifications）。

F14 身份与通道分离：OAuth app（登录身份）与 bot token（推通知通道）是两个独立
凭据实体。事件面钉死为 hub DB 自有 run 终态行（D2）——机械读行转发，零 pa 格式
依赖；语义事件（critic/PR/verify）与无 run 放弃归后续切片事件出口。

归属路由（add-mm-channel-intake 6.x）：频道关系唯一真相 = hub DB project_channels
绑定表（旧 HUB_MM_PROJECT_CHANNELS env 映射已废止——仅 serve 启动 seed 迁移消费）。
project 已绑定 → 推频道（含 `<@发起人>` 提及——MM 提及通知强度替代私信，且协作
可见）且不再重复私信；未绑定 → 既有 per-user 私信（D4 值形态三代分流不变）：
enqueued_by = mm_user_id（2b 会话入队，26 字符十六进制）直发；username（2a env
兜底 = cfg.user_id）经 users/username/{name} 解析 mm id 后发（内存缓存，解析失败
记日志放弃——可见的丢非静默吞）；`vault-deliver`（slice-3 机机字面量）回落
cfg.user_id 走 username 路径。绑定频道提及目标同源解析（26-hex 直拼；非 26-hex
经 username 解析，失败降级无提及纯文本——消息照发）。

fail-open（D3/D5）：通知异常绝不阻断流水线——lifecycle 挂点 try/except 全捕获。
频道 post 失败 fail-open 不回退私信（双通道同事件是噪音源）。通知零持久化（不落
db 不落卷，丢失兜底是读面查询——绑定表读是路由判据非持久写面）。
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


def build_notifier(cfg, mm=None, database=None) -> "Notifier | None":
    """装配判据（D8）：bot token + MM base_url 齐才造；缺任一 = None（通道关闭零行为）。

    database（add-mm-channel-intake 6.x）：绑定表读面（归属路由判据）。None =
    无绑定判据可用（旧装配形态/部分单测）→ 恒走私信回落，绝无频道路由。"""
    if not (cfg.mm_bot_token and cfg.mm_base_url):
        return None
    return Notifier(cfg, mm=mm, database=database)


class MMChannelNotFound(Exception):
    """GET /channels/{id}/members 返回 404——频道不存在（确定性配置错；
    add-mm-channel-intake D3：与 UNKNOWN 分立，不混入「判据不可用」）。"""


class MMClient(Protocol):
    """MM 推递鸭子面（D7/L5）——生产 HttpMMClient 与测试 FakeMMClient 的共享契约。

    lookup/post_dm/post_channel 是 Notifier 依赖面；me_id 供通道自检
    （cli check-notify 验 token 有效性）消费；channel_member_ids 供入站授权面
    （add-mm-channel-intake D3 三态：返回 None = UNKNOWN 判据不可用——网络错/5xx/
    403，绝非「非成员」结论；404 频道不存在抛 MMChannelNotFound）。
    """

    def me_id(self) -> str: ...
    def lookup_user_id(self, username: str) -> str | None: ...
    def post_dm(self, mm_user_id: str, text: str) -> None: ...
    def post_channel(self, channel_id: str, text: str) -> None: ...
    def channel_member_ids(self, channel_id: str) -> "list[str] | None": ...


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

    def channel_member_ids(self, channel_id: str) -> list[str] | None:
        """频道成员 user_id 全集（page/per_page=200 翻页取全）。三态契约（D3）：
        404 抛 MMChannelNotFound；网络错/5xx/403（bot 无权限——判据不可用而非授权
        结论）返回 None = UNKNOWN；200 返回成员 id 列表（空列表也是合法 FOUND）。"""
        ids: list[str] = []
        page = 0
        while True:
            try:
                r = self._c.get(
                    self._url(f"/channels/{channel_id}/members"),
                    params={"page": page, "per_page": 200}, headers=self._hdr(),
                )
            except httpx.HTTPError:
                return None  # 网络错 = UNKNOWN
            if r.status_code == 404:
                raise MMChannelNotFound(channel_id)
            if r.status_code != 200:
                return None  # 5xx/403 等 = UNKNOWN
            try:
                batch = r.json()
            except ValueError:
                return None  # 契约异常形态 = 不可判
            if not isinstance(batch, list):
                return None
            ids.extend(str(m["user_id"]) for m in batch if isinstance(m, dict) and m.get("user_id"))
            if len(batch) < 200:
                return ids
            page += 1


class FakeMMClient:
    """测试替身——按 (recipient, text) 记调用清单（D7：测试只锁此契约，不锁 API 序列）。

    实现 MMClient 契约（与 HttpMMClient 同判）；可编程：user_ids（username→id
    解析表）、fail_lookup（解析返回 None 的名单）、crash_on_send / crash_on_channel
    （推递抛异常注入，验 fail-open——send 面全崩 vs 仅频道崩两档）；成员面
    （add-mm-channel-intake 3.1）：channel_members（channel→成员 id 列表 = FOUND）、
    crash_on_members（查询抛异常——判层面归 UNKNOWN）、members_forbidden（403 →
    None = UNKNOWN）、members_not_found（404 → MMChannelNotFound）。
    """

    def __init__(self, user_ids: dict[str, str] | None = None,
                 fail_lookup: set[str] | None = None, crash_on_send: Exception | None = None,
                 crash_on_channel: Exception | None = None, crash_on_me: Exception | None = None,
                 channel_members: dict[str, list[str]] | None = None,
                 crash_on_members: Exception | None = None,
                 members_forbidden: set[str] | None = None,
                 members_not_found: set[str] | None = None):
        self.user_ids = dict(user_ids or {})
        self.fail_lookup = set(fail_lookup or ())
        self.crash_on_send = crash_on_send
        self.crash_on_channel = crash_on_channel
        self.crash_on_me = crash_on_me
        self.channel_members = dict(channel_members or {})
        self.crash_on_members = crash_on_members
        self.members_forbidden = set(members_forbidden or ())
        self.members_not_found = set(members_not_found or ())
        self.dms: list[tuple[str, str]] = []
        self.channels: list[tuple[str, str]] = []
        self.lookups: list[str] = []
        self.member_queries: list[str] = []

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

    def channel_member_ids(self, channel_id: str) -> list[str] | None:
        self.member_queries.append(channel_id)
        if self.crash_on_members is not None:
            raise self.crash_on_members
        if channel_id in self.members_not_found:
            raise MMChannelNotFound(channel_id)
        if channel_id in self.members_forbidden:
            return None  # 403 = UNKNOWN（判据不可用）
        return list(self.channel_members.get(channel_id, []))


class Notifier:
    """run 终态通知器——消息构造 + 归属路由（绑定频道/私信三代分流）推递。"""

    def __init__(self, cfg, mm=None, database=None):
        self._cfg = cfg
        self._mm = mm or HttpMMClient(cfg)
        self._db = database  # 绑定表读面（None = 无归属判据 → 恒私信回落）
        self._uid_cache: dict[str, str] = {}  # username → mm_id（解析成功才缓存，D4）

    def _binding_channel(self, project: str | None) -> str | None:
        """归属路由判据：project 绑定频道 id（无绑定/无判据面 = None → 私信回落）。"""
        if project is None or self._db is None:
            return None
        binding = self._db.get_channel_binding_by_project(project)
        return binding["channel_id"] if binding else None

    def _mention(self, enqueued_by: str | None) -> str:
        """频道提及前缀（add-mm-channel-intake 6.1）：26-hex 直拼 `<@id>`；非 26-hex
        （含 None / 机机字面量 → cfg.user_id）经 username 解析（同 _uid_cache 同
        fail-open）；解析失败降级空串——纯文本消息照发（提及是增强非送达前提）。"""
        recipient = enqueued_by if (enqueued_by and enqueued_by != VAULT_DELIVER_LITERAL) else self._cfg.user_id
        if _MM_ID_RE.fullmatch(recipient):
            return f"<@{recipient}> "
        mm_id = self._uid_cache.get(recipient)
        if mm_id is None:
            mm_id = self._mm.lookup_user_id(recipient)
            if mm_id is None:
                print(f"[notify][warn] 提及目标 {recipient!r} 无法解析 mm id——降级纯文本", file=sys.stderr)
                return ""
            self._uid_cache[recipient] = mm_id
        return f"<@{mm_id}> "

    def _post_bound_channel(self, channel_id: str, text: str, tag: str) -> None:
        """绑定频道推递（fail-open：异常 warn 可见，不回退私信——双通道同事件是噪音源）。"""
        try:
            self._mm.post_channel(channel_id, text)
        except Exception as e:  # noqa: BLE001——观察面绝不阻断执行面
            print(f"[notify][warn] {tag} 频道推递异常（fail-open，不回退私信）: {type(e).__name__}", file=sys.stderr)

    def notify_run(self, *, run_id: int, project: str, run_dir_name: str, status: str,
                   exit_code: int | None = None, enqueued_by: str | None = None,
                   initiator: str | None = None, last_error: str | None = None) -> None:
        text = self._message(run_id=run_id, project=project, run_dir_name=run_dir_name,
                             status=status, exit_code=exit_code, initiator=initiator,
                             last_error=last_error)
        chan = self._binding_channel(project)
        if chan:  # 绑定 → 频道全终态（成败皆进——频道=项目工作区）+ @提及，不重复私信
            self._post_bound_channel(chan, f"{self._mention(enqueued_by)}{text}", f"run#{run_id}")
            return
        try:
            self._send_dm(enqueued_by, text)
        except Exception as e:  # noqa: BLE001——观察面绝不阻断执行面
            print(f"[notify][warn] run#{run_id} 私信推递异常（fail-open）: {type(e).__name__}", file=sys.stderr)

    def notify_admin(self, text: str) -> None:
        """管理面告警（D6 陈旧检测等**无 project 归属**事件）：私信配置单用户
        （cfg.user_id，username 解析路径）；fail-open 不抛（调用方为周期检测循环）。
        有 project 归属的告警（无 run 放弃）走 notify_abandon 归属路由。"""
        try:
            self._send_dm(None, text)  # None → 回落 cfg.user_id
        except Exception as e:  # noqa: BLE001
            print(f"[notify][warn] 管理面告警推递异常（fail-open）: {type(e).__name__}", file=sys.stderr)

    def notify_abandon(self, project: str, text: str) -> None:
        """无 run 放弃告警归属路由（6.2，spec: hub-notifications「无 run 放弃告警」）：
        project 绑定频道 → 频道（团队可见「本 project 的需求没跑起来」——配置错多为
        一次性，纯事实文本无提及）；未绑定 → 配置单用户 DM（同 notify_admin 形态）。
        fail-open 不抛（调用方 _abandon 同步路径）。"""
        chan = self._binding_channel(project)
        if chan:
            self._post_bound_channel(chan, text, "abandon")
            return
        try:
            self._send_dm(None, text)
        except Exception as e:  # noqa: BLE001
            print(f"[notify][warn] 放弃告警推递异常（fail-open）: {type(e).__name__}", file=sys.stderr)

    def notify_event(self, *, text: str, enqueued_by: str | None = None,
                     project: str | None = None) -> None:
        """语义事件转发（4b + 6.2，spec: hub-notifications「语义事件通知」）：按归属
        路由——绑定频道 → 频道全量（四类事件皆进：全事件时间线语义，per-event 广播
        判定已废止）+ @提及，不重复私信；未绑定 → 私信发起人（三代分流）。fail-open
        绝不抛（调用方 reconcile 同步路径）。"""
        chan = self._binding_channel(project)
        if chan:
            self._post_bound_channel(chan, f"{self._mention(enqueued_by)}{text}", "event")
            return
        try:
            self._send_dm(enqueued_by, text)
        except Exception as e:  # noqa: BLE001——观察面绝不阻断执行面
            print(f"[notify][warn] 事件私信推递异常（fail-open）: {type(e).__name__}", file=sys.stderr)

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
