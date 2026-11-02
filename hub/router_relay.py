"""Router persona 解释层——绑定频道自然语言问答（add-router-persona，spec: hub-mm-intake「自然语言问答」）。

本体红线：Router = **解释者**非调度者（行动权=零——不 enqueue/不 abandon/不改状态）。
与直通互斥分流（一帖绝不双消费）：直通帖（在飞 run 窗口内授权人话）给 dev 上下文，
其余才进 router 候选。预筛全机械（零 LLM 判定调不调）；预算闸防刷屏烧钱。
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# 预筛缺省正则（HUB_ROUTER_PATTERN 可覆盖）：run 引用 #N / 提案 sp-* / 问句关键词
DEFAULT_PATTERN = r"#\d+|sp-[0-9a-f]{4,}|为什么|怎么|多少|状态|blocked|triage|什么问题|啥情况"
DEFAULT_RATE = 6
_ANSWER_TIMEOUT = 60


def _pattern(cfg) -> re.Pattern:
    raw = getattr(cfg, "router_pattern", None) or os.environ.get("HUB_ROUTER_PATTERN") or DEFAULT_PATTERN
    return re.compile(raw, re.IGNORECASE)


def prescreen(message: str, pattern: re.Pattern | None = None) -> bool:
    """机械预筛：命中任一形态才值得调 router（零 LLM 判定）。"""
    if pattern is None:
        pattern = re.compile(DEFAULT_PATTERN, re.IGNORECASE)
    return bool(message.strip()) and bool(pattern.search(message))


def build_context_block(cfg, database, run_ref: str | None = None) -> str:
    """注入块机械拼装（值不入面——凭据/token 绝不进块）。"""
    lines = ["## 值班上下文（机械注入，非对话记忆）"]
    snap = database.queue_snapshot()
    lines.append(f"- 今日：pending {snap['pending']} / running {snap['running']}")
    # 被引用 run 摘要（#N）
    if run_ref:
        try:
            rid = int(run_ref.lstrip("#"))
            for r in database.list_all_runs():
                if r["id"] == rid:
                    lines.append(f"- run #{rid}：{r['status']} / project={r['project']}")
                    dj = Path(r["run_dir"]).parent.parent.parent
                    for df in sorted(dj.glob("dispatch_*.json")):
                        try:
                            import json
                            day = json.loads(df.read_text(encoding="utf-8"))
                            for d in day if isinstance(day, list) else []:
                                if d.get("slug", "").endswith(Path(r["run_dir"]).name.split("_", 1)[1]):
                                    lines.append(f"  详情：{d.get('skip_reason') or d.get('status')} "
                                                 f"/ cost=${d.get('dev_cost', '?')} / turns={d.get('dev_turns', '?')}")
                                    lines.append(f"  详情文件：{df}（可 Read）")
                        except (OSError, ValueError):
                            continue
                    break
        except (ValueError, TypeError):
            pass
    # 影子提案
    sp_count = 0
    for sp_dir in sorted(cfg.data_root.glob("*/shadow_proposals")):
        sp_count += len(list(sp_dir.glob("*.md")))
    if sp_count:
        lines.append(f"- 活跃影子提案：{sp_count} 条")
    return "\n".join(lines)


ROUTER_SYSTEM = (
    "你是 pa-hub 的只读值班解释员。回答关于 run 状态、阻断原因、影子提案、今日计数的问题。\n"
    "纪律：只引事实（注入块是唯一真相源，引用来源）；查不到就明说；"
    "行动建议 = 命令文本（你绝不代执行）；≤200 词中文；开头一行直接答案，证据随后。"
)


def answer_question(cfg, database, question: str, run_ref: str | None = None) -> str | None:
    """直连 API 回答（G1 修复：hub thin 容器无 claude CLI——改 urllib POST Anthropic 协议）。

    模型走 cfg.user_model（vault 派发同源）；base_url/token 走 vault 凭据（与 R3 探活同面）。
    60s 上限；失败/超时 → None（fail-open 静默）。
    """
    import json as _json
    import urllib.error
    import urllib.request
    prompt = f"{build_context_block(cfg, database, run_ref)}\n\n---\n\n用户问题：{question}"
    body = _json.dumps({
        "model": cfg.user_model or "qwen3.8-27b-fast",
        "max_tokens": 500,
        "system": ROUTER_SYSTEM,
        "messages": [{"role": "user", "content": prompt}],
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{cfg.anthropic_base_url.rstrip('/')}/v1/messages",
        data=body,
        headers={"Content-Type": "application/json",
                 "x-api-key": cfg.anthropic_auth_token,
                 "anthropic-version": "2023-06-01"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_ANSWER_TIMEOUT) as resp:
            payload = _json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        print("[router][warn] API 调用超时/失败（静默跳过——fail-open）", file=sys.stderr)
        return None
    for block in (payload.get("content") or []):
        if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
            return block["text"].strip()[:1200]
    print("[router][warn] API 响应无文本块（静默）", file=sys.stderr)
    return None


def relay_router_answers(cfg, database, mm, notifier=None, *,
                         candidates: list[dict] | None = None,
                         rate_counter: dict | None = None) -> int:
    """单轮 router 问答（daemon 与 thread_relay 协作——candidates 来自直通拒帖集）。

    预算闸：每频道每小时至多 HUB_ROUTER_RATE 次（缺省 6——防刷屏烧钱）。
    candidates 形态 [{channel_id, user_id, message}]；bot 自帖由调用方排除。
    """
    if mm is None or notifier is None:
        return 0
    if not os.environ.get("PA_ROUTER_PERSONA", "").strip().lower() in ("1", "true", "yes", "on"):
        return 0  # flag 默认关
    pat = _pattern(cfg)
    rate = rate_counter if rate_counter is not None else {}
    answered = 0
    for c in candidates or []:
        chan, msg = c["channel_id"], c["message"]
        if not prescreen(msg, pat):
            continue
        if rate.get(chan, 0) >= DEFAULT_RATE:
            continue  # 预算闸静默
        m = re.search(r"#(\d+)", msg)
        run_ref = m.group(0) if m else None
        ans = answer_question(cfg, database, msg, run_ref)
        if ans:
            try:
                notifier._post_bound_channel(chan, f"[router] {ans}", "router")
                answered += 1
            except Exception:  # noqa: BLE001
                pass
        rate[chan] = rate.get(chan, 0) + 1
    if answered:
        print(f"[router] 本轮回答 {answered} 问（fail-open 零阻塞主链）", file=sys.stderr)
    return answered
