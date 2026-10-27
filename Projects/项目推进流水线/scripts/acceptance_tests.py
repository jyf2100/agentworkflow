"""acceptance_tests.py — 验收测试套件工件链（OpenSpec agent-verify-injection tasks 1.1/1.9）。

验收测试是**控制面工件**：PRD 验收点（稳定 id）→ pa-test-author persona 外部编写 →
content-addressed 存储 → 双注入（dev 目标 / verify 门）→ digest 对账。本模块是纯机械段：

    * ``store_suite`` —— 确定性 bundle（files 按路径排序 + 逐文件 digest）→ ``artifact_store``
      落盘（kind=``acceptance_suite``）+ journal 事件 ``acceptance_tests``（digest/path/
      criteria_ids/uncovered/supersedes/iteration_id）。同文件集同 digest（content-addressed 幂等，
      crash 重放不双份）。
    * 覆盖闭合（测试学二审 T1）—— PRD 全部验收点 id vs 套件声明 ids，缺者显式 ``uncovered``
      进事件（记录不扣留：orphan criterion 靠红门卡，不靠静默丢弃）。
    * 缺席三分显式（no_criteria / no_harness / self_check_failed）—— 不产空测试套件，
      ``record_absence`` 同事件面记 reason+detail（fail-open 不拦 dispatch）。
    * 自检扣留（T2）—— harness 支持收集级校验的（pytest ``--collect-only`` 类）入注入面前
      由调用方跑（``self_check`` 回调注入，本模块不触 subprocess），失败 → 扣留记缺席。
    * supersedes 版本链（T2 + tasks 1.9）—— oracle 缺陷重编写存新 digest，事件带
      ``supersedes=<旧digest>`` + ``iteration_id`` 绑定；新版本只对下一次 dispatch 生效。

依赖 ``artifact_store`` + ``loop_state`` + 标准库；journal 经调用方注入的 ``sj``
（StructuredJournal duck-typed：``.enabled`` / ``.emit``），本模块不直接触文件系统 journal。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

import artifact_store
from artifact_store import compute_digest

# 自检回调：() -> (ok, detail)。ok=False → 套件扣留（收集级失败签名，T2）。
SelfCheck = Callable[[], "tuple[bool, str]"]

BUNDLE_SCHEMA_VERSION = 1


def _bundle_payload(files: dict[str, str], suite_criteria_ids: list[str],
                    harness: str) -> dict:
    """确定性 bundle payload：files 按路径排序内嵌 content + 逐文件 digest（锚③ 复核面）。"""
    return {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "harness": harness,
        "criteria_ids": list(suite_criteria_ids),
        "files": [
            {"path": p, "content": files[p], "digest": compute_digest(files[p].encode("utf-8"))}
            for p in sorted(files)
        ],
    }


def check_coverage(suite_ids: list[str], prd_ids: list[str]) -> list[str]:
    """覆盖闭合（T1）：PRD 验收点未被套件声明的 → uncovered（保持 PRD 序，机械可查）。"""
    have = set(suite_ids)
    return [i for i in prd_ids if i not in have]


def store_suite(store_root: str | Path, files: dict[str, str], *,
                prd_criteria_ids: list[str], suite_criteria_ids: list[str], harness: str,
                sj=None, iter_id: str = "", prd_id: str = "",
                supersedes: str | None = None,
                self_check: SelfCheck | None = None) -> dict | None:
    """编写产物 → content-addressed bundle + journal 事件；缺席路径显式记事件返 None。

    Returns:
        ``{digest, path, size, criteria_ids, uncovered, harness, files, supersedes}``
        （files = 逐文件 ``{path, digest}``，verify 锚③ / dev 注入消费）；None = 缺席
        （事件已发：no_criteria / no_harness / self_check_failed）。观测层契约同
        handoff：store/emit 失败吞异常记日志——工件链是增补非判据，不拖垮 dispatch。
    """
    try:
        if not prd_criteria_ids:
            record_absence(sj, iter_id, prd_id, reason="no_criteria",
                           detail="PRD 无验收点（机械提取为空）——无可编写面")
            return None
        if not harness:
            record_absence(sj, iter_id, prd_id, reason="no_harness",
                           detail="profile 未声明 harness——不猜测测试面（v1 支持 jest/pytest）")
            return None
        if self_check is not None:
            ok, detail = self_check()
            if not ok:
                record_absence(sj, iter_id, prd_id, reason="self_check_failed", detail=detail)
                return None
        if not files:
            record_absence(sj, iter_id, prd_id, reason="no_files",
                           detail="编写产物为空——不产空测试套件")
            return None

        uncovered = check_coverage(suite_criteria_ids, prd_criteria_ids)
        payload = _bundle_payload(files, suite_criteria_ids, harness)
        content = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        ref = artifact_store.store(store_root, content, kind="acceptance_suite",
                                   sensitivity="internal")
        suite_ref = {
            "digest": ref.digest, "path": ref.path, "size": ref.size,
            "criteria_ids": list(suite_criteria_ids), "uncovered": uncovered,
            "harness": harness,
            "files": [{"path": f["path"], "digest": f["digest"]} for f in payload["files"]],
            "supersedes": supersedes,
        }
        if sj is not None and getattr(sj, "enabled", False):
            sj.emit("acceptance_tests", iter_id, prd_id, payload={
                **suite_ref, "status": "stored", "iteration_id": iter_id,
            })
        return suite_ref
    except Exception:
        # 观测层契约：工件链自身故障不拦 dispatch（fail-open，全量门兜底）
        record_absence(sj, iter_id, prd_id, reason="store_failed", detail="内部异常")
        return None


def record_absence(sj, iter_id: str, prd_id: str, *, reason: str, detail: str = "") -> None:
    """显式缺席事件（同 acceptance_tests 事件面，status=absent + reason + detail）。"""
    if sj is None or not getattr(sj, "enabled", False):
        return
    sj.emit("acceptance_tests", iter_id, prd_id,
            payload={"status": "absent", "reason": reason, "detail": detail,
                     "iteration_id": iter_id})


def load_suite(store_root: str | Path, suite_ref: dict) -> dict:
    """读回 bundle 并重算 digest 校验（fail-closed：篡改 → ArtifactIntegrityError）。

    Returns:
        bundle payload（files 含 content，注入时逐文件解包）。
    """
    from loop_state import ArtifactRef
    ref = ArtifactRef(digest=suite_ref["digest"], size=suite_ref.get("size", 0),
                      kind="acceptance_suite", path=suite_ref["path"],
                      sensitivity="internal")
    content = artifact_store.load(store_root, ref)
    return json.loads(content.decode("utf-8"))


def latest_suite(events: list[dict], prd_id: str) -> dict | None:
    """journal 事件流中同 prd 最新 stored 套件引用（supersedes 后消费方读新版本）。

    events 为 ``sj.events`` 形态（dict 带 type/iter/prd/payload）；absent 不顶掉 stored。
    """
    latest = None
    for ev in events:
        if ev.get("type") != "acceptance_tests" or ev.get("prd") != prd_id:
            continue
        p = ev.get("payload") or {}
        if p.get("status") == "stored":
            latest = p
    return latest


def inject_files(store_root: str | Path, suite_ref: dict) -> dict[str, str]:
    """注入解包面：套件引用 → ``{rel_path: content}``（load + digest 校验 + 解包一步）。"""
    return inject_from_bundle(load_suite(store_root, suite_ref))


def inject_from_bundle(bundle: dict) -> dict[str, str]:
    """bundle payload → ``{rel_path: content}``（编排器机械拷入 dev/verify worktree 的输入）。"""
    return {f["path"]: f["content"] for f in bundle["files"]}


def file_digests(suite_ref: dict) -> dict[str, str]:
    """套件引用 → 逐文件 digest 映射（verify 锚③ 注入后复核的期望值面）。"""
    return {f["path"]: f["digest"] for f in suite_ref["files"]}


def verify_file(content: str, expected_digest: str) -> bool:
    """单文件内容对期望 digest（锚③ 的逐文件复核原语）。"""
    return compute_digest(content.encode("utf-8")) == expected_digest
