#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""model_routing.py — per-agent 模型路由配置解析（独立文件，零依赖）。

主配置源 = ``config/model-routing.json``（独立于 ~/.claude/settings.json 与认证 env，
解耦——日常配置不经认证注入函数 _load_claude_settings_env）。env ``PA_PERSONA_MODEL_<AGENT>``
/ ``PA_DEV_MODEL`` 保留作 canary 覆盖（消费点处 env > 文件 > roc 默认）。

零依赖（stdlib only）——供 persona_call（dev 内循环，守 SDK 隔离 invariant：import 本模块
不得连带加载 claude_agent_sdk）与 run_daily 共享。路径经本模块 __file__ 自定位
（parents[1] = 项目推进流水线，不依赖 cwd）。

文件格式（JSON，纯 JSON 无注释——json.loads 遇注释失败降级 roc 默认）::

    {
      "pa-progress": "haiku",
      "pa-radar": "haiku",
      "pa-prd-critic": "sonnet",
      "pa-verify": "sonnet",
      "dev": "sonnet"
    }

key = persona agent_name（如 pa-progress）或 "dev"；value = roc fast alias
（haiku=glm-5.1 / sonnet|opus|fable=glm-5.2[1M]）或裸 glm-*。裸 Anthropic id 被 roc 拒。
文件不存在 / 空 / key 缺 / value 非字符串/null/空串 → None（走 roc 默认 glm-5.2，零变更 baseline）。

降级契约（review 2026-08-09）：_load 对**任意**损坏输入（缺文件/IO 错/语法错/深嵌套 RecursionError
/超大文件/非 object/value 非字符串）MUST 降级到 roc 默认，**不 raise**——消费点（run_persona/
_dev_cmd/persona_call）裸调用无 try/except 兜底，raise 会穿透致整晚 pipeline abort。
配错（非 object/未知 key）经 _log.warning 反馈（symmetry：语法错已 warn；主配置通道对 typo 不静默）。

API：
    resolve_persona_model(agent_name, path=None) -> str | None
    resolve_dev_model(path=None) -> str | None
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

_DEFAULT_PATH = Path(__file__).resolve().parents[1] / "config" / "model-routing.json"
# 防 MemoryError：config 本就几行，> cap 视为损坏降级（review security MED-1）。
_MAX_BYTES = 65536
# 配错反馈（review silent-failure H-2）：warn 未知 key（typo/大小写，如 pa-progres / DEV）。
# 新增 persona / config-key（如未来 reflection 入 config）时同步更新此处。
_KNOWN_KEYS = frozenset({
    "pa-fetch-deepresearch", "pa-fetch-wechat-url", "pa-fetch-github-repo",
    "pa-radar", "pa-prd", "pa-prd-critic", "pa-verify", "pa-progress", "dev",
})
_log = logging.getLogger(__name__)


def _load(path: Path | None = None) -> dict[str, object]:
    """读 model-routing.json → dict；任意损坏 → {}（降级 roc 默认，不 raise；配错 warn）。"""
    p = Path(path) if path else _DEFAULT_PATH
    if not p.exists():
        return {}
    try:
        raw = p.read_text(encoding="utf-8")
        if len(raw) > _MAX_BYTES:
            _log.warning("model-routing: %s 过大（%d 字节 > %d cap），降级 roc 默认", p, len(raw), _MAX_BYTES)
            return {}
        data = json.loads(raw)
    except (OSError, ValueError, RecursionError) as e:
        # RecursionError：深嵌套 JSON（实测 [×10000 触发）不被 ValueError 覆盖，须显式捕（守「不 raise」契约）。
        _log.warning("model-routing: 读取 %s 失败（%s），降级 roc 默认", p, e)
        return {}
    if not isinstance(data, dict):
        _log.warning("model-routing: %s 顶层非 object（%r），降级 roc 默认", p, type(data).__name__)
        return {}
    _unexpected = sorted(set(data) - _KNOWN_KEYS)
    if _unexpected:
        _log.warning(
            "model-routing: %s 含未知 key %r（已知 %d 个；typo/大小写？），这些 key 被忽略",
            p, _unexpected, len(_KNOWN_KEYS),
        )
    return data


def _coerce(value: object) -> str | None:
    """value 归一：非字符串/空串/None → None（守 str|None 契约；review python MED + security LOW-2）。

    非 dict 容器已在 _load 拦；此处守 value 层——非字符串真值（int/bool/object）不穿透成
    ``--model=123`` / ``--model=True``，统一降级 roc 默认。
    """
    return value if isinstance(value, str) and value else None


def resolve_persona_model(agent_name: str, path: Path | None = None) -> str | None:
    """读独立文件，返回该 persona 的 model；缺/非字符串/null/空串 → None（roc 默认）。"""
    return _coerce(_load(path).get(agent_name))


def resolve_dev_model(path: Path | None = None) -> str | None:
    """读独立文件，返回 dev loop 的 model（key="dev"）；缺/非字符串/null/空串 → None。"""
    return _coerce(_load(path).get("dev"))
