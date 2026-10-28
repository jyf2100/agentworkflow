"""cli.py 冒烟测试——面板前的人肉驱动面（add-hub-skeleton 任务 8.2：走 api 同一内部函数）。"""
from pathlib import Path

import cli
import notify


def _env(tmp_path: Path) -> dict:
    return {
        "HUB_USER_ID": "roc",
        # 长值：vault-import 录入口有占位符判据（vault_store.placeholder_issues，MIN_TOKEN_LEN=8）
        "HUB_USER_ANTHROPIC_BASE_URL": "https://litellm.example",
        "HUB_USER_ANTHROPIC_AUTH_TOKEN": "tok-abcdef0123456789abcdef",
        "HUB_USER_GH_TOKEN": "ghp_abcdefghij0123456789",
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
    assert "ghp_" not in out and "tok-abc" not in out and "litellm.example" not in out  # 明文值不回显

    vault = vs.CredentialVault(tmp_path / "data", key)
    assert vault.get_user_env("roc") == {
        "ANTHROPIC_BASE_URL": "https://litellm.example",
        "ANTHROPIC_AUTH_TOKEN": "tok-abcdef0123456789abcdef",
        "GH_TOKEN": "ghp_abcdefghij0123456789",
        "ANTHROPIC_MODEL": "haiku",
    }
    monkeypatch.setenv("HUB_USER_GH_TOKEN", "ghp-rotated-0123456789abcdef")
    assert cli.main(["vault-import", "--from-env"]) == 0  # 重复导入幂等覆盖
    assert vault.get_user_env("roc")["GH_TOKEN"] == "ghp-rotated-0123456789abcdef"


def test_cli_vault_import_missing_key_fails(tmp_path, capsys, monkeypatch):
    for k, v in _env(tmp_path).items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("HUB_VAULT_MASTER_KEY", raising=False)
    rc = cli.main(["vault-import", "--from-env"])
    assert rc == 2
    assert "HUB_VAULT_MASTER_KEY" in capsys.readouterr().err


def test_cli_vault_import_rejects_placeholder(tmp_path, capsys, monkeypatch):
    """录入口拦截（2026-10-23 首跑 #3）：hub.env 模板占位值 → 拒导 rc=2，不落垃圾记录。"""
    from cryptography.fernet import Fernet

    import vault_store as vs

    key = Fernet.generate_key()
    monkeypatch.setenv("HUB_VAULT_MASTER_KEY", key.decode())
    for k, v in _env(tmp_path).items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("HUB_USER_ANTHROPIC_BASE_URL", "https://x")   # 模板事故形态
    monkeypatch.setenv("HUB_USER_ANTHROPIC_AUTH_TOKEN", "t")
    rc = cli.main(["vault-import", "--from-env"])
    assert rc == 2
    assert "占位符" in capsys.readouterr().err
    assert vs.CredentialVault(tmp_path / "data", key).get_user_env("roc") is None  # 未落记录


def test_cli_vault_import_preserves_tokens_not_in_env(tmp_path, capsys, monkeypatch):
    """审核修复 M4：vault 已有面板录入的平台 token 而 hub.env 未设 → 重导不抹（与面板 merge 同语义）。"""
    from cryptography.fernet import Fernet

    import vault_store as vs

    key = Fernet.generate_key()
    monkeypatch.setenv("HUB_VAULT_MASTER_KEY", key.decode())
    for k, v in _env(tmp_path).items():
        monkeypatch.setenv(k, v)
    assert cli.main(["vault-import", "--from-env"]) == 0
    vault = vs.CredentialVault(tmp_path / "data", key)
    rec = dict(vault.get_user_env("roc"))
    rec["GITLAB_TOKEN"] = "glpat-abcdef0123456789ab"   # 面板 merge 录入形态（hub.env 无此键）
    vault.set_user("roc", rec)
    assert cli.main(["vault-import", "--from-env"]) == 0   # 重导（env 无 GITLAB_TOKEN）
    after = vault.get_user_env("roc")
    assert after["GITLAB_TOKEN"] == "glpat-abcdef0123456789ab"   # 旧值保留（原整覆会静默抹掉）
    assert after["GH_TOKEN"] == "ghp_abcdefghij0123456789"       # env 非空键照常覆盖（幂等不变）


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


# ── 入站自检（check-intake，add-mm-channel-intake 7.1；Fake 注入全离线）──

_C = "c1b2c3d4e5f6a1b2c3d4e5f6a1"  # 26 字符频道 id
_BOT = "f" * 26


def _intake_env(tmp_path: Path) -> dict:
    return {**_notify_env(tmp_path), "HUB_MM_SLASH_TOKEN": "slash-tok"}


def _bound_db(tmp_path: Path, *pairs) -> None:
    import db as hub_db
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    d = hub_db.HubDB(tmp_path / "data" / "hub.sqlite3")
    for project, chan in pairs:
        d.upsert_bind(project, chan, "test")


def test_check_intake_missing_slash_token_exit_1(tmp_path, capsys, monkeypatch):
    for k, v in _notify_env(tmp_path).items():  # 有 bot 凭据、无 slash token
        monkeypatch.setenv(k, v)
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    rc = cli.main(["check-intake"], mm=notify.FakeMMClient())
    assert rc == 1 and "HUB_MM_SLASH_TOKEN" in capsys.readouterr().err


def test_check_intake_bot_channel_off_exit_1(tmp_path, capsys, monkeypatch):
    for k, v in {**_env(tmp_path), "HUB_MM_SLASH_TOKEN": "slash-tok"}.items():
        monkeypatch.setenv(k, v)  # slash 配了但 bot 凭据缺
    monkeypatch.delenv("HUB_MM_BOT_TOKEN", raising=False)
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    rc = cli.main(["check-intake"], mm=notify.FakeMMClient())
    assert rc == 1 and "fail-closed" in capsys.readouterr().err


def test_check_intake_empty_bindings_warns_but_pass(tmp_path, capsys, monkeypatch):
    for k, v in _intake_env(tmp_path).items():
        monkeypatch.setenv(k, v)
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    rc = cli.main(["check-intake"], mm=notify.FakeMMClient())
    out = capsys.readouterr().out
    assert rc == 0 and "绑定表空" in out and "/pa bind" in out


def test_check_intake_all_bindings_reachable_pass(tmp_path, capsys, monkeypatch):
    for k, v in _intake_env(tmp_path).items():
        monkeypatch.setenv(k, v)
    _bound_db(tmp_path, ("proj-a", _C))
    mm = notify.FakeMMClient(channel_members={_C: [_MM_ID, _BOT]})
    rc = cli.main(["check-intake"], mm=mm)
    out = capsys.readouterr().out
    assert rc == 0 and "proj-a" in out and "含 bot" in out and "自检通过" in out


def test_check_intake_bot_not_in_channel_exit_1(tmp_path, capsys, monkeypatch):
    for k, v in _intake_env(tmp_path).items():
        monkeypatch.setenv(k, v)
    _bound_db(tmp_path, ("proj-a", _C))
    mm = notify.FakeMMClient(channel_members={_C: [_MM_ID]})  # 无 bot
    rc = cli.main(["check-intake"], mm=mm)
    err = capsys.readouterr().err
    assert rc == 1 and "bot 不在频道" in err


def test_check_intake_channel_missing_exit_1(tmp_path, capsys, monkeypatch):
    for k, v in _intake_env(tmp_path).items():
        monkeypatch.setenv(k, v)
    _bound_db(tmp_path, ("proj-a", _C))
    mm = notify.FakeMMClient(members_not_found={_C})  # 404
    rc = cli.main(["check-intake"], mm=mm)
    err = capsys.readouterr().err
    assert rc == 1 and "频道不存在" in err and "unbind" in err


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
