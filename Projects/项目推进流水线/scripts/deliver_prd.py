#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""deliver_prd.py — vault→hub PRD 投递器（add-hub-slice-3，F11 state ownership 交接）。

独立脚本独立退出码，run_cron.sh 在 run_daily --to-stage prd 之后串行调用（D5：
先编排后投递）。三态语义（spec: prd-delivery「投递三态与崩溃窗口」，fail-safe
三态不变式推广到投递响应）：

  * 2xx 回执       → 写 ledger delivered 行（唯一标记写面）；
  * UNKNOWN        → 超时/连接断/未收响应/5xx——绝不写终态行，下轮重扫重投；
                     同一 PRD 连续 UNKNOWN 达 K=5 → 转 unknown_exhausted 死信 + 告警
                     （防确定性 5xx 无界重投且零持久痕迹——无静默失败）；
  * 确定性 4xx     → 写 failed 死信行带 error_class（data_4xx 坏数据 / config_4xx
                     部署凭据配错——token 失配、hub 未升级批量死信须一眼可辨），
                     不再自动重投；--retry 显式重投。

崩溃窗口全靠「vault 重扫重投 + hub db/文件双层幂等」收敛 exactly-once（零协调
机制）。ledger = <state_dir>/deliver_ledger.jsonl append-only，半行损坏跳过
（该 PRD 视为未投递重投，hub 幂等去重兜底）。

用法：python3 deliver_prd.py [--state-dir DIR] (--hub-url URL | env PA_HUB_URL)
                            [--retry] [--timeout S] [--unknown-limit K]
凭据：env PA_DELIVER_TOKEN（可选，配则 Bearer 注入）。
退出码：0=无死信；1=有死信（data_4xx / unknown_exhausted）；2=config_4xx（部署
凭据错，高声）；3=hub 地址缺失等部署配置错。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

LEDGER_NAME = "deliver_ledger.jsonl"
DEFAULT_UNKNOWN_LIMIT = 5  # K：同一 PRD 连续 UNKNOWN 达此数转 unknown_exhausted 死信
DEFAULT_TIMEOUT_S = 60.0

VAULT_ROOT = Path(__file__).resolve().parents[3]  # 与 run_daily 同法（须从 vault 根跑）

# 确定性 4xx 中属部署/凭据错的（D4：token 失配、无权限、hub 未升级、方法不对）
_CONFIG_4XX = frozenset({401, 403, 404, 405})


def alarm(msg: str) -> None:
    """高声告警（stderr [ALARM] 前缀——与常规 log 一眼可辨）。"""
    print(f"[ALARM] {msg}", file=sys.stderr)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# ── ledger（append-only，唯一标记写面）──────────────────────────────

def append_row(ledger_path: Path, row: dict) -> None:
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()


def load_ledger(ledger_path: Path) -> list[dict]:
    """全量读；半行/坏行跳过（append-only 崩溃残留——该行视为未发生）。"""
    if not ledger_path.is_file():
        return []
    rows: list[dict] = []
    for line in ledger_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _rows_for(rows: list[dict], project: str, filename: str, sha: str) -> list[dict]:
    return [r for r in rows if (r.get("project"), r.get("filename"), r.get("sha256")) == (project, filename, sha)]


def terminal_status(rows: list[dict], project: str, filename: str, sha: str, *, retry: bool) -> str | None:
    """从尾部往前第一个终态行（delivered/failed）；retry 模式只认 delivered。

    failed 后若追加过 unknown 行（--retry 尝试中），默认轮仍视为 failed——retry
    是显式单次行为，常规轮不追投。
    """
    for r in reversed(_rows_for(rows, project, filename, sha)):
        status = r.get("status")
        if status == "delivered":
            return "delivered"
        if status == "failed" and not retry:
            return "failed"
        if status in ("delivered", "failed", "unknown") and status != "unknown":
            continue
        if status == "unknown":
            continue  # unknown 行非终态，继续往前找
    return None


def unknown_streak(rows: list[dict], project: str, filename: str, sha: str) -> int:
    """尾部连续 unknown 行数（遇任何非 unknown 行即断——delivered/failed 打断计数）。"""
    n = 0
    for r in reversed(_rows_for(rows, project, filename, sha)):
        if r.get("status") == "unknown":
            n += 1
        else:
            break
    return n


# ── 扫描：PRD 产出集 − ledger 终态集 ────────────────────────────────

def scan_pending(state_dir: Path, *, retry: bool = False) -> list[dict]:
    """state/prd/<project>/<stamp>_<slug>.md 全集 − 已终态集（重扫幂等：
    delivered 永跳过；failed 默认跳过、--retry 复投）。串行逐个由调用方保证。"""
    ledger = load_ledger(state_dir / LEDGER_NAME)
    prd_root = state_dir / "prd"
    pending: list[dict] = []
    if not prd_root.is_dir():
        return pending
    for md in sorted(prd_root.glob("*/*.md")):
        project, filename = md.parent.name, md.name
        content = md.read_text(encoding="utf-8")
        sha = sha256_text(content)
        if terminal_status(ledger, project, filename, sha, retry=retry) is not None:
            continue
        pending.append({"project": project, "filename": filename, "path": str(md), "content": content, "sha256": sha})
    return pending


# ── 投递（三态）────────────────────────────────────────────────────

def deliver_one(hub_url: str, prd: dict, *, token: str | None = None, timeout: float = DEFAULT_TIMEOUT_S) -> dict:
    """POST hub /deliver 一次并三态分类。UNKNOWN 绝不当成功——崩溃窗口②③⑥
    由下轮重扫重投 + hub 幂等收敛，本函数不重试（每轮每 PRD 恰投一次）。"""
    body = json.dumps({
        "project": prd["project"], "filename": prd["filename"],
        "sha256": prd["sha256"], "content": prd["content"],
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        hub_url.rstrip("/") + "/deliver", data=body, method="POST",
        headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if 200 <= resp.status < 300:
                return {"state": "delivered", "error_class": None, "detail": ""}
            return {"state": "unknown", "error_class": None, "detail": f"非预期状态码 {resp.status}"}
    except urllib.error.HTTPError as e:
        if e.code >= 500:
            return {"state": "unknown", "error_class": None, "detail": f"HTTP {e.code}（5xx 不可判）"}
        cls = "config_4xx" if e.code in _CONFIG_4XX else "data_4xx"
        return {"state": "rejected", "error_class": cls, "detail": f"HTTP {e.code}"}
    except (TimeoutError, OSError, urllib.error.URLError) as e:
        # 超时/连接断/未收响应：请求可能已达 hub——绝不当成功，也不写终态
        return {"state": "unknown", "error_class": None, "detail": f"{type(e).__name__}: {e}"}


# ── main ───────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    import os

    p = argparse.ArgumentParser(description="vault→hub PRD 投递器（add-hub-slice-3，F11）")
    p.add_argument("--state-dir", default=str(VAULT_ROOT / ".project-auto" / "state"))
    p.add_argument("--hub-url", default=os.environ.get("PA_HUB_URL", ""),
                   help="hub 根地址（默认 env PA_HUB_URL）")
    p.add_argument("--retry", action="store_true", help="failed 死信也复投（人工修复后显式重投）")
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    p.add_argument("--unknown-limit", type=int, default=DEFAULT_UNKNOWN_LIMIT, help="K：连续 UNKNOWN 上限")
    args = p.parse_args(argv)

    hub_url = args.hub_url.strip()
    if not hub_url:
        print("✗ 缺 hub 地址：--hub-url 或 env PA_HUB_URL（不静默跳过投递）", file=sys.stderr)
        return 3
    token = os.environ.get("PA_DELIVER_TOKEN") or None

    state_dir = Path(args.state_dir).resolve()
    ledger_path = state_dir / LEDGER_NAME
    pending = scan_pending(state_dir, retry=args.retry)
    if not pending:
        print(f"deliver_prd：无待投 PRD（state_dir={state_dir}）")
        return 0

    print(f"deliver_prd：待投 {len(pending)} 份 → {hub_url}（串行）")
    had_dead = had_config = False
    for prd in pending:
        key = (prd["project"], prd["filename"], prd["sha256"])
        if unknown_streak(load_ledger(ledger_path), *key) >= args.unknown_limit:
            append_row(ledger_path, {
                "status": "failed", "error_class": "unknown_exhausted",
                "project": key[0], "filename": key[1], "sha256": key[2],
                "failed_at": _now(), "detail": f"连续 UNKNOWN 达 {args.unknown_limit} 次——转死信防无界重投",
            })
            alarm(f"unknown_exhausted：{key[0]}/{key[1]} 连续 UNKNOWN 达 {args.unknown_limit} 次——"
                  f"转死信（排查 hub 5xx/网络后 --retry 重投）")
            had_dead = True
            continue

        out = deliver_one(hub_url, prd, token=token, timeout=args.timeout)
        base = {"project": key[0], "filename": key[1], "sha256": key[2]}
        if out["state"] == "delivered":
            append_row(ledger_path, {"status": "delivered", **base, "delivered_at": _now()})
            print(f"  ✓ delivered {key[0]}/{key[1]}")
        elif out["state"] == "unknown":
            append_row(ledger_path, {"status": "unknown", **base, "at": _now(), "detail": out["detail"]})
            print(f"  ? unknown  {key[0]}/{key[1]}（{out['detail']}）——不标记，下轮重投")
        else:  # rejected（确定性 4xx 死信）
            append_row(ledger_path, {
                "status": "failed", "error_class": out["error_class"], **base,
                "failed_at": _now(), "detail": out["detail"],
            })
            if out["error_class"] == "config_4xx":
                had_config = True
                alarm(f"config_4xx：{key[0]}/{key[1]} 被拒（{out['detail']}）——部署/凭据错"
                      f"（PA_DELIVER_TOKEN 失配或 hub 未升级？），修后 --retry 重投")
            else:
                had_dead = True
                print(f"  ✗ data_4xx {key[0]}/{key[1]}（{out['detail']}）——坏数据死信，修后 --retry")

    if had_config:
        return 2
    return 1 if had_dead else 0


if __name__ == "__main__":
    sys.exit(main())
