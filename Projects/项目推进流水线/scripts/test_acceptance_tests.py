"""agent-verify-injection tasks 1.1/1.2/1.9：验收测试工件链。

store_suite 确定性 bundle + journal 事件 acceptance_tests（digest/path/criteria_ids/
uncovered/supersedes/iteration 绑定）；缺席三分（no_criteria/no_harness/self_check_failed）
显式记事件不产空套件；覆盖闭合 uncovered 不静默（T1）；自检扣留（T2）；load fail-closed。
"""
import json

import pytest

import acceptance_tests as AT


class _SJ:
    """fake StructuredJournal：enabled + 捕 emit（event_type/iter/prd/payload）。"""
    enabled = True

    def __init__(self):
        self.events = []

    def emit(self, etype, iter_id, prd_id, payload=None):
        self.events.append({"type": etype, "iter": iter_id, "prd": prd_id,
                            "payload": payload or {}})


_FILES = {"test/prd_ac1.test.js": "test('ac1', () => { expect(1).toBe(1); });"}
_PRD_IDS = ["proj-x:ac1", "proj-x:ac2"]


def _store(sj, root, files=_FILES, suite_ids=("proj-x:ac1",), **kw):
    return AT.store_suite(root, files, prd_criteria_ids=_PRD_IDS,
                          suite_criteria_ids=list(suite_ids), harness="jest",
                          sj=sj, iter_id="it-1", prd_id="prd-1", **kw)


def test_store_digest_stable_and_content_sensitive(tmp_path):
    """同文件集两次 store → 同 digest（content-addressed 幂等）；内容变 → digest 变。"""
    sj = _SJ()
    r1 = _store(sj, tmp_path); r2 = _store(sj, tmp_path)
    assert r1["digest"] == r2["digest"]
    files2 = dict(_FILES); files2["test/prd_ac1.test.js"] = "test('changed', () => {});"
    r3 = _store(sj, tmp_path, files=files2)
    assert r3["digest"] != r1["digest"]


def test_journal_event_fields_complete(tmp_path):
    """stored 事件字段齐：digest/path/size/criteria_ids/uncovered/harness/files/iteration。"""
    sj = _SJ()
    ref = _store(sj, tmp_path)
    ev = sj.events[-1]
    assert ev["type"] == "acceptance_tests" and ev["iter"] == "it-1" and ev["prd"] == "prd-1"
    p = ev["payload"]
    assert p["status"] == "stored" and p["digest"] == ref["digest"] and p["path"] == ref["path"]
    assert p["criteria_ids"] == ["proj-x:ac1"] and p["harness"] == "jest"
    assert p["iteration_id"] == "it-1"          # 1.9 iteration 绑定
    assert p["files"] == ref["files"]           # 逐文件 digest（verify 锚③ 面）


def test_no_criteria_or_no_harness_absent(tmp_path):
    """PRD 无验收点 / profile 无 harness → absent 事件，不 store 不产空套件。"""
    sj = _SJ()
    r = AT.store_suite(tmp_path, _FILES, prd_criteria_ids=[], suite_criteria_ids=[],
                       harness="jest", sj=sj, iter_id="it-1", prd_id="prd-1")
    assert r is None
    assert sj.events[-1]["payload"]["status"] == "absent"
    assert sj.events[-1]["payload"]["reason"] == "no_criteria"
    r2 = AT.store_suite(tmp_path, _FILES, prd_criteria_ids=_PRD_IDS,
                        suite_criteria_ids=["proj-x:ac1"], harness="",
                        sj=sj, iter_id="it-1", prd_id="prd-1")
    assert r2 is None and sj.events[-1]["payload"]["reason"] == "no_harness"
    assert not list(tmp_path.rglob("sha256/*"))  # 零落盘


def test_coverage_closure_uncovered_explicit(tmp_path):
    """PRD 点未被套件声明 → uncovered 显式进事件（T1：orphan 不静默）。"""
    sj = _SJ()
    ref = _store(sj, tmp_path, suite_ids=("proj-x:ac1", "proj-x:ac2"))
    assert ref["uncovered"] == []
    sj2 = _SJ()
    ref2 = _store(sj2, tmp_path, suite_ids=("proj-x:ac1",))   # 缺 ac2
    assert ref2["uncovered"] == ["proj-x:ac2"]
    assert sj2.events[-1]["payload"]["uncovered"] == ["proj-x:ac2"]
    assert sj2.events[-1]["payload"]["status"] == "stored"    # uncovered 记录不扣留


def test_self_check_withheld(tmp_path):
    """收集级自检失败 → 扣留（absent reason=self_check_failed），零落盘。"""
    sj = _SJ()
    r = _store(sj, tmp_path, self_check=lambda: (False, "pytest --collect-only: 2 errors"))
    assert r is None
    p = sj.events[-1]["payload"]
    assert p["status"] == "absent" and p["reason"] == "self_check_failed"
    assert "collect-only" in p["detail"]
    assert not list(tmp_path.rglob("sha256/*"))
    r2 = _store(sj, tmp_path, self_check=lambda: (True, "ok"))   # 通过则正常 store
    assert r2 and r2["digest"].startswith("sha256:")


def test_supersedes_version_chain(tmp_path):
    """重编写 → 新 digest + supersedes 指旧 digest（T2 版本链）；同版本幂等不带。"""
    sj = _SJ()
    r1 = _store(sj, tmp_path)
    assert "supersedes" not in sj.events[-1]["payload"] or sj.events[-1]["payload"]["supersedes"] is None
    files2 = {"test/prd_ac1b.test.js": "test('ac1 v2', () => {});"}
    r2 = AT.store_suite(tmp_path, files2, prd_criteria_ids=_PRD_IDS,
                        suite_criteria_ids=["proj-x:ac1"], harness="jest",
                        sj=sj, iter_id="it-1", prd_id="prd-1", supersedes=r1["digest"])
    assert r2["digest"] != r1["digest"]
    assert sj.events[-1]["payload"]["supersedes"] == r1["digest"]


def test_load_suite_roundtrip_and_fail_closed(tmp_path):
    """load 读回内容一致；bundle 被篡改 → fail-closed raise（digest 校验）。"""
    sj = _SJ()
    ref = _store(sj, tmp_path)
    bundle = AT.load_suite(tmp_path, ref)
    assert bundle["files"][0]["path"] == "test/prd_ac1.test.js"
    assert bundle["criteria_ids"] == ["proj-x:ac1"]
    victim = tmp_path / ref["path"]
    victim.write_text(json.dumps({"tampered": True}), encoding="utf-8")
    with pytest.raises(Exception):
        AT.load_suite(tmp_path, ref)


def test_latest_suite_ref_from_events(tmp_path):
    """多事件取同 prd 最新 stored（supersedes 后消费方读新版本）；absent 不顶掉 stored。"""
    sj = _SJ()
    r1 = _store(sj, tmp_path)
    AT.record_absence(sj, "it-1", "prd-1", reason="self_check_failed", detail="x")
    files2 = {"test/v2.test.js": "// v2"}
    r2 = AT.store_suite(tmp_path, files2, prd_criteria_ids=_PRD_IDS,
                        suite_criteria_ids=["proj-x:ac1"], harness="jest",
                        sj=sj, iter_id="it-1", prd_id="prd-1", supersedes=r1["digest"])
    got = AT.latest_suite(sj.events, "prd-1")
    assert got["digest"] == r2["digest"] and got["supersedes"] == r1["digest"]
    assert AT.latest_suite(sj.events, "prd-other") is None


def test_inject_unpack_digests(tmp_path):
    """解包面：inject_files(root, ref) 返回逐文件 content + digest 可独立复核（锚③ 输入）。"""
    sj = _SJ()
    ref = _store(sj, tmp_path)
    inj = AT.inject_files(tmp_path, ref)
    assert inj["test/prd_ac1.test.js"] == _FILES["test/prd_ac1.test.js"]
    per = AT.file_digests(ref)
    assert per["test/prd_ac1.test.js"].startswith("sha256:")
    assert AT.verify_file(inj["test/prd_ac1.test.js"], per["test/prd_ac1.test.js"])
    assert not AT.verify_file("changed", per["test/prd_ac1.test.js"])
