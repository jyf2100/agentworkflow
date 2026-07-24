#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""r3 P1-2：evidence index 可独立复核单元测试。

runtime_evidence.py 作为 CLI 工具历来靠真实 ``--drill`` 运行验证（无单测传统）。本文件专测 r3 P1-2 新增
的 ``write_evidence_index`` 纯函数：index 含独立复核必需字段（digest/git/存储位置/验证命令），且不内联
evidence blob 内容（sensitivity 守则）。
"""
import json

import artifact_store as A
import runtime_evidence as RE


def test_write_evidence_index_has_verification_fields_and_no_content_leak(tmp_path):
    """r3 P1-2：index 含独立复核必需字段，且不内联 evidence drill 内容（仅 digest + 元数据 + 验证命令）。"""
    art_root = tmp_path / "art"
    evidence = {
        "collected_at": "2026-07-24T00:00:00Z", "host": "testhost",
        "failed": False, "failed_drills": [],
        "drills": {"7.6_cutover_suite": {
            "artifact_root": "/tmp/cutover-art",
            "archive_digest": "sha256:manifest", "overall_passed": True,
            "evidence_integrity": "ok",
            "sub_evidence_refs": ["sha256:s1", "sha256:s2"],
            "outcomes": [{"name": "shadow_parity", "passed": True,
                          "detail": "SECRET_DETAIL_MARKER_5f8a",
                          "evidence_digests": ["sha256:s1"]},
                         {"name": "quality_gate", "passed": True,
                          "detail": "ok", "evidence_digests": ["sha256:q1"]}]}},
    }
    # 顶层 evidence blob 真实归档（拿 manifest_ref，digest 绑定 blob 内容）
    blob = json.dumps(evidence, ensure_ascii=False, sort_keys=True)
    ref = A.store(str(art_root), blob, kind="test_output", sensitivity="internal")

    idx_path = tmp_path / "runtime-evidence-index.json"
    out = RE.write_evidence_index(index_path=idx_path, evidence=evidence, manifest_ref=ref,
                                  artifact_root=art_root, drill="all")
    text = out.read_text(encoding="utf-8")

    # 不内联 evidence drill 内容（sensitivity 守则：index 仅 digest + 元数据 + 验证命令）
    assert "SECRET_DETAIL_MARKER_5f8a" not in text

    index = json.loads(text)
    # 独立复核必需字段
    assert index["schema_version"] == 1
    assert index["evidence_manifest"]["digest"] == ref.digest
    assert index["evidence_manifest"]["artifact_root"] == str(art_root)
    assert "commit" in index["git"]
    assert index["runner"]["drill"] == "all"
    # r3 P0-2 核心证据链（cutover 7 子 evidence digest 清单）
    sub = index["cutover_sub_evidence"]
    assert sub["artifact_root"] == "/tmp/cutover-art"   # r3 P1-2：子证据真实存储根（评审据此 load）
    assert sub["sub_evidence_refs"] == ["sha256:s1", "sha256:s2"]
    assert sub["evidence_integrity"] == "ok"
    assert {o["name"] for o in sub["outcomes"]} == {"shadow_parity", "quality_gate"}
    # 验证命令可复制可用
    assert "artifact_store" in index["verification"]["load_command"]
    assert "--drill all" in index["verification"]["rerun_command"]
