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
