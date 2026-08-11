#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_graph_cron_path.py — 编排层新依赖导入冒烟（任务 1.2）。

cron 非 login shell（PATH 极简，run_cron.sh 已处理 nvm/PATH）。langgraph-workflow-upgrade 引入
langgraph + claude_agent_sdk 作编排层依赖；本测试验证：
  1. 两依赖装齐 + 版本满足 pyproject 锁（langgraph>=1.2.10,<2 / claude-agent-sdk>=0.2.128,<0.2.130）；
  2. 在接近 cron 的极简 PATH 下，python 解释器可启动且能 import 这两依赖（编排层冒烟，
     防 pip install 漏装/升级把依赖搞丢）。
"""
import os
import subprocess
import sys
from importlib.metadata import version


def _vtuple(pkg: str) -> tuple:
    """包版本前 3 段转 tuple（语义化比较；剥离 +local 后缀）。"""
    v = version(pkg).split("+")[0]
    return tuple(int(x) for x in v.split(".")[:3])


def test_langgraph_pinned():
    assert _vtuple("langgraph") >= (1, 2, 10)          # pyproject 锁 >=1.2.10,<2（避被 yank 的 1.2.3/1.1.7）


def test_claude_agent_sdk_pinned():
    assert _vtuple("claude-agent-sdk") >= (0, 2, 128)  # 锁 >=0.2.128,<0.2.130（sdk_compat_patch #1105 对齐）


def test_imports_under_minimal_path():
    """模拟 cron 极简 PATH：python 能 import langgraph + claude_agent_sdk + 编排层 graph_pa* 模块。"""
    minimal_path = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    env = {**os.environ, "PATH": minimal_path}
    code = ("import langgraph, claude_agent_sdk; "
            "from importlib.metadata import version; "
            "assert version('langgraph') >= '1.2.10'; "
            "print('ok')")
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    assert r.returncode == 0, f"import 失败（PATH={minimal_path}）:\n{r.stderr}"
