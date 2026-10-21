"""queue_engine.py 单测——排度核心（add-hub-skeleton 任务 3.1-3.3，spec: execution-hub「队列排度」+ A3 重派状态机）。"""
from pathlib import Path

import db
import queue_engine as qe


def _engine(tmp_path: Path, max_containers: int = 3, max_retries: int = 2):
    d = db.HubDB(tmp_path / "h.sqlite3")
    return (
        qe.QueueEngine(d, max_containers=max_containers, max_retries=max_retries, image_tag_default="pa-executor:test"),
        d,
    )


def _run_cycle(engine, item_id, rc):
    """模拟一次派发旅程：选中→running→容器退出 rc。"""
    engine.mark_running(item_id)
    engine.on_exit(item_id, exit_code=rc)


def test_pick_next_skips_running_project_and_serves_others(tmp_path):
    eng, d = _engine(tmp_path)
    a1 = d.enqueue("a", "/p1", "t", "u"); d.enqueue("a", "/p2", "t", "u"); b1 = d.enqueue("b", "/p3", "t", "u")
    eng.mark_running(a1)
    # spec scenario「同项目第二项等待」：A 的 pending 不派，B 照常
    picked = eng.pick_next()
    assert picked is not None and picked["id"] == b1
    eng.mark_running(b1)
    assert eng.pick_next() is None


def test_pick_next_respects_peak_cap(tmp_path):
    eng, d = _engine(tmp_path, max_containers=2)
    a = d.enqueue("a", "/p1", "t", "u"); b = d.enqueue("b", "/p2", "t", "u"); c = d.enqueue("c", "/p3", "t", "u")
    eng.mark_running(a); eng.mark_running(b)
    assert eng.pick_next() is None  # spec scenario「峰值上限约束」
    eng.on_exit(a, exit_code=0)     # 名额释放
    picked = eng.pick_next()
    assert picked is not None and picked["id"] == c


def test_requeued_item_wins_fifo_over_later_enqueue(tmp_path):
    eng, d = _engine(tmp_path)
    a = d.enqueue("a", "/p1", "t", "u"); b = d.enqueue("b", "/p2", "t", "u")
    _run_cycle(eng, a, rc=1)  # a 失败回 pending（attempt=1）
    eng.mark_running(b)       # b 在跑
    picked = eng.pick_next()
    assert picked is not None and picked["id"] == a  # A3：重派项因入队更早天然优先


def test_on_exit_zero_marks_done(tmp_path):
    eng, d = _engine(tmp_path)
    a = d.enqueue("a", "/p", "t", "u")
    _run_cycle(eng, a, rc=0)
    assert d.get_item(a)["status"] == "done"


def test_on_exit_retries_then_fails_after_limit(tmp_path):
    eng, d = _engine(tmp_path, max_retries=2)
    a = d.enqueue("a", "/p", "t", "u")
    _run_cycle(eng, a, rc=1)
    item = d.get_item(a)
    assert item["status"] == "pending" and item["attempt"] == 1  # 第一次失败：回 pending 保留计数
    _run_cycle(eng, a, rc=1)
    item = d.get_item(a)
    assert item["status"] == "failed" and item["attempt"] == 2  # 第二次失败：超限终态（spec scenario「重派超限止损」）


def test_fail_item_is_terminal_with_reason_recorded(tmp_path):
    eng, d = _engine(tmp_path)
    a = d.enqueue("a", "/p", "pa-executor:v9.9.9", "u")
    eng.fail_item(a, reason="镜像缺失：pa-executor:v9.9.9（先 build 再重新入队，不回退 latest）")
    item = d.get_item(a)
    assert item["status"] == "failed" and "镜像缺失" in item["last_error"]  # F13：fail 暴露且原因可查


def test_engine_stateless_restart_keeps_semantics(tmp_path):
    eng, d = _engine(tmp_path)
    a = d.enqueue("a", "/p1", "t", "u"); d.enqueue("a", "/p2", "t", "u"); b = d.enqueue("b", "/p3", "t", "u")
    eng.mark_running(a)
    eng2, _ = _engine(tmp_path)  # hub 重启：新引擎同 DB
    picked = eng2.pick_next()
    assert picked is not None and picked["id"] == b  # 语义照常：A 串行挡住、B 可派、队列不丢


def test_get_item_public_read_facade(tmp_path):
    # B5：引擎提供队列项公共读面——api/cli 回读经引擎，不穿透 _db
    eng, d = _engine(tmp_path)
    a = eng.enqueue("a", "/p.md", "roc")
    item = eng.get_item(a)
    assert item["id"] == a and item["project"] == "a" and item["status"] == "pending"


def test_enqueue_pins_default_tag_when_absent(tmp_path):
    # F13 spec scenario「入队固化 tag」：未显式传 → 取配置默认并写入队列项
    eng, d = _engine(tmp_path)
    q1 = eng.enqueue("a", "/p1.md", "roc")
    assert d.get_item(q1)["image_tag"] == "pa-executor:test"
    q2 = eng.enqueue("b", "/p2.md", "roc", image_tag="pa-executor:v0.9.0")
    assert d.get_item(q2)["image_tag"] == "pa-executor:v0.9.0"  # 显式值固化，派发与 meta 均用记录值
