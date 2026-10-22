#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_deliver_prd.py — add-hub-slice-3 task 3.1：vault 投递器三态语义（D3/D4）。

投递器职责（spec: prd-delivery「投递器与 delivered 标记」「投递三态与崩溃窗口」）：
扫描 = PRD 产出集 − ledger 终态集；2xx 才写 delivered 行；UNKNOWN（超时/连接断/
5xx）不写标记、下轮重扫重投，同一 PRD 连续 UNKNOWN 超 K=5 转 unknown_exhausted
死信 + 告警；确定性 4xx 写 failed 死信行带 error_class（data_4xx / config_4xx），
config_4xx 高声告警非零退出；ledger append-only 半行损坏跳过；串行逐个。

函数级直测（对齐 test_run_checkpoint.py 模式）：HTTP 经 monkeypatch urlopen 打桩。
跑：cd Projects/项目推进流水线 && python -m pytest scripts/test_deliver_prd.py -q
"""
from __future__ import annotations

import io
import json
import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import deliver_prd  # noqa: E402

PRD_A = ("proj-a", "20261022_slug-a.md", "# PRD A\n内容甲")
PRD_B = ("proj-b", "20261022_slug-b.md", "# PRD B\n内容乙")


def _sha(s: str) -> str:
    import hashlib
    return hashlib.sha256(s.encode()).hexdigest()


def _mk_prd(state: Path, project: str, filename: str, content: str) -> Path:
    d = state / "prd" / project
    d.mkdir(parents=True, exist_ok=True)
    p = d / filename
    p.write_text(content, encoding="utf-8")
    return p


def _mk_state(tmp_path: Path) -> Path:
    state = tmp_path / "state"
    state.mkdir(parents=True)  # tmp_path 可能是未建的子目录（exit-code 测试传 tmp/"a"|tmp/"b"）
    return state


def _row(status: str, prd=PRD_A, **kw):
    base = {"status": status, "project": prd[0], "filename": prd[1], "sha256": _sha(prd[2])}
    base.update(kw)
    return base


# ── 扫描：产出集 − ledger 终态集 ────────────────────────────────────

def test_scan_pending_set_minus_delivered(tmp_path):
    state = _mk_state(tmp_path)
    _mk_prd(state, *PRD_A); _mk_prd(state, *PRD_B)
    deliver_prd.append_row(state / deliver_prd.LEDGER_NAME, _row("delivered", delivered_at="t"))
    pending = deliver_prd.scan_pending(state)
    assert [(p["project"], p["filename"]) for p in pending] == [("proj-b", PRD_B[1])]


def test_scan_failed_skipped_unless_retry(tmp_path):
    state = _mk_state(tmp_path)
    _mk_prd(state, *PRD_A)
    deliver_prd.append_row(state / deliver_prd.LEDGER_NAME, _row("failed", error_class="data_4xx"))
    assert deliver_prd.scan_pending(state) == []  # 死信不再自动重投
    assert len(deliver_prd.scan_pending(state, retry=True)) == 1  # --retry 显式重投


def test_unknown_rows_are_not_terminal(tmp_path):
    """UNKNOWN 绝不当成功处理：unknown 行不算 delivered，PRD 仍待投（下轮重扫重投）。"""
    state = _mk_state(tmp_path)
    _mk_prd(state, *PRD_A)
    deliver_prd.append_row(state / deliver_prd.LEDGER_NAME, _row("unknown"))
    deliver_prd.append_row(state / deliver_prd.LEDGER_NAME, _row("unknown"))
    assert len(deliver_prd.scan_pending(state)) == 1


def test_ledger_half_line_corruption_skipped(tmp_path):
    """append-only jsonl 半行损坏（崩溃窗口②残留）可跳过——该 PRD 视为未投递重投。"""
    state = _mk_state(tmp_path)
    _mk_prd(state, *PRD_A)
    ledger = state / deliver_prd.LEDGER_NAME
    ledger.write_text(
        json.dumps(_row("delivered", delivered_at="t"), ensure_ascii=False)[:-8],  # 崩溃留下的半行
        encoding="utf-8",
    )
    rows = deliver_prd.load_ledger(ledger)
    assert rows == []  # 损坏行跳过不炸
    assert len(deliver_prd.scan_pending(state)) == 1  # 视为未投递


# ── 三态分类（D4）──────────────────────────────────────────────────

def _http_error(code: int):
    return urllib.error.HTTPError("url", code, "reason", io.StringIO(), io.StringIO())


def test_deliver_one_classifies_three_states(monkeypatch):
    calls = {}

    def fake_urlopen(req, timeout=None):
        calls["url"] = req.full_url
        if fake_urlopen.mode == "ok":
            class R:
                status = 201
                def read(self):
                    return b'{"id": 7}'
                def __enter__(self):
                    return self
                def __exit__(self, *a):
                    return False
            return R()
        if fake_urlopen.mode == "timeout":
            raise TimeoutError("timed out")
        if fake_urlopen.mode == "conn":
            raise OSError("connection reset")
        raise _http_error(fake_urlopen.mode)

    monkeypatch.setattr(deliver_prd.urllib.request, "urlopen", fake_urlopen)
    prd = {"project": PRD_A[0], "filename": PRD_A[1], "content": PRD_A[2], "sha256": _sha(PRD_A[2]), "path": "/x"}
    for mode, expect_state, expect_class in [
        ("ok", "delivered", None),
        ("timeout", "unknown", None),          # 超时：发了请求但结果不可判
        ("conn", "unknown", None),             # 连接断：响应未收到
        (500, "unknown", None),                # 5xx：不可判（handler bug 也可能已写库）
        (503, "unknown", None),
        (400, "rejected", "data_4xx"),         # 坏数据（sha 不符/超限/格式坏）
        (413, "rejected", "data_4xx"),
        (401, "rejected", "config_4xx"),       # token 失配——部署凭据错，高声告警
        (403, "rejected", "config_4xx"),
        (404, "rejected", "config_4xx"),       # hub 未升级
        (405, "rejected", "config_4xx"),
    ]:
        fake_urlopen.mode = mode
        out = deliver_prd.deliver_one("http://hub:8000", prd, token="sekrit", timeout=5)
        assert out["state"] == expect_state and out["error_class"] == expect_class, (mode, out)
    assert calls["url"] == "http://hub:8000/deliver"


# ── K=5 unknown_exhausted（防无界重投且零持久痕迹）─────────────────

def test_unknown_streak_counts_tail_consecutive_rows(tmp_path):
    state = _mk_state(tmp_path)
    ledger = state / deliver_prd.LEDGER_NAME
    for _ in range(3):
        deliver_prd.append_row(ledger, _row("unknown"))
    deliver_prd.append_row(ledger, _row("delivered", delivered_at="t"))  # 打断连续性
    deliver_prd.append_row(ledger, _row("unknown"))
    key = (PRD_A[0], PRD_A[1], _sha(PRD_A[2]))
    assert deliver_prd.unknown_streak(deliver_prd.load_ledger(ledger), *key) == 1  # 只数尾部连续


def test_unknown_exhausted_after_k_consecutive(tmp_path, monkeypatch):
    """同一 PRD 连续 UNKNOWN 超 K=5 → 转 unknown_exhausted 死信行 + 告警，不再投。"""
    state = _mk_state(tmp_path)
    _mk_prd(state, *PRD_A)
    ledger = state / deliver_prd.LEDGER_NAME
    for _ in range(deliver_prd.DEFAULT_UNKNOWN_LIMIT):
        deliver_prd.append_row(ledger, _row("unknown"))  # 已连续 5 次
    alarms: list[str] = []
    monkeypatch.setattr(deliver_prd, "alarm", lambda msg: alarms.append(msg))
    posted: list = []
    monkeypatch.setattr(
        deliver_prd, "deliver_one",
        lambda *a, **k: posted.append(1) or {"state": "delivered", "error_class": None, "detail": ""},
    )
    rc = deliver_prd.main(["--state-dir", str(state), "--hub-url", "http://hub:8000"])
    assert rc == 1  # 死信发生 → 非零退出
    assert posted == []  # 没有真投——streak 已达上限直接转死信
    rows = deliver_prd.load_ledger(ledger)
    assert rows[-1]["status"] == "failed" and rows[-1]["error_class"] == "unknown_exhausted"
    assert alarms and "unknown_exhausted" in alarms[0]


# ── main 编排：退出码与 ledger 写面 ─────────────────────────────────

def test_main_delivers_and_writes_ledger(tmp_path, monkeypatch):
    state = _mk_state(tmp_path)
    _mk_prd(state, *PRD_A)
    monkeypatch.setattr(
        deliver_prd, "deliver_one",
        lambda *a, **k: {"state": "delivered", "error_class": None, "detail": ""},
    )
    rc = deliver_prd.main(["--state-dir", str(state), "--hub-url", "http://hub:8000"])
    assert rc == 0
    rows = deliver_prd.load_ledger(state / deliver_prd.LEDGER_NAME)
    assert [r["status"] for r in rows] == ["delivered"]
    # 已 delivered：重跑不再投（幂等，spec scenario「重扫不重投」）
    rc2 = deliver_prd.main(["--state-dir", str(state), "--hub-url", "http://hub:8000"])
    assert rc2 == 0 and len(deliver_prd.load_ledger(state / deliver_prd.LEDGER_NAME)) == 1


def test_main_exit_codes_deadletter_vs_config(tmp_path, monkeypatch):
    alarms: list[str] = []
    monkeypatch.setattr(deliver_prd, "alarm", lambda msg: alarms.append(msg))

    # data_4xx → 死信退出 1
    state = _mk_state(tmp_path / "a")
    _mk_prd(state, *PRD_A)
    monkeypatch.setattr(
        deliver_prd, "deliver_one",
        lambda *a, **k: {"state": "rejected", "error_class": "data_4xx", "detail": "sha 不符"},
    )
    assert deliver_prd.main(["--state-dir", str(state), "--hub-url", "http://h"]) == 1

    # config_4xx → 高声告警 + 退出 2（token 失配/hub 未升级一眼可辨）
    state2 = _mk_state(tmp_path / "b")
    _mk_prd(state2, *PRD_A)
    monkeypatch.setattr(
        deliver_prd, "deliver_one",
        lambda *a, **k: {"state": "rejected", "error_class": "config_4xx", "detail": "401"},
    )
    assert deliver_prd.main(["--state-dir", str(state2), "--hub-url", "http://h"]) == 2
    assert any("config_4xx" in m for m in alarms)  # 高声告警
    rows = deliver_prd.load_ledger(state2 / deliver_prd.LEDGER_NAME)
    assert rows[-1]["status"] == "failed" and rows[-1]["error_class"] == "config_4xx"


def test_main_unknown_writes_no_terminal_row(tmp_path, monkeypatch):
    """UNKNOWN 不写终态行（下轮重扫重投收敛）；本轮退出 0（非终态非告警）。"""
    state = _mk_state(tmp_path)
    _mk_prd(state, *PRD_A)
    monkeypatch.setattr(
        deliver_prd, "deliver_one",
        lambda *a, **k: {"state": "unknown", "error_class": None, "detail": "timeout"},
    )
    rc = deliver_prd.main(["--state-dir", str(state), "--hub-url", "http://h"])
    assert rc == 0
    rows = deliver_prd.load_ledger(state / deliver_prd.LEDGER_NAME)
    assert [r["status"] for r in rows] == ["unknown"]  # 仅计数行，无 delivered/failed
