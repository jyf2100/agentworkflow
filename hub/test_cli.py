"""cli.py 冒烟测试——面板前的人肉驱动面（add-hub-skeleton 任务 8.2：走 api 同一内部函数）。"""
from pathlib import Path

import cli
import notify


def _env(tmp_path: Path) -> dict:
    return {
        "HUB_USER_ID": "roc",
        "HUB_USER_ANTHROPIC_BASE_URL": "https://x",
        "HUB_USER_ANTHROPIC_AUTH_TOKEN": "tok",
        "HUB_USER_GH_TOKEN": "ghp",
        "HUB_IMAGE_TAG_DEFAULT": "pa-executor:v0.1.3",
        "HUB_DATA_ROOT": str(tmp_path / "data"),
    }


def test_cli_enqueue_then_status_roundtrip(tmp_path, capsys, monkeypatch):
    for k, v in _env(tmp_path).items():
        monkeypatch.setenv(k, v)
    db_path = tmp_path / "h.sqlite3"
    prd = tmp_path / "p.md"; prd.write_text("# x", encoding="utf-8")
    rc = cli.main(["--db", str(db_path), "enqueue", "--project", "proj-a", "--prd", str(prd)], runner=None)
    assert rc == 0
    out = capsys.readouterr().out
    assert "proj-a" in out and "pending" in out
    rc = cli.main(["--db", str(db_path), "status"], runner=None)
    assert rc == 0
    out = capsys.readouterr().out
    assert "proj-a" in out and "pending" in out  # 与 api.queue_view 同源数据


def test_cli_vault_import_roundtrip_no_echo(tmp_path, capsys, monkeypatch):
    """vault-import --from-env（add-hub-slice-2a 1.3）：落密文、幂等覆盖、输出不回显明文。"""
    from cryptography.fernet import Fernet

    import vault_store as vs

    key = Fernet.generate_key()
    for k, v in _env(tmp_path).items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("HUB_VAULT_MASTER_KEY", key.decode())
    monkeypatch.setenv("HUB_USER_MODEL", "haiku")  # 可选模型路由随 payload 一并导入

    rc = cli.main(["vault-import", "--from-env"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "GH_TOKEN" in out and "ANTHROPIC_MODEL" in out  # 字段名可见
    assert "ghp" not in out and "tok" not in out and "https://x" not in out  # 明文值不回显

    vault = vs.CredentialVault(tmp_path / "data", key)
    assert vault.get_user_env("roc") == {
        "ANTHROPIC_BASE_URL": "https://x",
        "ANTHROPIC_AUTH_TOKEN": "tok",
        "GH_TOKEN": "ghp",
        "ANTHROPIC_MODEL": "haiku",
    }
    monkeypatch.setenv("HUB_USER_GH_TOKEN", "ghp-rotated")
    assert cli.main(["vault-import", "--from-env"]) == 0  # 重复导入幂等覆盖
    assert vault.get_user_env("roc")["GH_TOKEN"] == "ghp-rotated"


def test_cli_vault_import_missing_key_fails(tmp_path, capsys, monkeypatch):
    for k, v in _env(tmp_path).items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("HUB_VAULT_MASTER_KEY", raising=False)
    rc = cli.main(["vault-import", "--from-env"])
    assert rc == 2
    assert "HUB_VAULT_MASTER_KEY" in capsys.readouterr().err


def test_cli_entrypoint_runs_as_script(tmp_path):
    """`python3 cli.py status` 直跑必须有输出（5.3 真机验证坐实的回归防：
    __main__ 入口块缺失时 import 即静默退出——rc=0 零输出，CLI 形同虚设）。"""
    import os
    import subprocess
    import sys

    (tmp_path / "data").mkdir()
    env = {**os.environ, **_env(tmp_path), "HUB_VAULT_MASTER_KEY": ""}
    env.pop("HUB_VAULT_MASTER_KEY", None)
    r = subprocess.run(
        [sys.executable, str(Path(__file__).parent / "cli.py"), "status"],
        capture_output=True, text=True, env=env, timeout=60,
    )
    assert r.returncode == 0
    assert "共 0 项" in r.stdout  # 空队列也必须有这行——静默即入口块又没了


# ── 通道自检（check-notify，RUNBOOK §16 联调一键化；Fake 注入全离线）──

_MM_ID = "a1b2c3d4e5f6a1b2c3d4e5f6a1"  # 26-hex


def _notify_env(tmp_path: Path) -> dict:
    return {**_env(tmp_path), "HUB_MM_BOT_TOKEN": "bot-tok", "HUB_MM_BASE_URL": "https://mm"}


def test_check_notify_closed_channel_exit_1(tmp_path, capsys, monkeypatch):
    # 未配 bot token = 通道关闭：报缺项（只字段名）、exit 1、零出站调用
    for k, v in _env(tmp_path).items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("HUB_MM_BOT_TOKEN", raising=False)
    rc = cli.main(["check-notify"], mm=notify.FakeMMClient())
    assert rc == 1
    assert "HUB_MM_BOT_TOKEN" in capsys.readouterr().err


def test_check_notify_full_pass_with_channel(tmp_path, capsys, monkeypatch):
    # 三步全通 + 频道附加：username 解析路径 + DM + 频道各一次，exit 0
    for k, v in _notify_env(tmp_path).items():
        monkeypatch.setenv(k, v)
    mm = notify.FakeMMClient(user_ids={"roc": _MM_ID})
    rc = cli.main(["check-notify", "--channel", "chan-9"], mm=mm)
    assert rc == 0
    out = capsys.readouterr().out
    assert "1/3" in out and "2/3" in out and "3/3" in out and "自检通过" in out
    assert mm.dms == [(_MM_ID, mm.dms[0][1])] and "check-notify" in mm.dms[0][1]
    assert mm.channels == [("chan-9", mm.channels[0][1])]


def test_check_notify_direct_mm_id_skips_lookup(tmp_path, capsys, monkeypatch):
    # --to 26-hex = 直发路径：不 lookup（lookups 空），DM 收件人即该 id
    for k, v in _notify_env(tmp_path).items():
        monkeypatch.setenv(k, v)
    mm = notify.FakeMMClient(user_ids={"roc": _MM_ID})
    rc = cli.main(["check-notify", "--to", _MM_ID], mm=mm)
    assert rc == 0
    assert mm.lookups == []
    assert mm.dms[0][0] == _MM_ID
    assert "直发" in capsys.readouterr().out


def test_check_notify_bad_token_stops_at_step1(tmp_path, capsys, monkeypatch):
    # token 无效（me_id 抛）= 1/3 ✗ 即停：不走解析不发 DM（后续步骤依赖前步）
    for k, v in _notify_env(tmp_path).items():
        monkeypatch.setenv(k, v)
    mm = notify.FakeMMClient(user_ids={"roc": _MM_ID}, crash_on_me=RuntimeError("401"))
    rc = cli.main(["check-notify"], mm=mm)
    assert rc == 1
    err = capsys.readouterr().err
    assert "1/3" in err and "token 不回显" in err
    assert mm.dms == [] and mm.lookups == []


def test_check_notify_lookup_fail_stops_at_step2(tmp_path, capsys, monkeypatch):
    # username 解析失败 = 2/3 ✗ 即停（查无此用户）：不发 DM
    for k, v in _notify_env(tmp_path).items():
        monkeypatch.setenv(k, v)
    mm = notify.FakeMMClient(user_ids={}, fail_lookup={"roc"})
    rc = cli.main(["check-notify"], mm=mm)
    assert rc == 1
    assert "2/3" in capsys.readouterr().err
    assert mm.dms == []
