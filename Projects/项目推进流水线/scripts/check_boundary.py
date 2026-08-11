#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_boundary.py — 机械/语义边界 lint（langgraph-workflow-upgrade 任务 2.4，D3/R6）。

静态扫 graph_pa*.py 源码，守住两条 design D3/R6 不变式（运行时另有 commit_node 守，双保险）：

  规则 verdict_boundary：非 PersonaNode 工厂（make_mechanical_node / make_gateway_node /
    make_devloop_node）的函数体内出现 verdict 标识符 → 违规。语义判决（pass/revise/drop 等）只由
    PersonaNode 产出（其背后是 claude 对抗 persona）；机械/dev/gateway node 写 verdict = 边界滑落。

  规则 no_bare_path：TypedDict（NodeInput/NodeOutput 等）出现裸 `<*path>: str` 字段（非 rel_path）
    → 违规。文件路径须用 ArtifactHandle（{kind, store, rel_path, digest, must_exist}，D4）。

CLI：python check_boundary.py [file/dir...] → exit 0=干净 / 1=有违规（可接 quality.sh）。
无参默认扫 scripts/graph_pa*.py。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import NamedTuple


class Violation(NamedTuple):
    file: str
    line: int
    rule: str
    msg: str


# verdict 仅 PersonaNode 可写——这 3 个工厂函数体内绝不该出现 verdict 标识符
NON_VERDICT_FACTORIES = ("make_mechanical_node", "make_gateway_node", "make_devloop_node")

# no_bare_path 只扫 node I/O 契约 + artifact handle（路径契约层，D4）；
# state TypedDict（GraphState/CriticSubState）的 path 字段是运行期状态传递，非 artifact 契约，不扫。
CONTRACT_TYPES = ("NodeInput", "NodeOutput", "ArtifactHandle")


def _verdict_refs(node: ast.AST) -> list[int]:
    """node 子树内所有 verdict 标识符引用的行号（Name/常量字典 key/keyword/Subscript 下标）。"""
    refs: list[int] = []
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and n.id == "verdict":
            refs.append(n.lineno)
        elif isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value == "verdict":
            refs.append(n.lineno)
        elif isinstance(n, ast.keyword) and n.arg == "verdict":
            refs.append(n.lineno)
        elif (isinstance(n, ast.Subscript) and isinstance(n.value, ast.Constant)
              and n.value.value == "verdict"):
            refs.append(n.lineno)
    return refs


def check_verdict_boundary(tree: ast.Module, filename: str) -> list[Violation]:
    """非 PersonaNode 工厂函数体内出现 verdict → 违规（make_persona_node 内 verdict 允许，不扫）。"""
    vs: list[Violation] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in NON_VERDICT_FACTORIES:
            for line in _verdict_refs(node):
                vs.append(Violation(filename, line, "verdict_boundary",
                                    f"{node.name} 工厂体内出现 verdict（仅 PersonaNode 可写语义判决，D3/R6）"))
    return vs


def check_no_bare_path(tree: ast.Module, filename: str) -> list[Violation]:
    """契约 TypedDict（NodeInput/NodeOutput/ArtifactHandle）裸 <*path>: str → 违规（D4）。
    state TypedDict 的 path 字段是运行期状态传递，非 artifact 契约，不扫。"""
    vs: list[Violation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name not in CONTRACT_TYPES:
            continue
        for stmt in node.body:
            if not (isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)):
                continue
            fname = stmt.target.id
            if "path" not in fname.lower() or fname == "rel_path":
                continue                       # rel_path 是 ArtifactHandle 合法字段
            ann = stmt.annotation
            if isinstance(ann, ast.Name) and ann.id == "str":
                vs.append(Violation(filename, stmt.lineno, "no_bare_path",
                                    f"{node.name}.{fname}: str 是裸路径——应用 ArtifactHandle（D4）"))
    return vs


def check_source(src: str, filename: str = "<test>") -> list[Violation]:
    """校验源码字符串（供测试 + check_file 复用）。语法错误 → 单条 violation（不静默跳过）。"""
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return [Violation(filename, e.lineno or 0, "parse_error", f"语法错误: {e.msg}")]
    return check_verdict_boundary(tree, filename) + check_no_bare_path(tree, filename)


def check_file(path: str) -> list[Violation]:
    return check_source(Path(path).read_text(encoding="utf-8"), path)


def _default_targets() -> list[str]:
    """默认扫 scripts/graph_pa*.py（守编排层边界；不含 test_*）。"""
    root = Path(__file__).resolve().parent
    return sorted(str(p) for p in root.glob("graph_pa*.py"))


def main(argv: list[str]) -> int:
    args = argv[1:]
    if args:
        targets: list[str] = []
        for a in args:
            p = Path(a)
            targets += [str(x) for x in (p.rglob("graph_pa*.py") if p.is_dir() else [p])]
    else:
        targets = _default_targets()
    if not targets:
        return 0
    all_vs: list[Violation] = []
    for t in targets:
        all_vs += check_file(t)
    for v in all_vs:
        print(f"{v.file}:{v.line}: [{v.rule}] {v.msg}")
    return 1 if all_vs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
