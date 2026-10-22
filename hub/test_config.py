"""config.py 单测——切片 1 单用户 env 直注（add-hub-skeleton 任务 1.2，spec: execution-hub「单用户 env 直注 bootstrap」）。"""
import pytest
import config


def _full_env(**over):
    """完整合法 env（覆盖键传 None 表示删除该键）。"""
    env = {
        "HUB_USER_ID": "roc",
        "HUB_USER_ANTHROPIC_BASE_URL": "https://litellm.example",
        "HUB_USER_ANTHROPIC_AUTH_TOKEN": "tok-abc",
        "HUB_USER_GH_TOKEN": "ghp_xyz",
        "HUB_IMAGE_TAG_DEFAULT": "pa-executor:v0.1.3",
        "HUB_DATA_ROOT": "/tmp/pa-hub-data",
    }
    env.update(over)
    return {k: v for k, v in env.items() if v is not None}


def test_reads_all_required_env():
    cfg = config.load_config(_full_env())
    assert cfg.user_id == "roc"
    assert cfg.anthropic_base_url == "https://litellm.example"
    assert cfg.anthropic_auth_token == "tok-abc"
    assert cfg.gh_token == "ghp_xyz"
    assert cfg.image_tag_default == "pa-executor:v0.1.3"
    assert cfg.data_root == config.Path("/tmp/pa-hub-data")


def test_missing_required_lists_all_missing_keys():
    env = _full_env(HUB_USER_GH_TOKEN=None, HUB_IMAGE_TAG_DEFAULT=None, HUB_USER_ID=None)
    with pytest.raises(config.ConfigError) as ei:
        config.load_config(env)
    msg = str(ei.value)
    assert "HUB_USER_ID" in msg and "HUB_USER_GH_TOKEN" in msg and "HUB_IMAGE_TAG_DEFAULT" in msg


def test_missing_env_message_leaks_no_values():
    env = _full_env(HUB_USER_ANTHROPIC_AUTH_TOKEN=None)
    with pytest.raises(config.ConfigError) as ei:
        config.load_config(env)
    assert "tok-abc" not in str(ei.value)  # 凭据绝不进错误消息/日志（spec：凭据不入日志）


def test_model_routing_optional():
    assert config.load_config(_full_env()).user_model is None
    assert config.load_config(_full_env(HUB_USER_MODEL="glm-5.2")).user_model == "glm-5.2"
    assert config.load_config(_full_env(HUB_USER_MODEL="")).user_model is None  # 空串视为未设


def test_max_containers_default_and_override():
    assert config.load_config(_full_env()).max_containers == 3
    assert config.load_config(_full_env(HUB_MAX_CONTAINERS="7")).max_containers == 7


def test_max_containers_invalid_rejected():
    with pytest.raises(config.ConfigError, match="HUB_MAX_CONTAINERS"):
        config.load_config(_full_env(HUB_MAX_CONTAINERS="abc"))
    with pytest.raises(config.ConfigError, match="HUB_MAX_CONTAINERS"):
        config.load_config(_full_env(HUB_MAX_CONTAINERS="0"))


def test_max_retries_default_override_and_zero():
    assert config.load_config(_full_env()).max_retries == 2
    assert config.load_config(_full_env(HUB_MAX_RETRIES="5")).max_retries == 5
    assert config.load_config(_full_env(HUB_MAX_RETRIES="0")).max_retries == 0  # 0=一次失败即放弃
    with pytest.raises(config.ConfigError, match="HUB_MAX_RETRIES"):
        config.load_config(_full_env(HUB_MAX_RETRIES="-1"))


def test_user_env_three_values_plus_model():
    cfg = config.load_config(_full_env(HUB_USER_MODEL="glm-5.2"))
    env = cfg.user_env()
    assert env["ANTHROPIC_BASE_URL"] == "https://litellm.example"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "tok-abc"
    assert env["GH_TOKEN"] == "ghp_xyz"
    assert env["ANTHROPIC_MODEL"] == "glm-5.2"
    # 未设模型路由时不含该键（镜像中立——roc 特定形态运行时注入）
    assert "ANTHROPIC_MODEL" not in config.load_config(_full_env()).user_env()


# ── 通知面 env（add-hub-slice-4a 4.1，spec: hub-notifications）──

def test_notification_env_defaults_closed():
    cfg = config.load_config(_full_env())  # 全不配 = 各能力关闭零行为
    assert cfg.mm_bot_token is None
    assert cfg.project_channels == {}
    assert cfg.image_template_repo is None
    assert cfg.stale_check_ticks == config.DEFAULT_STALE_CHECK_TICKS


def test_notification_env_parsed():
    cfg = config.load_config(_full_env(
        HUB_MM_BOT_TOKEN="bot-tok",
        HUB_MM_PROJECT_CHANNELS="proj-a:chan1, proj-b:chan2",
        HUB_IMAGE_TEMPLATE_REPO="newland/pa-executor",
        HUB_STALE_CHECK_TICKS="10",
    ))
    assert cfg.mm_bot_token == "bot-tok"
    assert cfg.project_channels == {"proj-a": "chan1", "proj-b": "chan2"}
    assert cfg.image_template_repo == "newland/pa-executor"
    assert cfg.stale_check_ticks == 10


def test_stale_check_ticks_invalid_rejected():
    # M1（审核）：0 炸 daemon modulo、负数令检测静默关闭——fail fast 启动拦
    for bad in ("0", "-1", "abc"):
        with pytest.raises(config.ConfigError):
            config.load_config(_full_env(HUB_STALE_CHECK_TICKS=bad))


def test_project_channels_bad_segments_skipped(capsys):
    # 坏段（无冒号/空值）跳过不崩启动，warn 可见（观察面配错不崩、不静默）
    got = config.parse_project_channels("ok:chan1, badseg, :empty, proj:")
    assert got == {"ok": "chan1"}
    assert "坏段" in capsys.readouterr().err
