#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_structural_self_check.py — add-acceptance-preflight-guard P0：import 白名单单测。

背景（run #76 家族）：PRD 验收点写 jsdom，test-author 写出 require('jsdom') 考卷 → 注入 →
dev 烧 60 turns off_track。prompt 级纪律可违反且无人检查 → 作者期机械白名单（design D1/D2）：
  * pytest：AST 提取；放行 sys.stdlib_module_names + 相对导入（level>0），其余拒
  * jest：行首语句形状正则；放行相对/绝对路径 + {jest, @jest/globals}，其余裸包名拒
  * playwright：同上；放行 @playwright/test + 相对/绝对路径
playwright.config.ts 豁免 import 检查（环境对接工件非考卷本体，同 F2 逻辑）。
正反例全覆盖 + 字符串字面量不误判 + detail 带违规 specifier（journal 透传面）。

跑：python3 -m pytest scripts/test_structural_self_check.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import run_daily  # noqa: E402


def _ck(files: dict, harness: str, ids: set[str] | None = None):
    return run_daily._structural_self_check(files, harness, ids or {"demo:ac1"})


# ─── pytest 白名单 ────────────────────────────────────────────────────
def test_pytest_stdlib_and_relative_pass():
    files = {"test_demo.py": "import os\nfrom pathlib import Path\nfrom . import helper\n"
                             "# criterion: demo:ac1\ndef test_x():\n    assert os.sep\n"}
    ok, detail = _ck(files, "pytest")
    assert ok, detail


def test_pytest_third_party_import_rejected():
    files = {"test_demo.py": "import requests\n# criterion: demo:ac1\ndef test_x():\n    pass\n"}
    ok, detail = _ck(files, "pytest")
    assert not ok and "requests" in detail and "白名单" in detail


def test_pytest_repo_absolute_package_rejected():
    # 作者期无仓事实——仓绝对包名同拒（design D2：裸名无法区分仓真有还是幻觉，保守拒）
    files = {"test_demo.py": "import myrepo.core\n# criterion: demo:ac1\ndef test_x():\n    pass\n"}
    ok, detail = _ck(files, "pytest")
    assert not ok and "myrepo.core" in detail


def test_pytest_syntax_error_rejected():
    files = {"test_demo.py": "def broken(:\n# criterion: demo:ac1\n"}
    ok, detail = _ck(files, "pytest")
    assert not ok and ("语法" in detail or "parse" in detail.lower())


# ─── jest 白名单 ──────────────────────────────────────────────────────
def test_jest_relative_and_jest_globals_pass():
    files = {"login.test.js": "import { test, expect } from '@jest/globals';\n"
                              "const h = require('./helper');\n"
                              "// criterion: demo:ac1\ntest('a', () => expect(h).toBeTruthy());\n"}
    ok, detail = _ck(files, "jest")
    assert ok, detail


def test_jest_jsdom_disguise_rejected():
    """run #76 原标本：考卷 require 仓内不存在的 jsdom → 第一发生点即拦。"""
    files = {"login.test.js": "// criterion: demo:ac1\nconst { JSDOM } = require('jsdom');\n"
                              "test('a', () => {});\n"}
    ok, detail = _ck(files, "jest")
    assert not ok and "jsdom" in detail


def test_jest_bare_package_rejected():
    files = {"login.test.js": "// criterion: demo:ac1\nconst _ = require('lodash');\n"
                              "test('a', () => {});\n"}
    ok, detail = _ck(files, "jest")
    assert not ok and "lodash" in detail


# ─── playwright 白名单 ────────────────────────────────────────────────
def test_playwright_allowed_face_pass():
    files = {"e2e_demo.spec.ts": "import { test, expect } from '@playwright/test';\n"
                                 "import { login } from '../helpers/auth';\n"
                                 "// criterion: demo:ac1\ntest('ac1', () => {});\n"}
    ok, detail = _ck(files, "playwright")
    assert ok, detail


def test_playwright_vue_test_utils_rejected():
    files = {"e2e_demo.spec.ts": "// criterion: demo:ac1\nimport '@vue/test-utils';\n"
                                 "test('ac1', () => {});\n"}
    ok, detail = _ck(files, "playwright")
    assert not ok and "@vue/test-utils" in detail


def test_playwright_config_exempt_from_import_whitelist():
    # config 是环境对接工件非考卷本体（F2 同逻辑）：node 内置裸名（path 等）不拦
    files = {"e2e_demo.spec.ts": "import { test } from '@playwright/test';\n"
                                 "// criterion: demo:ac1\ntest('ac1', () => {});\n",
             "playwright.config.ts": "import path from 'path';\nimport { defineConfig } from '@playwright/test';\n"
                                     "export default defineConfig({ webServer: { command: 'npm run dev', port: 4180 } });\n"}
    ok, detail = _ck(files, "playwright")
    assert ok, detail


# ─── 误判面 ───────────────────────────────────────────────────────────
def test_string_literal_require_not_flagged():
    # 字符串字面量里的 require 不匹配（行首语句形状锚定）——防注释/文案误判
    files = {"login.test.js": "// criterion: demo:ac1\n"
                              "const doc = \"run require('jsdom') in docs\";\n"
                              "test('a', () => expect(doc).toContain('require'));\n"}
    ok, detail = _ck(files, "jest")
    assert ok, detail


def test_violation_detail_carries_specifier_for_journal():
    # 1.3：违规 specifier 进 detail（record_absence → journal payload 透传，MM/排查可见）
    files = {"test_demo.py": "import requests\n# criterion: demo:ac1\ndef test_x():\n    pass\n"}
    ok, detail = _ck(files, "pytest")
    assert not ok and "test_demo.py" in detail and "requests" in detail
