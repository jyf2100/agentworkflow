# -*- coding: utf-8 -*-
"""verify_anchors.py — pa-verify 反馈的机械行级锚点（harden-pa-verify-determinism task 1.1）。

把 independent_verify 落的 .testout（jest / pytest / node-test 红输出）机械解析成结构化锚点
``(test_name, failing_file, failing_line)``。解析不上 → ``unresolved``（fail-open，不伪造——同
fail-safe-dispatch 的三态 ``UNKNOWN`` 姿态，design D2）。

这是 verify 闭环的「确定性半边」（design D1）：编排器机械算锚点喂 pa-verify，persona 只在
「为什么红 / 怎么改」发挥。scenario 契约见 ``verified-dev-execution`` delta Requirement A。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

AnchorStatus = Literal["ok", "unresolved", "base-side"]

# pytest：FAILED 行（-v）含 file::name；FAILURES section header 是 ___ name ___；traceback file:line:
_PYTEST_FAILED = re.compile(r"^FAILED\s+(\S+?)::(\S+?)\s+-", re.M)
_PYTEST_SECTION = re.compile(r"^_{2,}\s+(\S+)\s+_{2,}\s*$", re.M)
_PYTEST_TRACEBACK = re.compile(r"^(\S+?\.py):(\d+):", re.M)
# jest：● test name + at ... (file:line:col)
_JEST_TEST = re.compile(r"^\s*●\s+(.+?)\s*$", re.M)
_JEST_AT = re.compile(r"at\s+\S.*?\((\S+?):(\d+):\d+\)")
# node:test (TAP)：not ok N - name + file:/line:
_NODETEST_NOTOK = re.compile(r"^not ok\s+\d+\s+-\s+(.+?)\s*$", re.M)
_NODETEST_FILE = re.compile(r"^\s*file:\s+(\S+)\s*$", re.M)
_NODETEST_LINE = re.compile(r"^\s*line:\s+(\d+)\s*$", re.M)


@dataclass(frozen=True)
class Anchor:
    """一条失败测试的机械锚点。

    status:
      - ``ok``：解析成功，file/line 已锚定（hunk 映射在 task 1.2 接入）；
      - ``unresolved``：解析失败 / runner 不支持 / testout 空——只填 reason，绝不伪造（design D2）；
      - ``base-side``：round≥2 红断言行落在 base（不在增量 diff）——task 1.2 接入时区分 unresolved。
    """

    test_name: str
    status: AnchorStatus
    file: str | None = None
    line: int | None = None
    hunk: str | None = None
    reason: str | None = None


def detect_runner(test_cmd: str | None, testout: str = "") -> str:
    """按 ``test_cmd`` 推断 runner；``npm test``（launcher）/未知时从 testout 内容兜底推。

    返回 ``"pytest" | "jest" | "node-test" | "unknown"``。
    """
    cmd = (test_cmd or "").strip().lower()
    if "pytest" in cmd:
        return "pytest"
    if "jest" in cmd:
        return "jest"
    if "node --test" in cmd or "node:test" in cmd:
        return "node-test"
    if "npm test" in cmd or not cmd:
        t = testout or ""
        if "TAP version" in t or re.search(r"^not ok", t, re.M):
            return "node-test"
        if "FAIL ./" in t or re.search(r"^●", t, re.M):
            return "jest"
        if re.search(r"FAILED|::.*AssertionError|\.py:\d+:", t):
            return "pytest"
        return "unknown"
    return "unknown"


def parse_failing_anchors(testout: str, runner: str) -> list[Anchor]:
    """从 ``.testout`` 文本解析失败测试锚点。多失败测试全部锚定（design OQ / task 1.1）。

    testout 空 / runner 不支持 → 单条 ``unresolved``（fail-open）。
    """
    if not testout or not testout.strip():
        return [Anchor(test_name="(empty)", status="unresolved", reason="testout 为空")]
    if runner == "pytest":
        return _parse_pytest(testout)
    if runner == "jest":
        return _parse_jest(testout)
    if runner == "node-test":
        return _parse_nodetest(testout)
    return [Anchor(test_name="(unknown-runner)", status="unresolved",
                   reason=f"unsupported runner: {runner}")]


def _tb_index_by_file(tracebacks: list[tuple[str, int]]) -> dict[str, int]:
    """``traceback [(file, line)]`` → ``{file: line}``（首个匹配，保序去重）。"""
    out: dict[str, int] = {}
    for f, ln in tracebacks:
        out.setdefault(f, ln)
    return out


def _parse_pytest(testout: str) -> list[Anchor]:
    failed = _PYTEST_FAILED.findall(testout)          # [(file, name)]
    sections = _PYTEST_SECTION.findall(testout)       # [name]
    tracebacks = [(f, int(ln)) for f, ln in _PYTEST_TRACEBACK.findall(testout)]
    by_file = _tb_index_by_file(tracebacks)

    if failed:
        # FAILED 行模式：每条 (file, name) 配 traceback 的 file→line
        return [Anchor(test_name=name, status="ok", file=f, line=by_file.get(f))
                for f, name in failed]
    if sections and tracebacks:
        # FAILURES section 模式：section name[i] 配 traceback[i]
        return [Anchor(test_name=n, status="ok", file=f, line=ln)
                for n, (f, ln) in zip(sections, tracebacks)]
    if tracebacks:
        # 仅有 traceback，无 name：用文件名作 test_name 占位
        return [Anchor(test_name=f, status="ok", file=f, line=ln) for f, ln in tracebacks]
    return [Anchor(test_name="(unparseable-pytest)", status="unresolved",
                   reason="pytest 输出无 FAILED 行 / FAILURES section / traceback file:line")]


def _parse_jest(testout: str) -> list[Anchor]:
    tests = _JEST_TEST.findall(testout)               # [name]
    locs = _JEST_AT.findall(testout)                  # [(file, line)]
    if tests and locs:
        return [Anchor(test_name=n, status="ok", file=f, line=int(ln))
                for n, (f, ln) in zip(tests, locs)]
    if locs:
        return [Anchor(test_name=f, status="ok", file=f, line=int(ln)) for f, ln in locs]
    return [Anchor(test_name="(unparseable-jest)", status="unresolved",
                   reason="jest 输出无 ● test / at (file:line:col)")]


def _parse_nodetest(testout: str) -> list[Anchor]:
    notoks = _NODETEST_NOTOK.findall(testout)         # [name]
    files = _NODETEST_FILE.findall(testout)           # [file]
    lines = _NODETEST_LINE.findall(testout)           # [line str]
    if notoks and files and lines:
        # 简化：单失败块（多失败块需按 YAML 块切分，留将来增强——design D2 fail-open 兜底）
        return [Anchor(test_name=notoks[0], status="ok", file=files[0], line=int(lines[0]))]
    if notoks:
        return [Anchor(test_name=n, status="ok") for n in notoks]
    return [Anchor(test_name="(unparseable-nodetest)", status="unresolved",
                   reason="node:test 输出无 not ok / file / line")]


def _parse_diff_hunks(diff_text: str) -> dict[str, list[tuple[int, int, str]]]:
    """``git diff`` → ``{file: [(new_start, new_count, hunk_header), ...]}``。

    file 取 ``+++ b/<file>``（目标面文件名）；hunk 起始/计数取 ``@@ -a,b +c,d @@`` 的 ``c,d``（d 省略=1）。
    hunk_text 暂存 header 行（足够判覆盖 + pa-verify 引用；完整上下文按需扩展）。
    """
    if not diff_text:
        return {}
    out: dict[str, list[tuple[int, int, str]]] = {}
    cur_file: str | None = None
    for line in diff_text.splitlines():
        mf = re.match(r"^\+\+\+ b/(.+)$", line)
        if mf:
            cur_file = mf.group(1).strip()
            continue
        mh = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
        if mh and cur_file:
            start = int(mh.group(1))
            count = int(mh.group(2)) if mh.group(2) else 1
            out.setdefault(cur_file, []).append((start, count, line))
    return out


def map_anchor_to_hunk(anchor: Anchor, diff_text: str) -> Anchor:
    """把 ``ok`` anchor 映射到 diff hunk（task 1.2）：

    - line 落某 hunk ``[start, start+count-1]`` → ``status=ok`` + ``hunk``（header）；
    - file 在 diff 但 line 不落 hunk / file 不在 diff → ``base-side``（round≥2「改 A 断 B」信号，
      design D2 区分 ``unresolved``）；
    - 已 ``unresolved`` → 原样返回（不 map）。
    """
    if anchor.status != "ok":
        return anchor
    if anchor.line is None:
        return Anchor(test_name=anchor.test_name, status="unresolved",
                      file=anchor.file, reason="anchor 缺 line，无法映射 hunk")
    file_hunks = _parse_diff_hunks(diff_text).get(anchor.file or "", [])
    for start, count, htext in file_hunks:
        if start <= anchor.line < start + count:
            return Anchor(test_name=anchor.test_name, status="ok",
                          file=anchor.file, line=anchor.line, hunk=htext)
    return Anchor(test_name=anchor.test_name, status="base-side",
                  file=anchor.file, line=anchor.line,
                  reason=f"{anchor.file}:{anchor.line} 不在本轮增量 diff hunk（base-side 回归）")


def derive_anchors(testout: str, test_cmd: str | None, diff_text: str) -> list[Anchor]:
    """dispatch 接入点（task 1.1 wire / 1.4 round）：``testout + test_cmd + diff`` → 带状态/hunk 的 anchors。

    round≥2 的 base-side 由 :func:`map_anchor_to_hunk` 标记（``base-side`` vs ``unresolved``）。
    """
    runner = detect_runner(test_cmd, testout)
    raw = parse_failing_anchors(testout, runner)
    return [map_anchor_to_hunk(a, diff_text) for a in raw]


__all__ = ["Anchor", "AnchorStatus", "detect_runner", "parse_failing_anchors",
           "map_anchor_to_hunk", "derive_anchors"]
