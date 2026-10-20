#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_release_gates.py — release 门禁扩展（add-executor-image tasks 2.1/2.2）。

两组纯函数（design D8 接缝：pytest 直测；release_template.sh 只消费退出码）：

  ① check_dockerfile_static(path) — Dockerfile 四项静态检查
     （spec: template-release delta「发布门禁」）：
     存在 / FROM 显式 tag（非 latest）/ USER 非 root / 无凭据字面量模式。
     完整 build 验证属 build 脚手职责（executor-image），release 只做静态完备性。

  ② untracked_under_manifest(vault_root, manifest_lines) — 工作树 vs ls-files 差集
     （F12 闭环：新文件未 stage → manifest 经 git ls-files 展开静默漏出投影；
     v0.1.2 曾因此漏发新测试文件）。gitignore 天然排除（--exclude-standard）。

CLI（release_template.sh 接线点）：
  python3 check_release_gates.py --dockerfile <path>
      四项检查；违规 → stderr 列出并 exit 2（阻断）
  python3 check_release_gates.py --untracked-diff <manifest> [--vault <root>]
      差集阻断；非空 exit 2（本体论审核 F2 裁决 warning→block：纳管目录下
      「未 stage 未 ignore」几乎必是遗漏——故意不发该 gitignore，warning
      形态下 v0.1.2 漏发场景可重演，E2E 实证）

退出码：0 全过 | 2 静态检查违规或差集非空 | 3 用法错误
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

# 凭据字面量模式（spec: executor-image「凭据零入镜像」+ template-release 门禁共享）。
# 前缀 + 最低熵长；sk-ant- 先于裸 sk- 匹配（防前缀互吃）。增删须过 change 评审。
CREDENTIAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ghp_", re.compile(r"ghp_[A-Za-z0-9]{20,}")),
    ("github_pat_", re.compile(r"github_pat_[A-Za-z0-9_]{20,}")),
    ("sk-ant-", re.compile(r"sk-ant-[A-Za-z0-9._-]{8,}")),
    ("sk-", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("AKIA", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("xox", re.compile(r"xox[bpars]-[A-Za-z0-9-]{10,}")),
)

_FROM_RE = re.compile(r"^\s*FROM\s+(?:--platform=\S+\s+)?(\S+)", re.MULTILINE)
_USER_RE = re.compile(r"^\s*USER\s+(\S+)", re.MULTILINE)


def check_dockerfile_static(dockerfile: Path) -> list[str]:
    """四项静态检查，返回违规清单（空 = 过）。中文消息直通 release 输出。"""
    violations: list[str] = []
    if not dockerfile.is_file():
        return [f"Dockerfile 不存在：{dockerfile}（spec: template-release——投影根必须有 Dockerfile）"]
    text = dockerfile.read_text(encoding="utf-8")

    # ② FROM 显式 tag（scratch / digest 形式合法）
    froms = _FROM_RE.findall(text)
    if not froms:
        violations.append("无 FROM 指令")
    for ref in froms:
        if ref == "scratch" or "@" in ref:
            continue
        tag = ref.rsplit(":", 1)[-1] if ":" in ref else ""
        if not tag:
            violations.append(f"FROM 缺显式 tag：{ref}")
        elif tag == "latest":
            violations.append(f"FROM 使用 latest（须钉显式 tag）：{ref}")

    # ③ USER 非 root（镜像级非 root 基线，spec: executor-image「隔离基线」）
    users = _USER_RE.findall(text)
    if not users:
        violations.append("无 USER 指令（默认 root 运行——须声明非 root 用户）")
    for u in users:
        if u in ("root", "0"):
            violations.append(f"USER 声明为 root/0：{u}")

    # ④ 无凭据字面量（与 executor-image build 自检共享模式集）
    for prefix, pat in CREDENTIAL_PATTERNS:
        m = pat.search(text)
        if m:
            violations.append(f"凭据字面量命中 [{prefix}…]：{m.group(0)[:12]}…")

    return violations


def untracked_under_manifest(vault_root: Path, manifest_lines: list[str]) -> list[str]:
    """manifest 覆盖范围内「未跟踪且未忽略」的文件 = 将静默漏出投影者（F12）。

    经 ``git ls-files --others --exclude-standard`` —— gitignore 排除与 ls-files
    展开（release_template.sh [1] 段）语义天然对齐。
    """
    out: list[str] = []
    for line in manifest_lines:
        spec = line.strip()
        if not spec or spec.startswith("#"):
            continue
        proc = subprocess.run(
            ["git", "-C", str(vault_root), "-c", "core.quotepath=off",
             "ls-files", "--others", "--exclude-standard", "--", spec],
            check=True, capture_output=True, text=True,
        )
        out.extend(l for l in proc.stdout.splitlines() if l)
    return sorted(out)


def main(argv: list[str] | None = None) -> int:
    here = Path(__file__).resolve().parent
    vault_default = here.parent.parent.parent  # release/ → pa → vault
    ap = argparse.ArgumentParser(description="release 门禁扩展（Dockerfile 静态检查 + F12 差集告警）")
    ap.add_argument("--dockerfile", type=Path, help="副本根 Dockerfile 路径（四项静态检查）")
    ap.add_argument("--untracked-diff", type=Path, metavar="MANIFEST",
                    help="F12 差集告警：manifest 清单内未跟踪文件")
    ap.add_argument("--vault", type=Path, default=vault_default, help="vault 根（差集告警用）")
    args = ap.parse_args(argv)
    if not args.dockerfile and not args.untracked_diff:
        ap.error("须给 --dockerfile 或 --untracked-diff 之一")

    rc = 0
    if args.dockerfile:
        violations = check_dockerfile_static(args.dockerfile)
        if violations:
            rc = 2
            print("✗ [executor-image] Dockerfile 静态检查未过：", file=sys.stderr)
            for v in violations:
                print(f"  - {v}", file=sys.stderr)
        else:
            print("✓ [executor-image] Dockerfile 四项静态检查过（存在/tag/非root/无凭据）")

    if args.untracked_diff:
        lines = args.untracked_diff.read_text(encoding="utf-8").splitlines()
        untracked = untracked_under_manifest(args.vault, lines)
        if untracked:
            # F12 阻断（本体论审核 F2 裁决 warning→block）：manifest 纳管范围内
            # 「未跟踪未忽略」文件 = 将静默漏出投影（v0.1.2 教训）——先 git add 或移出纳管范围
            rc = 2
            print("✗ [F12] manifest 覆盖范围内存在未跟踪文件（将静默漏出投影）——先 git add 或移出纳管范围：",
                  file=sys.stderr)
            for f in untracked:
                print(f"  - {f}", file=sys.stderr)
        else:
            print("✓ [F12] 工作树与 ls-files 无差集")
    return rc


if __name__ == "__main__":
    sys.exit(main())
