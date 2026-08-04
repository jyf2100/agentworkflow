# -*- coding: utf-8 -*-
"""verify_bundles.py — 大 diff 文件打包（harden-pa-verify-determinism task 3.1/3.2，Requirement B）。

diff 超 threshold → 按相关文件分组（impl + its test + collocated i18n/config 同 stem 同组），
isolated per-bundle context（design D3，divide-and-conquer，借鉴 open-code-review smart file bundling：
"Groups related files into a single review unit... each bundle runs as a sub-agent with isolated
context"）。小 diff → 单 bundle（全部文件，无切分开销，spec scenario B4）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Bundle:
    """一个 review bundle（相关文件组）。reason 记 grouping 依据（可复核）。"""

    files: tuple[str, ...]
    reason: str = ""


_DIFF_FILE = re.compile(r"^\+\+\+ b/(.+)$", re.M)
_DIFF_ADDREM = re.compile(r"^[+-](?![+-])", re.M)        # +/- 内容行（排除 +++/--- header）
_STEM_RE = re.compile(r"\.(py|js|ts|tsx|jsx|json|yaml|yml|properties|md|go|rs|java|rb|php|c|cpp|h)$", re.I)
_TEST_PRE = re.compile(r"^(test_|spec_)", re.I)
_TEST_SUF = re.compile(r"(_test|_spec)$", re.I)
_LOCALE = re.compile(r"[._-](en|zh|zh_cn|zh_tw|ja|ko|de|fr|es|ru)\b", re.I)


def _changed_files(diff_text: str) -> list[str]:
    """``git diff`` → 变更文件列表（``+++ b/<file>``，去重保序）。"""
    if not diff_text:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for m in _DIFF_FILE.finditer(diff_text):
        f = m.group(1).strip()
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


def _diff_line_count(diff_text: str) -> int:
    """``+/-`` 内容行数（排除 ``+++/---`` header）—— bundle 触发的 line 维度。"""
    if not diff_text:
        return 0
    return sum(1 for ln in diff_text.splitlines() if _DIFF_ADDREM.match(ln))


def _stem(path: str) -> str:
    """归一 stem：取 basename → 去扩展名 → 去 test_/spec_ 前缀 → 去 _test/_spec 后缀 → 去 locale 后缀。

    impl / its test / collocated i18n 同 stem → 同组（open-code-review smart bundling 的 related-file 规则）。
    """
    name = path.split("/")[-1]
    stem = _STEM_RE.sub("", name)
    stem = _TEST_PRE.sub("", stem)
    stem = _TEST_SUF.sub("", stem)
    stem = _LOCALE.sub("", stem)
    return stem or name


def _group_related(files: list[str]) -> list[Bundle]:
    """按 stem 分组成 bundles（同 stem 同 bundle，组内文件排序稳定）。"""
    groups: dict[str, list[str]] = {}
    for f in files:
        groups.setdefault(_stem(f), []).append(f)
    return [Bundle(tuple(sorted(g)), f"stem={s}") for s, g in sorted(groups.items())]


def split_bundles(diff_text: str, *, file_threshold: int = 10,
                  line_threshold: int = 800) -> list[Bundle]:
    """大 diff（超 ``file_threshold`` **或** ``line_threshold``）→ 相关文件分组；
    小 diff（两者都未超）→ 单 bundle（全部文件，无切分开销）。空 diff → ``[]``。"""
    files = _changed_files(diff_text)
    if not files:
        return []
    lines = _diff_line_count(diff_text)
    if len(files) <= file_threshold and lines <= line_threshold:
        return [Bundle(tuple(files), "single-bundle（threshold 未超）")]
    return _group_related(files)


__all__ = ["Bundle", "split_bundles"]
