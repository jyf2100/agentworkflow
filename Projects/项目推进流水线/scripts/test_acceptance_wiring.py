"""agent-verify-injection 1.4 接线测试：_author_acceptance_suite / _author_acceptance_for_gate。

缺席三分显式登记、结构自检扣留、persona 异常 fail-open 不穿透、同 stamp 幂等复用、
正常路径 store 落盘 + 登记文件字段。run_persona 全 mock（零 LLM）。
"""
import json

import run_daily


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path)
    calls = []

    def fake_persona(name, prompt, stage, label, allowed_tools=None, env=None):
        calls.append({"name": name, "stage": stage})
        return fake_persona.ret

    fake_persona.ret = ({}, {"cost": 0.01})
    monkeypatch.setattr(run_daily, "run_persona", fake_persona)
    return tmp_path, calls, fake_persona


def _gate_entry(tmp_path, prd_text="# PRD\n## 验收标准\n- A 必须 1\n- B 必须 2\n"):
    prd = tmp_path / "20261027_demo.md"
    prd.write_text(prd_text, encoding="utf-8")
    return {"prd_path": str(prd), "project": "demo-proj", "verdict": "pass"}


def test_absent_no_harness_registered(tmp_path, monkeypatch):
    """profile 未声明 harness → absent 登记（reason=no_harness），零 persona 调用。"""
    root, calls, _ = _setup(tmp_path, monkeypatch)
    r = run_daily._author_acceptance_suite(_gate_entry(root), {}, "20261027")
    assert r["status"] == "absent" and r["reason"] == "no_harness"
    assert calls == []
    reg = list(root.glob("acceptance_20261027_*.json"))
    assert len(reg) == 1 and json.loads(reg[0].read_text())["reason"] == "no_harness"


def test_absent_no_criteria(tmp_path, monkeypatch):
    """PRD 无验收节 → absent（no_criteria）。"""
    root, calls, _ = _setup(tmp_path, monkeypatch)
    r = run_daily._author_acceptance_suite(
        _gate_entry(root, prd_text="# PRD 无验收\n正文"), {"harness": "jest"}, "20261027")
    assert r["reason"] == "no_criteria" and calls == []


def test_stored_path_and_registration(tmp_path, monkeypatch):
    """正常路径：persona manifest → 文件落盘读取 → 结构自检过 → store + stored 登记。"""
    root, calls, fp = _setup(tmp_path, monkeypatch)
    out_dir = root / "acceptance" / "20261027" / "demo-proj_20261027_demo"
    out_dir.mkdir(parents=True)
    tf = out_dir / "test_ac1.test.js"
    tf.write_text("// criterion: 20261027-demo:ac1\ntest('ac1', () => {});", encoding="utf-8")
    fp.ret = ({"files": [{"path": str(tf), "criteria_ids": ["20261027-demo:ac1"]}],
               "uncovered": [{"id": "20261027-demo:ac2", "reason": "UI 面无自动化锚"}],
               "refused": None}, {"cost": 0.05})
    r = run_daily._author_acceptance_suite(
        _gate_entry(root), {"harness": "jest"}, "20261027")
    assert r["status"] == "stored" and r["digest"].startswith("sha256:")
    assert r["criteria_ids"] == ["20261027-demo:ac1"]
    assert r["uncovered"] == ["20261027-demo:ac2"]          # T1 显式 uncovered
    assert calls[0]["name"] == "pa-test-author" and calls[0]["stage"] == "test-author"
    assert (root / "acceptance_store" / r["path"]).is_file()   # bundle 真落盘


def test_self_check_withheld(tmp_path, monkeypatch):
    """文件零 criterion 标注 → 扣留 absent（self_check_failed），不 store。"""
    root, calls, fp = _setup(tmp_path, monkeypatch)
    out_dir = root / "acceptance" / "20261027" / "demo-proj_20261027_demo"
    out_dir.mkdir(parents=True)
    tf = out_dir / "test_x.test.js"
    tf.write_text("test('no annotation', () => {});", encoding="utf-8")
    fp.ret = ({"files": [{"path": str(tf), "criteria_ids": ["20261027-demo:ac1"]}],
               "uncovered": [], "refused": None}, {"cost": 0.05})
    r = run_daily._author_acceptance_suite(_gate_entry(root), {"harness": "jest"}, "20261027")
    assert r["status"] == "absent" and r["reason"] == "self_check_failed"
    assert not list((root / "acceptance_store").rglob("*")) if (root / "acceptance_store").exists() else True


def test_persona_failure_fail_open(tmp_path, monkeypatch):
    """persona 抛异常 → absent（author_failed）不穿透（fail-open，全量门兜底）。"""
    root, calls, fp = _setup(tmp_path, monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("persona wall-clock 超时")
    monkeypatch.setattr(run_daily, "run_persona", boom)
    r = run_daily._author_acceptance_suite(_gate_entry(root), {"harness": "pytest"}, "20261027")
    assert r["status"] == "absent" and r["reason"] == "author_failed"
    assert "超时" in r["detail"]


def test_same_stamp_idempotent_reuse(tmp_path, monkeypatch):
    """同 stamp 第二次调用 → 登记文件复用，persona 不再调（不重复烧 LLM）。"""
    root, calls, fp = _setup(tmp_path, monkeypatch)
    fp.ret = ({"files": [], "uncovered": [], "refused": {"reason": "信息不足"}}, {"cost": 0.01})
    e = _gate_entry(root)
    r1 = run_daily._author_acceptance_suite(e, {"harness": "jest"}, "20261027")
    assert r1["reason"] == "refused"
    r2 = run_daily._author_acceptance_suite(e, {"harness": "jest"}, "20261027")
    assert r2 == r1 and len(calls) == 1


def test_harness_outside_v1_support_set(tmp_path, monkeypatch):
    """harness=mocha（v1 支持集外）→ no_harness + detail 记原值（T5 显式降级不猜测）。"""
    root, calls, _ = _setup(tmp_path, monkeypatch)
    r = run_daily._author_acceptance_suite(_gate_entry(root), {"harness": "mocha"}, "20261027")
    assert r["status"] == "absent" and r["reason"] == "no_harness"
    assert "mocha" in r.get("detail", "")
    assert calls == []


def test_for_gate_only_pass_entries(tmp_path, monkeypatch):
    """_author_acceptance_for_gate 只碰 verdict=pass；缺 path 的 pass 也跳过。"""
    root, calls, fp = _setup(tmp_path, monkeypatch)
    entries = [{"prd_path": "", "project": "x", "verdict": "pass"},
               {"prd_path": "gone.md", "project": "x", "verdict": "drop"}]
    run_daily._author_acceptance_for_gate(entries, {}, "20261027")
    assert calls == []   # pass+缺path 跳过；drop 不碰
