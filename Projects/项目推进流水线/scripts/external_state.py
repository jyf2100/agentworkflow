"""external_state.py — 三态外部查询结果 + 诊断脱敏（OpenSpec fail-safe-dispatch / 本变更 tasks 4.1）。

把 dispatch 段对 GitHub/Git 远程态的查询从「失败返默认（容忍）」升级为三态：

    FOUND     查询成功且对象确定存在——带载荷（保护态 bool / 在途 PR 数 int / 分支名 / PR dict / commit sha）
    NOT_FOUND 查询成功且对象确定不存在（如分支保护 404=未保护、远端无匹配 auto/* 分支、无在途 PR）
    UNKNOWN   查询本身失败（超时 / 非零退出 / 缺凭证 / 坏 JSON）——状态不明，fail-safe

设计依据 design.md「三态外部态」决策：旧 ``count_inflight_prs``/``already_dispatched`` 失败返 0/False
（fail-open，可能超额投递或重复投递）；新模型要求「无法证明安全」一律 **不 dispatch**（fail-safe），
并把已脱敏的诊断上下文带到 state 记录与报告（tasks 5.1/5.2）。

脱敏：``reason`` 只留命令名/退出码/超时/截断 stderr，绝不带 token/密钥/Bearer/basic-auth——可安全落盘与上报。

纯逻辑零依赖模块（同 ``evidence``/``slug_utils`` 既定模式）：单测可零 SDK 导入锁定三态分支与脱敏。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, ClassVar


class ExtState(str, Enum):
    """外部查询三态（str 子类化便于 JSON 序列化进 state 记录）。"""
    FOUND = "found"
    NOT_FOUND = "not_found"
    UNKNOWN = "unknown"          # fail-safe 信号：dispatch 准入见之即阻断


# ── 诊断脱敏：抹 token/密钥/Bearer/basic-auth，留命令名/退出码/超时/截断 stderr ──
_TOKEN_RE = re.compile(
    r"(gh[pousr]_[A-Za-z0-9]{16,}"            # GitHub PAT：ghp_/gho_/ghu_/ghs_/ghr_
    r"|Bearer\s+[A-Za-z0-9._\-]+"              # OAuth Bearer
    r"|token[=:]\s*[A-Za-z0-9._\-]+"           # token=... / token:...
    r"|https?://[A-Za-z0-9._\-]+:[A-Za-z0-9._\-]+@)"  # basic-auth URL  user:pass@
)
_REDACTED = "***"


def sanitize(text: str | None, limit: int = 120) -> str:
    """脱敏诊断文本：抹 token/密钥/Bearer/basic-auth，换行压空格，截断到 ``limit``。None/空→''。"""
    if not text:
        return ""
    cleaned = _TOKEN_RE.sub(_REDACTED, text).replace("\n", " ").strip()
    return cleaned[:limit]


@dataclass(frozen=True)
class ExtResult:
    """三态外部查询结果。

    ``state=UNKNOWN`` 是 fail-safe 信号：dispatch 准入见之即记 ``blocked_external_state`` 不起 dev loop
    （tasks 4.3）；reconcile 见之即保留分支、不创建/删除/覆盖 PR（tasks 4.4）。
    ``reason`` 已脱敏，可安全落 state 记录与报告。构造请走 ``found``/``not_found``/``unknown`` 工厂
    （自动脱敏 reason）；直接构造 ExtResult 视为已脱敏。
    """
    __test__: ClassVar[bool] = False   # 显式声明非测试类，免 pytest 收集告警（与 evidence.TestEngine 一致）

    state: ExtState
    value: Any = None          # FOUND 载荷（保护 bool / 在途 PR 数 / 分支名 / PR dict / commit sha）；NOT_FOUND/UNKNOWN 通常 None
    reason: str = ""           # 已脱敏诊断上下文

    @property
    def is_unknown(self) -> bool:
        """fail-safe 判定：状态不明。准入/reconcile 见 True 即保守阻断。"""
        return self.state is ExtState.UNKNOWN

    @property
    def is_decidable(self) -> bool:
        """查询可决断（FOUND 或 NOT_FOUND）——非 UNKNOWN。准入仅放行「可决断且安全」的组合。"""
        return self.state is not ExtState.UNKNOWN


def found(value: Any = None, reason: str = "") -> ExtResult:
    """查询成功且对象存在：带载荷 ``value``（可省略，纯存在性查询时留 None）。"""
    return ExtResult(ExtState.FOUND, value, sanitize(reason))


def not_found(reason: str = "") -> ExtResult:
    """查询成功且对象确定不存在（如分支保护 404=未保护、远端无匹配 auto/* 分支、无在途 PR）。"""
    return ExtResult(ExtState.NOT_FOUND, None, sanitize(reason))


def unknown(reason: str = "") -> ExtResult:
    """查询本身失败（超时/非零/缺凭证/坏 JSON）——状态不明，fail-safe。``reason`` 自动脱敏。"""
    return ExtResult(ExtState.UNKNOWN, None, sanitize(reason))
