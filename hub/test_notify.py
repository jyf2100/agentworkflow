"""notify.py 单测——bot 通道 / run 终态通知 / fail-open / 频道广播（add-hub-slice-4a，spec: hub-notifications）。

D7 测试接缝：FakeMMClient 按 (recipient, text) 记调用清单——测试只锁此契约，
不锁 MM API 调用序列。全离线（httpx 不触网）。
"""
from pathlib import Path

import api
import config
import notify

_MM_ID = "a1b2c3d4e5f6a1b2c3d4e5f6a1"  # 26-hex（值形态分流判据）


def _cfg(tmp_path: Path, **over) -> config.HubConfig:
    base = dict(
        user_id="roc", anthropic_base_url="https://litellm.example",
        anthropic_auth_token="tok", gh_token="ghp", user_model=None,
        image_tag_default="pa:v1", data_root=tmp_path / "data",
        max_containers=1, max_retries=0,
        mm_base_url="https://mm.example.com", mm_bot_token="bot-tok",
    )
    base.update(over)
    return config.HubConfig(**base)


def _notify(**kw):
    out = dict(run_id=7, project="proj-a", run_dir_name="20261021_my-feature", status="succeeded")
    out.update(kw)  # 允许覆盖 status/exit_code 等
    return out


def test_build_notifier_off_without_bot_token_or_base_url(tmp_path):
    # D8 关闭态构造：缺 bot token 或缺 base_url → None（通道关闭零行为，两级回退之一级）
    assert notify.build_notifier(_cfg(tmp_path, mm_bot_token=None)) is None
    assert notify.build_notifier(_cfg(tmp_path, mm_base_url=None)) is None
    assert notify.build_notifier(_cfg(tmp_path)) is not None  # 齐备才造


def test_dm_to_mm_user_id_direct_no_lookup(tmp_path):
    # 值形态第一代：26-hex mm_user_id 直发，不经 username 解析
    mm = notify.FakeMMClient()
    n = notify.Notifier(_cfg(tmp_path), mm=mm)
    n.notify_run(**_notify(enqueued_by=_MM_ID))
    assert mm.dms == [(_MM_ID, mm.dms[0][1])] and mm.lookups == []


def test_dm_username_resolved_and_cached(tmp_path):
    # 值形态第二代：username 经 users/username/{name} 解析后发；解析成功进内存缓存（一次 lookup 多次复用）
    mm = notify.FakeMMClient(user_ids={"roc": _MM_ID})
    n = notify.Notifier(_cfg(tmp_path), mm=mm)
    n.notify_run(**_notify(enqueued_by="roc"))
    n.notify_run(**_notify(enqueued_by="roc"))
    assert mm.lookups == ["roc"]  # 第二次命中缓存不再查
    assert len(mm.dms) == 2 and all(r == _MM_ID for r, _ in mm.dms)


def test_dm_unresolvable_username_visible_drop(tmp_path, capsys):
    # 解析失败：可见的丢（stderr warn 留痕）非静默吞；异常不外抛
    mm = notify.FakeMMClient(fail_lookup={"ghost"})
    n = notify.Notifier(_cfg(tmp_path), mm=mm)
    n.notify_run(**_notify(enqueued_by="ghost"))
    assert mm.dms == [] and "ghost" in capsys.readouterr().err


def test_vault_deliver_literal_and_none_fall_back_to_cfg_user(tmp_path):
    # 值形态第三代：机机字面量 / None → 回落 cfg.user_id 走 username 路径；
    # 两代回落同目标 → 第二次命中解析缓存（lookup 只一次）
    mm = notify.FakeMMClient(user_ids={"roc": _MM_ID})
    n = notify.Notifier(_cfg(tmp_path), mm=mm)
    n.notify_run(**_notify(enqueued_by=notify.VAULT_DELIVER_LITERAL))
    n.notify_run(**_notify(enqueued_by=None))
    assert mm.lookups == ["roc"] and len(mm.dms) == 2
    assert all(r == _MM_ID for r, _ in mm.dms)


def test_vault_deliver_literal_matches_api_constant():
    # 锁等防漂移：notify 侧字面量与 api._DELIVER_ENQUEUED_BY 必须同值（两处定义的代价）
    assert notify.VAULT_DELIVER_LITERAL == api._DELIVER_ENQUEUED_BY


def test_notify_crash_fail_open_no_raise(tmp_path, capsys):
    # fail-open：MM 推递崩溃 → notify_run 不抛（stderr warn），调用方流水线零扰动
    mm = notify.FakeMMClient(crash_on_send=RuntimeError("mm down"))
    n = notify.Notifier(_cfg(tmp_path), mm=mm)
    n.notify_run(**_notify(enqueued_by=_MM_ID))  # 不抛即过
    assert "私信推递异常" in capsys.readouterr().err


def test_channel_broadcast_only_failure_statuses(tmp_path):
    # D5：仅 failed / failed_to_start 广播；succeeded 不进频道；project 不在映射也不广播
    mm = notify.FakeMMClient(user_ids={"roc": _MM_ID})
    n = notify.Notifier(_cfg(tmp_path, project_channels={"proj-a": "chan-1"}), mm=mm)
    n.notify_run(**_notify(enqueued_by="roc"))  # succeeded
    assert mm.channels == []
    n.notify_run(**_notify(status="failed", exit_code=1, enqueued_by="roc"))
    assert mm.channels == [("chan-1", mm.channels[0][1])]
    n.notify_run(run_id=8, project="proj-b", run_dir_name="x", status="failed_to_start", enqueued_by="roc")
    assert len(mm.channels) == 1  # proj-b 无映射：不广播


def test_channel_crash_does_not_affect_dm(tmp_path, capsys):
    # 私信与频道并行互不依赖：频道崩 → 私信照发（warn 留痕）
    mm = notify.FakeMMClient(user_ids={"roc": _MM_ID}, crash_on_channel=RuntimeError("chan down"))
    n = notify.Notifier(_cfg(tmp_path, project_channels={"proj-a": "chan-1"}), mm=mm)
    n.notify_run(**_notify(status="failed", enqueued_by="roc"))
    assert len(mm.dms) == 1  # 私信不受频道故障影响
    assert "频道广播异常" in capsys.readouterr().err


def test_message_contains_identifiers_not_routing_initiator(tmp_path):
    # initiator 进文本（人读）不参与路由；消息含 project / run 标识 / 终态 / 失败原因
    mm = notify.FakeMMClient()
    n = notify.Notifier(_cfg(tmp_path), mm=mm)
    n.notify_run(**_notify(status="failed", exit_code=2, enqueued_by=_MM_ID,
                           initiator="zhang3", last_error="起容器异常：docker down"))
    text = mm.dms[0][1]
    assert "proj-a" in text and "#7" in text and "20261021_my-feature" in text
    assert "failed" in text and "exit 2" in text and "zhang3" in text and "docker down" in text
