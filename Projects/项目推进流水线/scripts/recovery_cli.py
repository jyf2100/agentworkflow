#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""recovery_cli.py — task 7.4 operator journal-corruption recovery CLI（runbook 引用的可执行命令）。

spec scenario「Documented recovery command」（runtime-cutover-evidence）：operator follows the runbook
for a corrupt journal → every referenced command exists in the repository and produces a verifiable
recovery or explicit manual-block result。

薄 CLI 包装 ``cutover.run_journal_recovery``：校验 journal 完整性 → verifiable recovery（重建终态 +
可选 RecoveryContext）或 explicit manual-block（中部损坏 fail-closed，不自动修复）。打印 JSON 结果；
exit code：0=recovered，2=manual_block（运维介入，绝不盲目重放/丢弃）。

用法（见 ``../RUNBOOK.md``）：
    python recovery_cli.py <journal_path> [--prd <prd_path>] [--prd-id <id>] [--iteration <id>]

纯 stdlib + 复用 cutover（cron 隔离友好，不触 SDK）。
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cutover as CT  # noqa: E402


def main(argv=None) -> int:
    """CLI：校验 + 恢复 journal，打印 JSON 证据；recovered → 0，manual_block → 2。"""
    p = argparse.ArgumentParser(
        prog="recovery_cli.py",
        description="operator journal-corruption recovery（verifiable recovery or manual-block）")
    p.add_argument("journal_path", help="journal JSONL 路径（损坏真源）")
    p.add_argument("--prd", default=None, help="可选 PRD 文件路径（提供 → 完整 RecoveryContext）")
    p.add_argument("--prd-id", default="prd_recover", help="recovery_context prd 归属")
    p.add_argument("--iteration", default="iter_recover", help="recovery_context iteration 归属")
    args = p.parse_args(argv)

    prd_content = None
    if args.prd:
        prd_content = Path(args.prd).read_text(encoding="utf-8")

    r = CT.run_journal_recovery(
        journal_path=args.journal_path, prd_content=prd_content,
        iteration_id=args.iteration, prd_id=args.prd_id)

    rc_obj = None
    if r.recovery_context is not None:
        rc_obj = dataclasses.asdict(r.recovery_context)

    print(json.dumps({
        "journal_path": r.journal_path,
        "action": r.action,
        "is_fail_closed": r.report.is_fail_closed,
        "tail_truncated": r.report.tail_truncated,
        "corrupted_line_numbers": list(r.report.corrupted_line_numbers),
        "events_read": r.report.events_read,
        "terminal_status": r.terminal_status,
        "recovery_context": rc_obj,
        "detail": r.detail,
    }, indent=2, ensure_ascii=False))
    return 0 if r.action == "recovered" else 2


if __name__ == "__main__":
    raise SystemExit(main())
