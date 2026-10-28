#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_e2e_harness.py — add-e2e-acceptance-harness task 4.1：harness=playwright 全链单测。

覆盖面（离线 fixture，零 npm/网络）：
- 前置拦截：playwright 缺 e2e_dev_cmd → refused 登记（不烧 persona 额度、不产出半套件）；
- 集外值回归：mocha 等仍未拦（no_harness），jest/pytest 面零变化；
- `_test_author_prompt` playwright 分支：E2E 专则全文（webServer/port/retries/chromium/
  NODE_PATH import）+ jest 分支零污染；
- `_e2e_port_for`：固定段 4173-4199 + 确定性；
- `_structural_self_check` playwright：形态/sleep/无 webServer 三拒 + 合法套件过；
- `declared_suite_cmds_for`：全局 bin 直调 `playwright test`（非 npx）；
- `detect_runner` 映射 + testout 兜底（两形态：带/不带 [browser] 前缀）；
- 锚解析（**playwright 1.64.1 锁版实测输出** fixture）：双失败 retry 形态两锚、
  project 显式声明 `[chromium]` 形态、flaky 块不混入（计绿语义）、混合 failed+flaky 块界定。

跑：python3 -m pytest scripts/test_e2e_harness.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import run_daily                          # noqa: E402
import verify_agent as VA                 # noqa: E402
from verify_anchors import detect_runner, parse_failing_anchors  # noqa: E402


# ─── playwright 1.64.1 实测输出 fixture（锁版；格式变更时以镜像锁版重实测校准）──────
# 双失败 + retries=1（无显式 project → 头行无 [browser] 段）
PW_RED_NO_PROJECT = """\
Running 2 tests using 1 worker

  ✘  1 e2e_theme.spec.ts:3:5 › criterion: demo:ac1 主题切换后 body 属性变更 (5.1s)
  ✘  2 e2e_theme.spec.ts:3:5 › criterion: demo:ac1 主题切换后 body 属性变更 (retry #1) (5.1s)

  1) e2e_theme.spec.ts:3:5 › criterion: demo:ac1 主题切换后 body 属性变更 ───────────

    Error: expect(locator).toHaveAttribute(expected) failed

      3 | test('criterion: demo:ac1 主题切换后 body 属性变更', async ({ page }) => {
    > 5 |   await expect(page.locator('body')).toHaveAttribute('data-theme', 'dark');
        at /tmp/x/e2e_theme.spec.ts:5:38

  2 failed
    e2e_theme.spec.ts:3:5 › criterion: demo:ac1 主题切换后 body 属性变更 ──────────────
    e2e_theme.spec.ts:8:5 › criterion: demo:ac2 标题渲染 ─────────────────────────────
"""

# 显式 projects: chromium（spec D3 要求形态 → 头行带 [chromium] 段）
PW_RED_PROJECT = """\
Running 1 test using 1 worker

  ✘  1 [chromium] › e2e_proj.spec.ts:2:5 › criterion: demo:ac4 项目显式声明形态 (5.1s)

  1) [chromium] › e2e_proj.spec.ts:2:5 › criterion: demo:ac4 项目显式声明形态 ─────────

  1 failed
    [chromium] › e2e_proj.spec.ts:2:5 › criterion: demo:ac4 项目显式声明形态 ───────────
"""

# flaky（首红次绿 → exit 0 计绿）：详情块存在但尾汇总只有 flaky 块，无 failed 块
PW_FLAKY_ONLY = """\
Running 1 test using 1 worker

  ✘  1 e2e_flaky.spec.ts:4:5 › criterion: demo:ac3 flaky 首红次绿 (3ms)
  ✓  2 e2e_flaky.spec.ts:4:5 › criterion: demo:ac3 flaky 首红次绿 (retry #1) (5ms)

  1) e2e_flaky.spec.ts:4:5 › criterion: demo:ac3 flaky 首红次绿 ──────────────────────

    Error: first run red

  1 flaky
    e2e_flaky.spec.ts:4:5 › criterion: demo:ac3 flaky 首红次绿 ───────────────────────
"""

# 混合：1 failed + 1 flaky（failed 块在前 flaky 块在后——块界定回归）
PW_FAILED_AND_FLAKY = """\
  1 failed
    e2e_a.spec.ts:10:5 › criterion: p:ac1 失败测试 ──────────────────────────────────
  1 flaky
    e2e_b.spec.ts:20:5 › criterion: p:ac2 flaky 测试 ────────────────────────────────
"""


# ─── 前置拦截（_author_acceptance_suite 前半，不触 persona）───────────────────
def _prd_with_acs(tmp_path: Path) -> Path:
    p = tmp_path / "demo_prd.md"
    p.write_text("# PRD\n\n正文。\n\n## 验收标准\n\n- body data-theme 切换为 dark\n- 标题渲染正确\n",
                 encoding="utf-8")
    return p


def test_missing_e2e_dev_cmd_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path)
    entry = {"project": "demo", "prd_path": str(_prd_with_acs(tmp_path)), "verdict": "pass"}
    prof = {"harness": "playwright"}   # 缺 e2e_dev_cmd
    res = run_daily._author_acceptance_suite(entry, prof, "20261028")
    assert res["status"] == "absent" and res["reason"] == "refused"
    assert "e2e_dev_cmd" in res["detail"]          # 回执指引补声明


def test_out_of_set_harness_still_gated(tmp_path, monkeypatch):
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path)
    entry = {"project": "demo", "prd_path": str(_prd_with_acs(tmp_path)), "verdict": "pass"}
    res = run_daily._author_acceptance_suite(entry, {"harness": "mocha"}, "20261028")
    assert res["reason"] == "no_harness" and "playwright" in res["detail"]   # 支持集文案更新


def test_playwright_declared_full_track_to_store(tmp_path, monkeypatch):
    """声明齐（playwright + e2e_dev_cmd）→ 放行到 persona → 自检过 → stored（全链 mock）。"""
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path)
    prd = _prd_with_acs(tmp_path)
    out_dir = tmp_path / "acceptance" / "s" / "demo_demo_prd"
    out_dir.mkdir(parents=True)
    # 稳定 id 前缀 = dev_slugify(slug)——slug 是 PRD 文件名 stem「demo_prd」→「demo-prd」
    spec = _GOOD_SPEC.replace("demo:ac1", "demo-prd:ac1")
    (out_dir / "e2e_theme.spec.ts").write_text(spec, encoding="utf-8")
    (out_dir / "playwright.config.ts").write_text(_GOOD_CONFIG, encoding="utf-8")

    def fake_persona(name, prompt, tag, label, allowed_tools=None):
        assert name == "pa-test-author" and "playwright" in prompt
        return ({"files": [{"path": str(out_dir / "e2e_theme.spec.ts"), "criteria_ids": ["demo-prd:ac1"]},
                           {"path": str(out_dir / "playwright.config.ts"), "criteria_ids": []}],
                 "uncovered": [], "refused": None}, {"cost": 0.0})
    monkeypatch.setattr(run_daily, "run_persona", fake_persona)
    entry = {"project": "demo", "prd_path": str(prd), "verdict": "pass"}
    prof = {"harness": "playwright", "e2e_dev_cmd": "npm run dev"}
    res = run_daily._author_acceptance_suite(entry, prof, "20261028x")
    assert res["status"] == "stored", res


# ─── prompt 构建面 ─────────────────────────────────────────────────────────────
def test_prompt_playwright_branch_rules():
    acs = [{"id": "demo:ac1", "text": "body data-theme 切换为 dark"}]
    prof = {"tech_stack": "node", "e2e_dev_cmd": "npm run dev"}
    prompt = run_daily._test_author_prompt("p/x.md", acs, "playwright", Path("/tmp/o"), prof)
    assert "playwright.config.ts" in prompt                     # config 是 authored file 之一
    assert "npm run dev" in prompt                              # webServer command 来自 profile
    assert str(run_daily._e2e_port_for("p/x.md")) in prompt     # 机械分配端口写进 prompt
    assert "reuseExistingServer" in prompt and "retries: 1" in prompt
    assert "chromium" in prompt and "@playwright/test" in prompt
    assert "page.waitForTimeout" in prompt                      # 禁令显式出现


def test_prompt_jest_branch_zero_pollution():
    acs = [{"id": "demo:ac1", "text": "x"}]
    prompt = run_daily._test_author_prompt("p/x.md", acs, "jest", Path("/tmp/o"), {"tech_stack": "js"})
    assert "playwright" not in prompt and "webServer" not in prompt


def test_e2e_port_range_and_determinism():
    for p in ("a.md", "b.md", "深路径/中文.md", "x" * 300):
        port = run_daily._e2e_port_for(p)
        assert 4173 <= port <= 4199
    assert run_daily._e2e_port_for("a.md") == run_daily._e2e_port_for("a.md")


# ─── 结构自检（_structural_self_check playwright 分支）─────────────────────────
_GOOD_SPEC = "import { test, expect } from '@playwright/test';\n// criterion: demo:ac1\ntest('ac1', () => {});\n"
_GOOD_CONFIG = ("import { defineConfig } from '@playwright/test';\n"
                "export default defineConfig({ retries: 1, webServer: { command: 'npm run dev', port: 4180 } });\n")


def test_self_check_playwright_good_suite():
    files = {"e2e_theme.spec.ts": _GOOD_SPEC, "playwright.config.ts": _GOOD_CONFIG}
    ok, detail = run_daily._structural_self_check(files, "playwright", {"demo:ac1"})
    assert ok, detail


def test_self_check_playwright_bad_shape():
    files = {"smoke.test.ts": _GOOD_SPEC, "playwright.config.ts": _GOOD_CONFIG}
    ok, detail = run_daily._structural_self_check(files, "playwright", {"demo:ac1"})
    assert not ok and "e2e_" in detail


def test_self_check_playwright_sleep_rejected():
    files = {"e2e_theme.spec.ts": _GOOD_SPEC + "page.waitForTimeout(500);\n",
             "playwright.config.ts": _GOOD_CONFIG}
    ok, detail = run_daily._structural_self_check(files, "playwright", {"demo:ac1"})
    assert not ok and "waitForTimeout" in detail


def test_self_check_playwright_config_no_webserver():
    files = {"e2e_theme.spec.ts": _GOOD_SPEC,
             "playwright.config.ts": "export default defineConfig({ retries: 1 });\n"}
    ok, detail = run_daily._structural_self_check(files, "playwright", {"demo:ac1"})
    assert not ok and "webServer" in detail


def test_self_check_jest_regression_unchanged():
    ok, detail = run_daily._structural_self_check({"a.test.js": "// criterion: d:ac1"}, "jest", {"d:ac1"})
    assert ok, detail


# ─── 命令映射（全局 bin 直调，非 npx）───────────────────────────────────────────
def test_declared_cmds_playwright_global_bin():
    cmds = VA.declared_suite_cmds_for("playwright", ["e2e_theme.spec.ts", "e2e_ac2.spec.ts"])
    assert cmds == ["playwright test e2e_theme.spec.ts", "playwright test e2e_ac2.spec.ts"]
    assert all("npx" not in c for c in cmds)


def test_declared_cmds_jest_pytest_unchanged():
    assert VA.declared_suite_cmds_for("jest", ["a.test.js"]) == ["npx jest a.test.js"]
    assert VA.declared_suite_cmds_for("pytest", ["test_a.py"]) == ["python3 -m pytest test_a.py"]
    assert VA.declared_suite_cmds_for("mocha", ["x"]) == []


# ─── runner 检测 ───────────────────────────────────────────────────────────────
def test_detect_runner_by_cmd():
    assert detect_runner("playwright test e2e_theme.spec.ts") == "playwright"
    assert detect_runner("npx playwright test e2e_x.spec.ts") == "playwright"


def test_detect_runner_testout_fallback_both_shapes():
    assert detect_runner("npm test", PW_RED_NO_PROJECT) == "playwright"
    assert detect_runner("npm test", PW_RED_PROJECT) == "playwright"
    assert detect_runner(None, "") == "unknown"   # 兜底回归：空仍 unknown


# ─── 锚解析（1.64.1 实测 fixture）──────────────────────────────────────────────
def test_parse_anchors_no_project_shape():
    anchors = parse_failing_anchors(PW_RED_NO_PROJECT, "playwright")
    assert [(a.test_name, a.file, a.line, a.status) for a in anchors] == [
        ("criterion: demo:ac1 主题切换后 body 属性变更", "e2e_theme.spec.ts", 3, "ok"),
        ("criterion: demo:ac2 标题渲染", "e2e_theme.spec.ts", 8, "ok"),
    ]


def test_parse_anchors_project_shape():
    anchors = parse_failing_anchors(PW_RED_PROJECT, "playwright")
    assert len(anchors) == 1
    a = anchors[0]
    assert a.file == "e2e_proj.spec.ts" and a.line == 2 and a.status == "ok"
    assert "criterion: demo:ac4" in a.test_name and "─" not in a.test_name   # 尾划线剥除


def test_parse_flaky_only_yields_no_fake_anchors():
    anchors = parse_failing_anchors(PW_FLAKY_ONLY, "playwright")
    assert all(a.status == "unresolved" for a in anchors)   # flaky 计绿：无 failed 块 → 不伪造锚


def test_parse_failed_and_flaky_block_boundary():
    anchors = parse_failing_anchors(PW_FAILED_AND_FLAKY, "playwright")
    assert len(anchors) == 1                                 # flaky 块行不混入
    assert anchors[0].file == "e2e_a.spec.ts" and anchors[0].line == 10
