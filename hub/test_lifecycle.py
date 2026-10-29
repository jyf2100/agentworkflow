"""lifecycle.py 单测——容器生命周期编排（add-hub-skeleton 任务 7.1-7.3，spec: execution-hub「容器生命周期」）。"""
import json
from datetime import datetime, timezone
from pathlib import Path

import config
import db as hub_db
import lifecycle
import notify
import queue_engine as qe
from runner import FakeRunner

_CLOCK = datetime(2026, 10, 21, 3, 0, 0, tzinfo=timezone.utc)


def _env(data_root: Path, **over) -> dict:
    env = {
        "HUB_USER_ID": "roc",
        "HUB_USER_ANTHROPIC_BASE_URL": "https://litellm.example",
        # 长 token：占位符判据（vault_store.placeholder_issues，MIN_TOKEN_LEN=8——内网短 key 放行，见其注释）
        "HUB_USER_ANTHROPIC_AUTH_TOKEN": "tok-abcdef0123456789abcdef",
        "HUB_USER_GH_TOKEN": "ghp_abcdefghij0123456789",
        "HUB_IMAGE_TAG_DEFAULT": "pa-executor:v0.1.3",
        "HUB_DATA_ROOT": str(data_root),
    }
    env.update(over)
    return {k: v for k, v in env.items() if v is not None}


def _boot_inputs(data_root: Path, project: str) -> None:
    """per-project 启动必读面（镜像咬合）：profiles 目录 + sources.yaml——
    容器内 pa 无条件读取（_run_pipeline 顶部 load_sources / inject 校验 profiles）。"""
    profs = data_root / project / "profiles"
    profs.mkdir(parents=True, exist_ok=True)
    (profs / f"{project}.yaml").write_text(f"name: {project}\nadmission: true\n", encoding="utf-8")
    (data_root / project / "sources.yaml").write_text("sources: []\n", encoding="utf-8")


def _mk(tmp_path: Path, images=None, max_retries: int = 2, boot_inputs: bool = True, vault=None, mm=None):
    d = hub_db.HubDB(tmp_path / "h.sqlite3")
    eng = qe.QueueEngine(d, max_containers=2, max_retries=max_retries, image_tag_default="pa-executor:v0.1.3")
    fake = FakeRunner(images=images if images is not None else {"pa-executor:v0.1.3"})
    cfg = config.load_config(_env(tmp_path / "data"))
    if boot_inputs:
        _boot_inputs(tmp_path / "data", "proj-a")
    notifier = notify.Notifier(cfg, mm=mm) if mm is not None else None  # 4a D3：注入式通知（None=关闭）
    lc = lifecycle.Lifecycle(d, eng, fake, cfg, clock=_CLOCK, vault=vault, notifier=notifier)
    return lc, d, fake, tmp_path / "data"


def _prd(tmp_path: Path, name: str = "my-feature.md") -> Path:
    p = tmp_path / name
    p.write_text("# PRD\n", encoding="utf-8")
    return p


def test_dispatch_signs_meta_creates_run_starts_container(tmp_path):
    lc, d, fake, data = _mk(tmp_path)
    prd = _prd(tmp_path)
    qid = lc.engine.enqueue("proj-a", str(prd), "roc")
    assert lc.tick_dispatch() is True
    # F10：起容器前署名 meta，文件在 state 卷 runs/<proj>/<stamp>_<slug>/
    run_dir = data / "proj-a" / "state" / "runs" / "proj-a" / "20261021_my-feature"
    meta = run_dir / "20261021_my-feature.meta.json"
    payload = json.loads(meta.read_text(encoding="utf-8"))
    assert payload["initiator"] == "roc" and payload["queue_item_id"] == qid
    assert payload["image_tag"] == "pa-executor:v0.1.3"
    # run 索引 + 队列项 running + 容器载荷（A2：统一入口 /inject 只读卷 + 预分配 --stamp）
    runs = d.runs_for_item(qid)
    assert len(runs) == 1 and runs[0]["status"] == "running" and runs[0]["container_ref"]
    assert d.get_item(qid)["status"] == "running"
    spec = fake.containers[runs[0]["container_ref"]]["spec"]
    assert "--stamp" in spec.command and "20261021" in spec.command
    assert "/input/my-feature.md" in spec.command and "--from-stage" in spec.command
    assert spec.env_file is not None and spec.env_file.suffix == ".env"  # 与切片 2 同通道（D6）
    assert spec.state_root == data / "proj-a" / "state"
    assert spec.profiles_root == data / "proj-a" / "profiles"      # inject 校验必读卷（镜像咬合）
    assert spec.sources_file == data / "proj-a" / "sources.yaml"   # 启动必读单文件卷


def test_dispatch_cmd_runs_full_second_half_inject_to_report(tmp_path):
    # 段范围钉死（fix-hub-form-parity，spec: execution-hub「执行链段范围」）：
    # cmd 必含相邻对 ("--from-stage","inject") 与 ("--to-stage","report")——report 段
    # 双写产物落 state/reports/ 供面板渲染；--to-stage 缺席则止于 dispatch 默认（回归形态）。
    lc, d, fake, _ = _mk(tmp_path)
    qid = lc.engine.enqueue("proj-a", str(_prd(tmp_path)), "roc")
    assert lc.tick_dispatch() is True
    spec = fake.containers[d.runs_for_item(qid)[0]["container_ref"]]["spec"]
    cmd = spec.command
    for i, tok in enumerate(cmd):
        if tok == "--from-stage":
            assert cmd[i + 1] == "inject"
        if tok == "--to-stage":
            assert cmd[i + 1] == "report"
    assert "--to-stage" in cmd and "--from-stage" in cmd  # 两个 flag 都在（防只断相邻不防缺位）


def test_dispatch_missing_image_fails_item_records_no_container(tmp_path):
    lc, d, fake, _ = _mk(tmp_path, images=set())
    prd = _prd(tmp_path)
    qid = lc.engine.enqueue("proj-a", str(prd), "roc")
    assert lc.tick_dispatch() is False  # 没派成
    item = d.get_item(qid)
    assert item["status"] == "failed" and "pa-executor:v0.1.3" in item["last_error"]  # F13：缺失 fail 报 tag
    assert not fake.containers  # 不产出容器
    run = d.runs_for_item(qid)[0]
    assert run["status"] == "failed_to_start" and run["container_ref"] is None  # A7 粒度


def test_missing_boot_inputs_fails_fast_before_run_created(tmp_path):
    # 镜像咬合 fail fast：缺 profiles/sources.yaml → 容器内 pa 启动必崩（load_sources 无条件读 +
    # inject 校验 profiles）——派发前拦下（终态，与 F13 同级：修环境后重新入队）
    lc, d, fake, _ = _mk(tmp_path, boot_inputs=False)
    qid = lc.engine.enqueue("proj-a", str(_prd(tmp_path)), "roc")
    assert lc.tick_dispatch() is False
    item = d.get_item(qid)
    assert item["status"] == "failed" and "启动必读面缺失" in item["last_error"]
    assert not fake.containers and d.runs_for_item(qid) == []  # 不产 run 行不产容器


def test_reconcile_exit_zero_marks_done_and_run_succeeded(tmp_path):
    lc, d, fake, _ = _mk(tmp_path)
    qid = lc.engine.enqueue("proj-a", str(_prd(tmp_path)), "roc")
    lc.tick_dispatch()
    ref = d.runs_for_item(qid)[0]["container_ref"]
    fake.finish(ref, exit_code=0)
    lc.reconcile_running()
    assert d.get_item(qid)["status"] == "done"
    run = d.runs_for_item(qid)[0]
    assert run["status"] == "succeeded" and run["exit_code"] == 0


def test_nonzero_requeues_same_run_dir_new_container(tmp_path):
    # 7.1：重派上限内续跑 = 同 run 新容器复用卷 state（runs 表 1:N 行同 run_dir）
    lc, d, fake, _ = _mk(tmp_path, max_retries=2)
    qid = lc.engine.enqueue("proj-a", str(_prd(tmp_path)), "roc")
    lc.tick_dispatch()
    r1 = d.runs_for_item(qid)[0]
    fake.finish(r1["container_ref"], exit_code=1)
    lc.reconcile_running()
    item = d.get_item(qid)
    assert item["status"] == "pending" and item["attempt"] == 1  # 回 pending 保留计数，不占 running 名额
    assert d.runs_for_item(qid)[0]["status"] == "failed"
    assert lc.tick_dispatch() is True  # FIFO 重派优先（同项最老）
    runs = d.runs_for_item(qid)
    assert len(runs) == 2 and runs[0]["run_dir"] == runs[1]["run_dir"]  # 同 run 复用卷 state
    fake.finish(runs[1]["container_ref"], exit_code=0)
    lc.reconcile_running()
    assert d.get_item(qid)["status"] == "done"


def test_attempt_limit_separates_item_failed_from_run_failed(tmp_path):
    # 两生命周期分离：run failed 是容器事实（每行都记），item failed 是放弃决定
    lc, d, fake, _ = _mk(tmp_path, max_retries=1)
    qid = lc.engine.enqueue("proj-a", str(_prd(tmp_path)), "roc")
    lc.tick_dispatch()
    ref = d.runs_for_item(qid)[0]["container_ref"]
    fake.finish(ref, exit_code=1)
    lc.reconcile_running()
    item = d.get_item(qid)
    assert item["status"] == "failed" and item["attempt"] == 1  # 达上限即放弃
    assert d.runs_for_item(qid)[0]["status"] == "failed" and d.runs_for_item(qid)[0]["exit_code"] == 1


def test_orphan_meta_marked_failed_to_start_and_kept(tmp_path):
    # 7.2：有 meta 无 journal 的 run 目录 → failed_to_start 索引，meta 保留不清理（F10）
    lc, d, _, data = _mk(tmp_path)
    orphan_dir = data / "proj-a" / "state" / "runs" / "proj-a" / "20260101_old-thing"
    orphan_dir.mkdir(parents=True)
    meta = orphan_dir / "20260101_old-thing.meta.json"
    meta.write_text(json.dumps({"initiator": "roc", "queue_item_id": 999, "image_tag": "t:v0", "written_at": "x"}), encoding="utf-8")
    lc.reconcile_startup()
    archived = [r for r in d.list_all_runs() if r["run_dir"].endswith("20260101_old-thing")]
    assert len(archived) == 1 and archived[0]["status"] == "failed_to_start"
    assert meta.exists()  # 保留不清理
    # 幂等：再跑一次不重复索引
    lc.reconcile_startup()
    assert len([r for r in d.list_all_runs() if r["run_dir"].endswith("20260101_old-thing")]) == 1
    # 平铺 journal 在（容器跑过）→ 非孤儿不归档（pa 布局事实；执行证据查平铺文件非目录内）
    ran_dir = data / "proj-a" / "state" / "runs" / "proj-a" / "20260102_ran-once"
    ran_dir.mkdir(parents=True)
    (ran_dir / "20260102_ran-once.meta.json").write_text(
        json.dumps({"initiator": "roc", "queue_item_id": 998, "image_tag": "t:v0", "written_at": "x"}),
        encoding="utf-8")
    (ran_dir.parent / "20260102_ran-once.journal.jsonl").write_text("{}\n", encoding="utf-8")
    lc.reconcile_startup()
    assert not [r for r in d.list_all_runs() if r["run_dir"].endswith("20260102_ran-once")]


def test_crash_restart_live_container_retracked_until_exit(tmp_path):
    # 7.3：hub 崩溃不杀容器（进程树分离）——新 lifecycle 同 DB 同 runner 续跟踪
    lc1, d, fake, _ = _mk(tmp_path)
    qid = lc1.engine.enqueue("proj-a", str(_prd(tmp_path)), "roc")
    lc1.tick_dispatch()
    ref = d.runs_for_item(qid)[0]["container_ref"]
    # 模拟 hub 崩溃重启：全新 lifecycle（同 DB 同 FakeRunner——容器还活着）
    eng2 = qe.QueueEngine(d, max_containers=2, max_retries=2, image_tag_default="pa-executor:v0.1.3")
    cfg2 = config.load_config(_env(tmp_path / "data"))
    lc2 = lifecycle.Lifecycle(d, eng2, fake, cfg2, clock=_CLOCK)
    lc2.reconcile_startup()
    assert d.runs_for_item(qid)[0]["status"] == "running"  # 在跑：续跟踪不误杀
    assert d.get_item(qid)["status"] == "running"
    fake.finish(ref, exit_code=0)
    lc2.reconcile_running()
    assert d.get_item(qid)["status"] == "done"


def test_crash_restart_gone_container_archived_by_volume_evidence(tmp_path):
    # 已消失：按卷归档终态——有 journal → run failed；无 journal → failed_to_start；均走失败状态机
    lc1, d, fake, data = _mk(tmp_path)
    qid = lc1.engine.enqueue("proj-a", str(_prd(tmp_path)), "roc")
    lc1.tick_dispatch()
    ref = d.runs_for_item(qid)[0]["container_ref"]
    fake.stop(ref)  # 容器消失（如 daemon 清理）
    run_dir = Path(d.runs_for_item(qid)[0]["run_dir"])
    # pa 事实布局：journal 是 runs/<proj>/ 下平铺文件（与 run 目录同名同层），非目录内
    (run_dir.parent / "20261021_my-feature.journal.jsonl").write_text("{}\n", encoding="utf-8")  # 卷上证据：跑过
    eng2 = qe.QueueEngine(d, max_containers=2, max_retries=2, image_tag_default="pa-executor:v0.1.3")
    lc2 = lifecycle.Lifecycle(d, eng2, fake, config.load_config(_env(tmp_path / "data")), clock=_CLOCK)
    lc2.reconcile_startup()
    run = d.runs_for_item(qid)[0]
    assert run["status"] == "failed"  # 有 journal：跑过，终态不明按失败归档（fail-safe）
    assert d.get_item(qid)["status"] == "pending"  # 走重派状态机


def test_crash_restart_gone_without_journal_is_failed_to_start(tmp_path):
    lc1, d, fake, _ = _mk(tmp_path)
    qid = lc1.engine.enqueue("proj-a", str(_prd(tmp_path)), "roc")
    lc1.tick_dispatch()
    fake.stop(d.runs_for_item(qid)[0]["container_ref"])
    lc1.reconcile_running()
    run = d.runs_for_item(qid)[0]
    assert run["status"] == "failed_to_start"  # 无 journal：容器从未跑成（A7 粒度）
    assert d.get_item(qid)["status"] == "pending"


def test_daemon_failure_records_once_per_retry_with_backoff(tmp_path):
    # B2 退避：起容器 Exception（daemon 故障）→ run 记账 failed_to_start、item 留 pending
    # （区别 F13 终态）；连续失败进冷却——冷却 tick 不重复记账，冷却结束再试
    d = hub_db.HubDB(tmp_path / "h.sqlite3")
    eng = qe.QueueEngine(d, max_containers=2, max_retries=2, image_tag_default="pa-executor:v0.1.3")
    fake = FakeRunner(images={"pa-executor:v0.1.3"}, crash_on_start=RuntimeError("docker daemon down"))
    cfg = config.load_config(_env(tmp_path / "data"))
    lc = lifecycle.Lifecycle(d, eng, fake, cfg, clock=_CLOCK, dispatch_backoff=2)
    _boot_inputs(tmp_path / "data", "proj-a")
    qid = lc.engine.enqueue("proj-a", str(_prd(tmp_path)), "roc")

    assert lc.tick_dispatch() is False  # 第 1 次尝试：记账 1 行，不放弃
    assert len(d.runs_for_item(qid)) == 1
    assert d.runs_for_item(qid)[0]["status"] == "failed_to_start"
    assert d.get_item(qid)["status"] == "pending"
    assert lc.tick_dispatch() is False and lc.tick_dispatch() is False  # 冷却 2 tick：不记账
    assert len(d.runs_for_item(qid)) == 1
    assert lc.tick_dispatch() is False  # 冷却毕：重试（第 2 行记账）
    assert len(d.runs_for_item(qid)) == 2

    fake.crash_on_start = None  # daemon 恢复：tick4 失败再入冷却，耗尽后派发成功
    assert lc.tick_dispatch() is False and lc.tick_dispatch() is False
    assert lc.tick_dispatch() is True
    assert len(d.runs_for_item(qid)) == 3 and d.runs_for_item(qid)[2]["status"] == "running"


def test_retry_dispatch_preserves_first_meta_written_at(tmp_path):
    # B2 首写保真（lifecycle 面）：daemon 故障重试再成功，meta 仍是首派署名时刻
    d = hub_db.HubDB(tmp_path / "h.sqlite3")
    eng = qe.QueueEngine(d, max_containers=2, max_retries=2, image_tag_default="pa-executor:v0.1.3")
    fake = FakeRunner(images={"pa-executor:v0.1.3"}, crash_on_start=RuntimeError("down"))
    cfg = config.load_config(_env(tmp_path / "data"))
    _boot_inputs(tmp_path / "data", "proj-a")
    t1 = datetime(2026, 10, 21, 3, 0, 0, tzinfo=timezone.utc)
    lc1 = lifecycle.Lifecycle(d, eng, fake, cfg, clock=t1)
    lc1.engine.enqueue("proj-a", str(_prd(tmp_path)), "roc")
    assert lc1.tick_dispatch() is False  # 失败 tick：meta 已署名（written_at = t1）

    fake.crash_on_start = None
    t2 = datetime(2026, 10, 21, 9, 30, 0, tzinfo=timezone.utc)
    lc2 = lifecycle.Lifecycle(d, eng, fake, cfg, clock=t2)  # 新实例（hub 重启形态），重派同 item
    assert lc2.tick_dispatch() is True
    meta = (tmp_path / "data" / "proj-a" / "state" / "runs" / "proj-a"
            / "20261021_my-feature" / "20261021_my-feature.meta.json")
    assert json.loads(meta.read_text(encoding="utf-8"))["written_at"] == "2026-10-21T03:00:00+00:00"


def test_project_path_injection_rejected_no_write(tmp_path):
    lc, d, fake, data = _mk(tmp_path)
    prd = _prd(tmp_path)
    qid = lc.engine.enqueue("../evil", str(prd), "roc")
    assert lc.tick_dispatch() is False
    assert d.get_item(qid)["status"] == "failed"  # 防御性 fail（入队面 API 层另有校验）
    assert not (tmp_path / "data" / "..").joinpath("evil").exists()  # 无越界写
    assert not fake.containers


# ── add-hub-slice-2a：凭据来源 vault 优先 + env 兜底（D3，spec「单用户凭据来源」）──

_VAULT_ENV = {
    "ANTHROPIC_BASE_URL": "https://vault-litellm.example",
    "ANTHROPIC_AUTH_TOKEN": "tok-from-vault-0123456789abcdef",
    "GH_TOKEN": "ghp-from-vault-0123456789",
}


def _vault(tmp_path: Path, records: dict | None = None):
    from cryptography.fernet import Fernet

    import vault_store as vs

    v = vs.CredentialVault(tmp_path / "data", Fernet.generate_key())
    for user, env in (records or {}).items():
        v.set_user(user, env)
    return v


def test_credentials_vault_wins_over_env(tmp_path):
    lc, d, fake, data = _mk(tmp_path, vault=_vault(tmp_path, {"roc": _VAULT_ENV}))
    env, source = lc._credentials()
    assert source == "vault" and env == _VAULT_ENV  # 命中保险箱，不经 hub 进程 env
    assert lc.cfg.user_model is None or "ANTHROPIC_MODEL" not in env  # 未配路由 = 零键混入


def test_credentials_vault_merges_user_model(tmp_path):
    """组5.1 hub 全链实证修复：vault 有记录（无模型）时 HUB_USER_MODEL→ANTHROPIC_MODEL
    仍须注入——vault payload 只存凭据面，模型路由（非凭据）不 merge 则容器内 claude 落
    CLI 内置默认模型（claude-opus-5），roc 代理 400 拒裸 Anthropic id。"""
    lc, d, fake, data = _mk(tmp_path, vault=_vault(tmp_path, {"roc": _VAULT_ENV}))
    import dataclasses

    lc.cfg = dataclasses.replace(lc.cfg, user_model="glm-5.3")  # HubConfig frozen，整体替换
    env, source = lc._credentials()
    assert source == "vault"
    assert env["ANTHROPIC_MODEL"] == "glm-5.3"  # 记录无模型 → hub 级缺省兜底注入（F9）
    assert env["GH_TOKEN"] == _VAULT_ENV["GH_TOKEN"]  # 凭据面原样（非替换式 merge）


def test_credentials_vault_model_wins_over_user_model(tmp_path):
    """2026-10-27 #56 回归锚：面板凭据页存的 ANTHROPIC_MODEL（用户显式选择）优先于
    hub 级 HUB_USER_MODEL 缺省——恒覆盖会让面板 model 框形同虚设。"""
    lc, d, fake, data = _mk(tmp_path, vault=_vault(tmp_path, {
        "roc": {**_VAULT_ENV, "ANTHROPIC_MODEL": "qwen3.8-27b"}}))
    import dataclasses

    lc.cfg = dataclasses.replace(lc.cfg, user_model="glm-5.3")
    env, source = lc._credentials()
    assert source == "vault" and env["ANTHROPIC_MODEL"] == "qwen3.8-27b"


def test_credentials_fallback_env_with_warn(tmp_path, capsys):
    lc, d, fake, data = _mk(tmp_path, vault=_vault(tmp_path))  # 保险箱无 roc 记录
    env, source = lc._credentials()
    assert source == "env" and env["GH_TOKEN"] == "ghp_abcdefghij0123456789"  # cfg.user_env() 兜底（切片 1 形态）
    assert "降级" in capsys.readouterr().err  # warn 可见可追溯


# ── 占位符形态检测（2026-10-23 首跑 #3 实证：hub.env 模板值未换真值）──
def test_credentials_rejects_placeholder_env(tmp_path, capsys):
    """env 分支：占位 token/host（hub.env 模板事故形态）→ (None, 报占位符) 不注入。"""
    import dataclasses

    lc, d, fake, data = _mk(tmp_path, vault=_vault(tmp_path))  # 无 vault 记录 → 降级 env
    lc.cfg = dataclasses.replace(lc.cfg, anthropic_base_url="https://x",
                                 anthropic_auth_token="t", gh_token="g")
    env, msg = lc._credentials()
    assert env is None and "占位符" in msg and "ANTHROPIC_BASE_URL" in msg and "GH_TOKEN" in msg
    qid = lc.engine.enqueue("proj-a", str(_prd(tmp_path)), "roc")
    assert lc.tick_dispatch() is False
    assert d.get_item(qid)["status"] == "failed"  # 派发前 fail-fast，不烧容器
    assert not fake.containers


def test_credentials_rejects_placeholder_vault_record(tmp_path):
    """vault 分支：保险箱记录本身是占位符 → 同样拒绝（录入口漏拦的兜底）。"""
    bad_rec = {"ANTHROPIC_BASE_URL": "https://litellm.example",
               "ANTHROPIC_AUTH_TOKEN": "<roc 的 ANTHROPIC_AUTH_TOKEN>",  # 模板尖括号形态
               "GH_TOKEN": "ghp-real-0123456789abcdef"}
    lc, d, fake, data = _mk(tmp_path, vault=_vault(tmp_path, {"roc": bad_rec}))
    env, msg = lc._credentials()
    assert env is None and "占位符" in msg and "ANTHROPIC_AUTH_TOKEN" in msg


def test_credentials_both_missing_fails_dispatch(tmp_path):
    import dataclasses

    empty = _vault(tmp_path)  # 保险箱无记录
    lc, d, fake, data = _mk(tmp_path, vault=empty)
    # dataclass 绕过 load_config 硬校验构造空三值（守卫未来 env 键可选化形态）
    lc.cfg = dataclasses.replace(lc.cfg, anthropic_base_url="", anthropic_auth_token="", gh_token="")
    env, msg = lc._credentials()
    assert env is None and "ANTHROPIC_BASE_URL" in msg and "GH_TOKEN" in msg  # 列出缺失项
    qid = lc.engine.enqueue("proj-a", str(_prd(tmp_path)), "roc")
    assert lc.tick_dispatch() is False
    assert d.get_item(qid)["status"] == "failed"  # 明确报错，不静默空值注入
    assert not fake.containers


# ── add-forge-adapter 3.2：必需键按项目 profiles 的 forge 声明动态计算 ──
_GITLAB_VAULT_ENV = {
    "ANTHROPIC_BASE_URL": "https://litellm.example",
    "ANTHROPIC_AUTH_TOKEN": "tok-from-vault-0123456789abcdef",
    "GITLAB_TOKEN": "glpat-from-vault-0123456789",   # 无 GH_TOKEN（gitlab-only 用户）
}


def _declare_forge(data: Path, project: str, forge: str) -> None:
    """profile 追加 forge 声明行（projects.py 落盘的同款 yaml 形态）。"""
    p = data / project / "profiles" / f"{project}.yaml"
    p.write_text(p.read_text(encoding="utf-8") + f'forge: "{forge}"\n', encoding="utf-8")


def test_credentials_gitlab_project_missing_token_abandons(tmp_path):
    """gitlab 项目 + 记录只有 GH_TOKEN → 缺 GITLAB_TOKEN：派发前 fail-fast abandon 明示，
    不烧容器（同批 github 项目不受影响——必需键按项目独立计算）。"""
    lc, d, fake, data = _mk(tmp_path, vault=_vault(tmp_path, {"roc": _VAULT_ENV}))
    _declare_forge(data, "proj-a", "gitlab")
    env, msg = lc._credentials("roc", project="proj-a")
    assert env is None and "GITLAB_TOKEN" in msg
    qid = lc.engine.enqueue("proj-a", str(_prd(tmp_path)), "roc")
    assert lc.tick_dispatch() is False
    assert d.get_item(qid)["status"] == "failed"
    assert "GITLAB_TOKEN" in str(d.get_item(qid))   # abandon 消息列缺失键
    assert not fake.containers


def test_credentials_gitlab_only_user_without_gh_token_passes(tmp_path):
    """gitlab-only 用户（无 GH_TOKEN）派发 gitlab 项目 → 凭据过检（不被迫造假 GH_TOKEN）。"""
    lc, d, fake, data = _mk(tmp_path, vault=_vault(tmp_path, {"roc": _GITLAB_VAULT_ENV}))
    _declare_forge(data, "proj-a", "gitlab")
    env, source = lc._credentials("roc", project="proj-a")
    assert env is not None and source == "vault"
    assert "GITLAB_TOKEN" in env and "GH_TOKEN" not in env
    lc.engine.enqueue("proj-a", str(_prd(tmp_path)), "roc")
    assert lc.tick_dispatch() is True   # 凭据面通过，容器正常起（GH_TOKEN 缺席不拦）


def test_credentials_github_project_still_requires_gh_token(tmp_path):
    """无 forge 声明项目（github 缺省）行为零变化：缺 GH_TOKEN 仍 abandon（回归锚）。"""
    lc, d, fake, data = _mk(tmp_path, vault=_vault(tmp_path, {"roc": _GITLAB_VAULT_ENV}))
    env, msg = lc._credentials("roc", project="proj-a")   # profile 未声明 forge
    assert env is None and "GH_TOKEN" in msg


def test_credentials_illegal_forge_declaration_abandons(tmp_path):
    """profile 写非法 forge 值（gitee）→ (None, 声明非法)——不静默回落 github。"""
    lc, d, fake, data = _mk(tmp_path, vault=_vault(tmp_path, {"roc": _VAULT_ENV}))
    _declare_forge(data, "proj-a", "gitee")
    env, msg = lc._credentials("roc", project="proj-a")
    assert env is None and "非法 forge" in msg and "gitee" in msg
    qid = lc.engine.enqueue("proj-a", str(_prd(tmp_path)), "roc")
    assert lc.tick_dispatch() is False
    assert d.get_item(qid)["status"] == "failed" and not fake.containers


def test_dispatch_env_file_content_from_vault(tmp_path, monkeypatch):
    lc, d, fake, data = _mk(tmp_path, vault=_vault(tmp_path, {"roc": _VAULT_ENV}))
    lc.engine.enqueue("proj-a", str(_prd(tmp_path)), "roc")
    captured: dict = {}
    orig = lifecycle.env_file
    monkeypatch.setattr(lifecycle, "env_file", lambda env: (captured.update(env), orig(env))[1])
    assert lc.tick_dispatch() is True
    assert captured == _VAULT_ENV  # env-file 写入内容实证来自保险箱


# ── 3.3 派发链贯通（2b D4/D5：会话用户入队 → vault 按 mm_user_id 解密 + meta initiator=username）──

_MM_ID = "mmid-xyz"


def _capture_env(monkeypatch) -> dict:
    captured: dict = {}
    orig = lifecycle.env_file
    monkeypatch.setattr(lifecycle, "env_file", lambda env: (captured.update(env), orig(env))[1])
    return captured


def _meta_payload(data: Path, project: str = "proj-a") -> dict:
    meta = data / project / "state" / "runs" / project / "20261021_my-feature" / "20261021_my-feature.meta.json"
    return json.loads(meta.read_text(encoding="utf-8"))


def test_dispatch_session_user_vault_and_meta_initiator(tmp_path, monkeypatch):
    # 会话用户入队（enqueued_by=mm_user_id、initiator=username）→ 凭据按 mm_user_id
    # 查保险箱（该用户登录后自录的记录），meta initiator=username（人读名）
    lc, d, fake, data = _mk(tmp_path, vault=_vault(tmp_path, {_MM_ID: _VAULT_ENV}))
    lc.engine.enqueue("proj-a", str(_prd(tmp_path)), _MM_ID, initiator="zhang3")
    captured = _capture_env(monkeypatch)
    assert lc.tick_dispatch() is True
    assert captured == _VAULT_ENV  # 解密的是 mm_user_id 的记录，不是 cfg.user_id
    assert _meta_payload(data)["initiator"] == "zhang3"


def test_dispatch_session_user_no_vault_record_falls_back_env(tmp_path, monkeypatch, capsys):
    # 该用户未录凭据（vault 无记录）→ 降级 hub 进程 env + warn 点名 user（可追溯）
    lc, d, fake, data = _mk(tmp_path, vault=_vault(tmp_path, {}))  # 空保险箱
    lc.engine.enqueue("proj-a", str(_prd(tmp_path)), _MM_ID, initiator="zhang3")
    captured = _capture_env(monkeypatch)
    assert lc.tick_dispatch() is True
    assert captured == lc.cfg.user_env()  # env 兜底（2a 同通道）
    assert _MM_ID in capsys.readouterr().err  # warn 点名降级用户


def test_dispatch_meta_initiator_falls_back_to_enqueued_by(tmp_path):
    # 无 initiator（2a 形态入队/CLI）→ meta initiator=enqueued_by（D4 回落，非 cfg.user_id）
    lc, d, fake, data = _mk(tmp_path)
    lc.engine.enqueue("proj-a", str(_prd(tmp_path)), "cli-user")
    assert lc.tick_dispatch() is True
    assert _meta_payload(data)["initiator"] == "cli-user"


# ── add-hub-slice-4a 2.6：run 终态通知（D3 挂点 _finish_run；FakeRunner 全离线）──

_MM_ID_A = "a1b2c3d4e5f6a1b2c3d4e5f6a1"  # 26-hex（值形态判据）


def test_reconcile_exit_zero_notifies_succeeded(tmp_path):
    # exited 0 → succeeded 私信发起人（username "roc" 解析路径，消息含 run 标识与终态）
    mm = notify.FakeMMClient(user_ids={"roc": _MM_ID_A})
    lc, d, fake, _ = _mk(tmp_path, mm=mm)
    qid = lc.engine.enqueue("proj-a", str(_prd(tmp_path)), "roc")
    lc.tick_dispatch()
    fake.finish(d.runs_for_item(qid)[0]["container_ref"], exit_code=0)
    lc.reconcile_running()
    assert len(mm.dms) == 1
    recipient, text = mm.dms[0]
    assert recipient == _MM_ID_A  # username 经解析后直发
    assert "proj-a" in text and "succeeded" in text and "exit 0" in text
    assert d.get_item(qid)["status"] == "done"  # 通知不扰动状态机
    lc.reconcile_running()  # 二次对账：终态行已离开 running 扫描集——至多一次（不重发）
    assert len(mm.dms) == 1


def test_reconcile_exit_nonzero_notifies_failed_with_channel(tmp_path):
    # 6.x 归属路由：exited 非零 → 绑定频道含提及（不重复私信）；未绑定 → 私信
    mm = notify.FakeMMClient(user_ids={"roc": _MM_ID_A})
    lc, d, fake, _ = _mk(tmp_path, mm=mm)
    lc.notifier = notify.Notifier(lc.cfg, mm=mm, database=d)  # 绑定表路由（env 映射废止）
    d.upsert_bind("proj-a", "chan-1", "test")
    qid = lc.engine.enqueue("proj-a", str(_prd(tmp_path)), "roc")
    lc.tick_dispatch()
    fake.finish(d.runs_for_item(qid)[0]["container_ref"], exit_code=1)
    lc.reconcile_running()
    assert mm.dms == []                                   # 绑定 → 不重复私信
    assert len(mm.channels) == 1 and mm.channels[0][0] == "chan-1"
    t = mm.channels[0][1]
    assert t.startswith("<@") and "failed" in t and "exit 1" in t  # 提及 + 失败事实


def test_image_missing_notifies_failed_to_start(tmp_path):
    # ImageMissingError → failed_to_start 通知（消息含缺失 tag，人可定位）+ item 终态 failed
    mm = notify.FakeMMClient(user_ids={"roc": _MM_ID_A})
    lc, d, fake, _ = _mk(tmp_path, images=set(), mm=mm)
    qid = lc.engine.enqueue("proj-a", str(_prd(tmp_path)), "roc")
    assert lc.tick_dispatch() is False
    assert len(mm.dms) == 1
    text = mm.dms[0][1]
    assert "failed_to_start" in text and "pa-executor:v0.1.3" in text
    assert d.get_item(qid)["status"] == "failed"  # F13 终态不受通知影响



# ── add-pipeline-outcome-observability 2.1：_finish_run 读 outcome → 通知 lead 业务摘要 ──
def test_reconcile_with_outcome_notifies_business_summary(tmp_path):
    # outcome 文件在手（state 卷根 pipeline_outcome_<stamp>.json）→ 通知 lead 业务终态
    # 计数 + PR 行，容器退出码降附录；状态机不扰动（done 照落）
    mm = notify.FakeMMClient(user_ids={"roc": _MM_ID_A})
    lc, d, fake, data = _mk(tmp_path, mm=mm)
    qid = lc.engine.enqueue("proj-a", str(_prd(tmp_path)), "roc")
    lc.tick_dispatch()
    run = d.runs_for_item(qid)[0]
    stamp = Path(run["run_dir"]).name.split("_", 1)[0]
    (data / "proj-a" / "state" / f"pipeline_outcome_{stamp}.json").write_text(json.dumps({
        "schema": "pa.pipeline_outcome.v1",
        "summary": {"green": 0, "red": 1, "invalid": 0, "blocked": 0, "abnormal": 0},
        "prs": [{"project": "proj-a", "status": "pr_open", "pr_number": 60,
                 "verify_verdict": "red", "reason_headline": "e2e exit 1"}],
        "container": {"exit_code": 0}}), encoding="utf-8")
    fake.finish(run["container_ref"], exit_code=0)
    lc.reconcile_running()
    text = mm.dms[0][1]
    assert "业务终态：🟢0 🔴1" in text and "🔴 PR #60：e2e exit 1" in text
    assert "容器 exit 0" in text                      # 退出码附录（非 lead）
    assert d.get_item(qid)["status"] == "done"


def test_reconcile_missing_outcome_fallback_logged(tmp_path, capsys):
    # 无 outcome 文件（旧镜像/中断 run 常态）→ 回退 exit-code 文本 + stderr 留痕不猜
    mm = notify.FakeMMClient(user_ids={"roc": _MM_ID_A})
    lc, d, fake, _ = _mk(tmp_path, mm=mm)
    qid = lc.engine.enqueue("proj-a", str(_prd(tmp_path)), "roc")
    lc.tick_dispatch()
    fake.finish(d.runs_for_item(qid)[0]["container_ref"], exit_code=0)
    lc.reconcile_running()
    assert "→ succeeded，exit 0" in mm.dms[0][1]      # 既有回退文本回归锁
    assert "无 pipeline_outcome" in capsys.readouterr().err
