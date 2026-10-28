#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_agent.py — agent-verify-injection 2.1：verify agent 执行器（persona 范畴）。

verify agent 与 persona 同构（headless、``-p`` 单发、编排器 subprocess 链式调、stdout 机器面）
——D12：它是 persona 范畴的新实例而非新调用范畴，差异仅 ``--output-format stream-json``
（vs json 信封）与解析面。实施含义：**base_cmd 参数面经 persona_call 共享**（effort 档 /
per-agent model 路由 equals 形式 / resolve_claude_bin——PA_CLAUDE_BIN 覆盖语义经共享解析
自然继承 D13 容器 ``/usr/local/bin/claude`` 形态），不在本模块手拼第三份；stream-json 解析面
（tool_use/tool_result 配对、exit code 提取、三锚）在 ``verify_anchors`` 独立实现。

2.0 冒烟实证（fixtures/verify_stream_*.slim.jsonl，2026-10-27）：``-p`` 下 stream-json
**必须配 ``--verbose``**（否则 CLI 拒绝）；tool_result 无结构化 exit code 字段——
``is_error=false`` ⇔ exit 0，``is_error=true`` ⇔ content 首行 ``Exit code N``。

超时语义（测试学二审 T4）：会话墙钟超时 raise ``VerifySessionTimeout``——编排层映射
**invalid（非红）**：超时是不可判读不是失败。墙钟分档（D13）：``PA_VERIFY_WALL_CLOCK``
显式覆盖 > hub 容器形态（``/data/pa`` 挂载探测）对齐 dev 会话语义 3600s（容器 npm ci 冷缓存
数分钟——m14 贴爆教训）> 本机形态沿用 VERIFY_*_TIMEOUT 合并量级。
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Protocol

import persona_call   # D12 共享参数面（shared_persona_args / resolve_claude_bin；纯 stdlib 模块）

# 归入 persona 范畴（D12）→ 架构上自然获得 per-agent model 路由能力：
# env PA_PERSONA_MODEL_PA_VERIFY_AGENT / config model-routing.json 的 "pa-verify-agent" 条目。
VERIFY_AGENT_NAME = "pa-verify-agent"

# 墙钟分档（D13）：env 覆盖 > hub 探测 > 本机默认
HUB_WALL_CLOCK = 3600.0     # 容器形态对齐 dev 会话语义（m14 教训：300s 壳装 900s 活）
LOCAL_WALL_CLOCK = 1200.0   # 本机形态 = VERIFY_INSTALL/TEST_TIMEOUT 量级合并
HUB_MARK = "/data/pa"       # hub 容器挂载卷标记（run 容器内必挂）

# maxTurns 分档（run 92 真机，2026-10-28）：hub 容器 30 turn 全花在环境装配（npm ci + 绕
# scope-bash 探查），考卷一条没跑就 max_turns_reached → claude 退出 1。D13 只给墙钟分了档，
# max-turns 漏跟——hub 档对齐 dev 会话 ``max_turns=150``（dev-agent.py:1227）。
HUB_MAX_TURNS = 150
LOCAL_MAX_TURNS = 30

# harness config 换版形态（F2，本体论审核 T2）：这些注入件是「环境对接工件」非考卷本体——
# dev 分支已有同名文件（dev 对目标环境的对接修正）时 verify 不覆盖（store 原件对照记录）。
CONFIG_SWAP_RELS = frozenset({
    "playwright.config.ts", "playwright.config.js", "playwright.config.mjs", "playwright.config.cjs",
    "jest.config.ts", "jest.config.js", "jest.config.mjs", "jest.config.cjs", "jest.config.json",
    "pytest.ini", "vitest.config.ts", "vitest.config.js",
})


class VerifySessionTimeout(Exception):
    """verify agent 会话墙钟超时——不可判读非失败（编排层映射 invalid，T4）。"""


class VerifySessionFailed(RuntimeError):
    """verify agent 会话级失败（claude 非零退出：max-turns 撞墙 / API 断流）——不可判读非
    失败（编排层映射 invalid）。run 92 真机：此形态曾冒泡成「dispatch 异常 status=fail」+
    report 静默全绿（T1 活标本），故立专用异常与 RuntimeError 语义分开。"""


class VerifyExecutor(Protocol):
    """执行器接口：verify 会话 = (worktree, prompt) → 原始 stream-json 文本。

    原始流由编排器负责持久化（2.2 ④：state/runs/ 落盘；hub 容器形态必须命中挂载卷）。"""

    def run(self, cwd: Path, prompt: str, *, timeout: float,
            allowed_tools: tuple[str, ...] = ("Bash", "Read"),
            max_turns: int = 30) -> str: ...


def _is_hub_env() -> bool:
    """hub 容器形态探测（D13）：挂载卷标记目录存在即容器态（run 容器内必挂 /data/pa）。"""
    return Path(HUB_MARK).is_dir()


def resolve_wall_clock() -> float:
    """会话墙钟上限（D13 分档）：PA_VERIFY_WALL_CLOCK 覆盖 > hub 3600s > 本机 1200s。"""
    env = os.environ.get("PA_VERIFY_WALL_CLOCK")
    if env:
        try:
            return float(env)
        except ValueError:
            pass   # 配错回落分档（对齐空串语义：不废整个机制）
    return HUB_WALL_CLOCK if _is_hub_env() else LOCAL_WALL_CLOCK


def resolve_max_turns() -> int:
    """会话 turn 上限分档（run 92 真机补 D13 漏跟）：PA_VERIFY_MAX_TURNS 覆盖 >
    hub 150（对齐 dev 会话）> 本机 30。"""
    env = os.environ.get("PA_VERIFY_MAX_TURNS")
    if env:
        try:
            return int(env)
        except ValueError:
            pass
    return HUB_MAX_TURNS if _is_hub_env() else LOCAL_MAX_TURNS


def declared_suite_cmds_for(harness: str, rels: list[str]) -> list[str]:
    """注入测试 declared 命令的机械构造（2.4：prompt 指令与锚①判定**同一来源**）。

    判定依据必须与给 agent 的指令一字不差——若 prompt 只给文件清单让 agent 自选命令，
    末端段全等匹配（match_declared）的命中率不稳 → invalid 率不可控。故命令在此构造，
    同时喂 build_verify_prompt（指令面）与 match_declared（判定面）。harness 集外（v1
    支持 jest/pytest，1.5 T5 收窄；add-e2e-acceptance-harness 扩 playwright）→ 空（套件
    本不会 stored，defensive）。
    playwright 走**全局 bin 直调**（非 npx）：执行镜像全局装 playwright@钉版 + NODE_PATH=
    /usr/local/lib/node_modules，运行时 clone 的目标仓 node_modules 无 @playwright/test，
    npx 会走 cache 下载（外网）或交互卡死（headless 无 stdin）——全局 resolve 零仓内依赖零网络。
    """
    if harness == "jest":
        return [f"npx jest {r}" for r in rels]
    if harness == "pytest":
        return [f"python3 -m pytest {r}" for r in rels]
    if harness == "playwright":
        return [f"playwright test {r}" for r in rels]
    return []


def build_verify_prompt(env_spec: str, suite_cmds: list[str],
                        gate_cmd: str | None, gate_source: str) -> str:
    """verify 会话 prompt（design 36 行）：env spec + 验收测试命令 + 全量门命令 + 围栏。

    ``suite_cmds`` 是 declared 命令清单（``declared_suite_cmds_for`` 构造）——prompt 要求
    **按原样执行**（末段与 declared 全等才采信，锚①同一性谓词）；可加前置链段（cd / 装依赖），
    但末段命令须逐字一致、不带 ``|`` ``>`` 修饰（管道吞 exit code = 不可判读）。
    围栏：**禁改任何文件**（判定锚②要求 git 工作区净）；装依赖/工具链可（install 属环境面）。
    gate_cmd None = 无全量门（只跑注入测试，显式声明不编造命令——harden-verify-test-scope
    归并 D7：跳过要注明，note 记来源）。
    """
    env_block = f"\n   - 环境变量：`{env_spec}`（装配后再跑测试）" if env_spec else ""
    suite_block = "\n".join(f"   - `{c}`" for c in suite_cmds) or "（无注入验收测试）"
    if gate_cmd:
        gate_block = (f"2. 跑全量门命令：`{gate_cmd}`（来源：{gate_source}）——"
                      "整仓回归面，与注入测试独立采证。")
    else:
        gate_block = ("2. 无全量门命令（来源：none——项目未声明 profile `verify_test_cmd` 且 dev "
                      "未上报）→ **只跑注入验收测试**，在总结里注明未跑全量门。")
    return "\n".join([
        "你是独立验证执行 agent（verify executor）。你的任务是**只执行、不修改**：",
        "",
        "## 硬围栏",
        "- **不要修改、创建、删除任何文件**（代码 / 测试 / 配置都不行）——判定锚要求 git 工作区干净，",
        "  任何写都会让本次验证作废（invalid）。",
        "- 装依赖 / 工具链（如 `npm ci` / `pip install`）**可以**——这属于环境装配非内容修改。",
        "",
        "## 环境纪律（省 turn，一次到位）",
        "- 本仓有 **scope-bash hook**：Bash 只放行白名单前缀（npm/npx/node/git 等只读与包管理面）；",
        "  组合命令（`&&` / `;`）、管道 / 重定向、`VAR=x cmd` env 前缀**会被拒**。被拒不重试同形态，",
        "  直接改单条纯净命令。",
        "- 依赖未预装是常态（worktree 新建无 node_modules）：直接 `npm ci`（或 `pip install -r",
        "  requirements.txt`）——node_modules / venv 是 gitignore 的，**不算弄脏工作区**。",
        "- 全局 CLI 报 `Cannot find package 'xxx'`（ESM 不走 NODE_PATH）时：`npm install --no-save",
        "  xxx` 装进 gitignore 的 node_modules 即可，不改 package.json。",
        "",
        "## 执行步骤",
        "1. 装配环境后跑下列**验收测试**（控制面注入的 PRD 验收点考卷，逐个跑；命令请按给出的",
        "   原样执行——可加前置链段如 `cd` / 装依赖，但末段命令须与上面逐字一致）：",
        suite_block + env_block,
        gate_block,
        "3. 汇报每个命令的完整输出与退出结果（成功失败都原样报，不要重试掩盖）。",
        "",
        "用**纯净命令**跑（不带 `|` `>` 修饰），让退出码可信。",
    ])


class ClaudeHeadlessExecutor:
    """headless claude 会话执行器（stream-json 捕获面；D12 参数面经 persona_call 共享）。"""

    def __init__(self, claude_bin: str | None = None,
                 log=None):
        self.claude_bin = claude_bin or persona_call.resolve_claude_bin()
        self.log = log

    def run(self, cwd: Path, prompt: str, *, timeout: float,
            allowed_tools: tuple[str, ...] = ("Bash", "Read"),
            max_turns: int = 30) -> str:
        cmd = [self.claude_bin, "-p", prompt,
               "--output-format", "stream-json", "--verbose",   # 2.0 冒烟：-p 下必须 --verbose
               "--allowedTools", ",".join(allowed_tools),
               "--max-turns", str(max_turns)]
        # D12 共享参数面（effort + model 路由）——与 persona json 信封面同一份语义
        cmd += persona_call.shared_persona_args(VERIFY_AGENT_NAME, log=self.log)
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=timeout, cwd=str(cwd),
                                  stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired as e:
            raise VerifySessionTimeout(
                f"verify agent 会话墙钟超时（{timeout}s），已 kill（不可判读→invalid）") from e
        if proc.returncode != 0:
            raise VerifySessionFailed(persona_call.exit_diag(VERIFY_AGENT_NAME, proc))
        return proc.stdout or ""
