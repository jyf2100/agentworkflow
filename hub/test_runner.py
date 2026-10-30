"""runner.py 单测——ContainerRunner 接缝（add-hub-skeleton 任务 5.2/6.1-6.3，design D5）。

F13 镜像咬合（5.2）+ Protocol/Fake 可编程面（6.1）+ DockerRunner 命令构造（6.2）+ env-file 注入（6.3）。
"""
from pathlib import Path

import runner


def _spec(tag: str, **over) -> runner.RunSpec:
    kw = dict(
        image_tag=tag,
        command=["--from-stage", "inject", "--inject-prd", "/input/prd.md", "--stamp", "20261021"],
        state_root=Path("/data/pa/proj-a/state"),
        project="proj-a",
        prd_input=Path("/data/pa/proj-a/prd/prd.md"),
        profiles_root=Path("/data/pa/proj-a/profiles"),
        sources_file=Path("/data/pa/proj-a/sources.yaml"),
    )
    kw.update(over)
    return runner.RunSpec(**kw)


def test_missing_image_raises_with_tag_no_fallback(tmp_path):
    fake = runner.FakeRunner(images=set())  # fake 镜像清单为空——同判（spec scenario「镜像缺失 fail」）
    try:
        fake.start(_spec("pa-executor:v0.1.3")); raised = False
    except runner.ImageMissingError as e:
        raised = True
        assert "pa-executor:v0.1.3" in str(e)  # 报告缺失 tag
        assert "build" in str(e).lower() or "构建" in str(e)  # build 提示
    assert raised


def test_no_latest_fallback_anywhere():
    # MUST NOT 静默回退 :latest 或任何其他 tag——接缝源码无回退路径
    src = Path(runner.__file__).read_text(encoding="utf-8")
    assert ":latest" not in src and '"latest"' not in src and "'latest'" not in src


def test_fake_catalog_same_verdict_as_docker_inspect_semantics(tmp_path):
    fake = runner.FakeRunner(images={"pa-executor:v0.1.3"})
    assert fake.image_exists("pa-executor:v0.1.3")
    assert not fake.image_exists("pa-executor:v9.9.9")
    ref = fake.start(_spec("pa-executor:v0.1.3"))  # 在清单内：正常起，产出 container_ref
    assert ref and fake.status(ref).state == "running"


# ── 6.1 Protocol + FakeRunner 可编程面 ──────────────────────────────


def test_runspec_carries_full_payload():
    spec = _spec("pa-executor:v0.1.3", env_file=Path("/tmp/e.env"), cpus="3", memory="8g")
    assert spec.image_tag == "pa-executor:v0.1.3"
    assert spec.state_root == Path("/data/pa/proj-a/state")
    assert spec.project == "proj-a"
    assert spec.prd_input.name == "prd.md"
    assert spec.env_file == Path("/tmp/e.env")
    assert spec.cpus == "3" and spec.memory == "8g"


def test_fake_crash_injection_on_start():
    fake = runner.FakeRunner(images={"t:v1"}, crash_on_start=RuntimeError("daemon down"))
    try:
        fake.start(_spec("t:v1")); raised = False
    except RuntimeError as e:
        raised = "daemon down" in str(e)
    assert raised  # 可编程崩溃注入（6.1）


def test_fake_stop_transitions_and_gone_status():
    fake = runner.FakeRunner(images={"t:v1"})
    ref = fake.start(_spec("t:v1"))
    fake.stop(ref)
    assert fake.status(ref).state == "gone"
    ref2 = fake.start(_spec("t:v1"))
    fake.finish(ref2, exit_code=3)
    st = fake.status(ref2)
    assert st.state == "exited" and st.exit_code == 3  # 终态容器 stop 幂等不复活
    fake.stop(ref2)
    assert fake.status(ref2).state == "exited" and fake.status(ref2).exit_code == 3


# ── 6.2 DockerRunner 命令构造（零真 docker 单测）─────────────────────


def test_docker_cmd_pins_mounts_envfile_limits_and_tag():
    dr = runner.DockerRunner()
    spec = _spec("pa-executor:v0.1.3", env_file=Path("/tmp/e.env"), cpus="2", memory="4g")
    argv = dr._docker_cmd(spec)
    join = " ".join(argv)
    assert argv[:3] == ["docker", "run", "-d"]
    assert f"{spec.state_root}:/opt/pa/.project-auto/state" in join              # state 写卷 → 镜像内 pa 期望路径
    assert f"{spec.profiles_root}:/opt/pa/.project-auto/profiles:ro" in join     # inject 校验必读（load_profiles）
    assert f"{spec.sources_file}:/opt/pa/.project-auto/sources.yaml:ro" in join  # _run_pipeline 顶部无条件必读
    assert f"{spec.prd_input}:/input/{spec.prd_input.name}:ro" in join           # PRD 只读输入卷（A2）
    assert "--env-file /tmp/e.env" in join                                    # 与切片 2 同通道（D6）
    assert "--workdir /opt/pa" in join  # vault 根 = run_daily 约定 cwd（persona 相对路径基；#8 实证缺它 critic 读不到 PRD）
    assert "--cpus 2" in join and "--memory 4g" in join
    assert "pa-executor:v0.1.3" in argv
    cmd_at = argv.index("pa-executor:v0.1.3") + 1
    assert argv[cmd_at:] == spec.command  # 纯参数原样接在 tag 后（附加到镜像 ENTRYPOINT=run_daily）
    assert not any(a.startswith("python3") for a in spec.command)  # 入口收敛镜像——hub 不重复指解释器
    # 非 root 是镜像默认（isolated-observable-execution 契约）——hub 不加 --user，仅传参与施加（A5）


def test_docker_cmd_without_env_file_omits_flag():
    argv = " ".join(runner.DockerRunner()._docker_cmd(_spec("t:v1")))
    assert "--env-file" not in argv


def test_docker_image_exists_by_inspect_rc(monkeypatch):
    dr = runner.DockerRunner()
    calls = []
    def fake_run(cmd, **kw):
        calls.append(cmd)
        class P: returncode = 0 if cmd[3] == "pa-executor:v0.1.3" else 1; stdout = ""; stderr = ""
        return P()
    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    assert dr.image_exists("pa-executor:v0.1.3")
    assert not dr.image_exists("pa-executor:v9.9.9")
    assert calls[0][:3] == ["docker", "image", "inspect"]


def test_docker_status_parses_inspect_state(monkeypatch):
    import json as _json
    dr = runner.DockerRunner()
    def fake_run(cmd, **kw):
        payload = {"Running": True, "ExitCode": 0} if cmd[-1] == "c-run" else {"Running": False, "ExitCode": 3}
        class P: returncode = 0; stdout = _json.dumps(payload); stderr = ""
        return P()
    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    assert dr.status("c-run") == runner.ContainerStatus(state="running", exit_code=None)
    assert dr.status("c-done") == runner.ContainerStatus(state="exited", exit_code=3)
    def fake_missing(cmd, **kw):
        class P: returncode = 1; stdout = ""; stderr = "No such object"
        return P()
    monkeypatch.setattr(runner.subprocess, "run", fake_missing)
    assert dr.status("c-gone").state == "gone"


def test_docker_start_missing_image_raises_before_run(monkeypatch):
    dr = runner.DockerRunner()
    monkeypatch.setattr(dr, "image_exists", lambda tag, docker_host=None: False)
    started = []
    monkeypatch.setattr(runner.subprocess, "run", lambda cmd, **kw: started.append(cmd))
    try:
        dr.start(_spec("pa-executor:v9.9.9")); raised = False
    except runner.ImageMissingError:
        raised = True
    assert raised and not started  # 不产出容器：docker run 根本不发起


def test_docker_image_exists_daemon_unreachable_raises_not_missing(monkeypatch):
    """daemon 不可达（socket 权限/daemon down）≠ 镜像缺失——三态精神（UNKNOWN 不当
    NOT_FOUND，spec: fail-safe-dispatch）。5.3 真机坐实：permission denied 曾被误报
    『镜像缺失』进 fail 终态，带偏排障方向。"""
    dr = runner.DockerRunner()
    features = [
        "permission denied while trying to connect to the Docker daemon socket",
        "Cannot connect to the Docker daemon at unix:///var/run/docker.sock",
    ]
    for err in features:
        def fake_run(cmd, *, _err=err, **kw):
            class P: returncode = 1; stdout = ""; stderr = _err
            return P()
        monkeypatch.setattr(runner.subprocess, "run", fake_run)
        try:
            dr.image_exists("pa-executor:v0.1.3")
            raised = None
        except RuntimeError as e:
            raised = e
        assert raised is not None, f"daemon 不可达须抛 RuntimeError：{err!r}"
        assert "不可达" in str(raised)
        assert not isinstance(raised, runner.ImageMissingError)  # 绝不进镜像缺失终态


# ── 6.3 env-file 注入（0600 + 即弃）────────────────────────────────


def test_env_file_0600_content_and_transient():
    with runner.env_file({"ANTHROPIC_BASE_URL": "https://x", "GH_TOKEN": "tok"}) as p:
        assert p.exists()
        assert p.stat().st_mode & 0o777 == 0o600  # 0600（spec：凭据文件权限）
        assert p.read_text(encoding="utf-8") == "ANTHROPIC_BASE_URL=https://x\nGH_TOKEN=tok\n"
        captured = str(p)
    assert not Path(captured).exists()  # 容器即弃后删——退出即删（D6）


# ── 远程 daemon 路由（add-hub-remote-runner 2.2 → add-hub-runner-pool per-call；
#    spec scenario「CLI 调用路由」/「不泄漏进容器」）─────────────────────────


def _capture_cli(monkeypatch):
    """Fake CLI：捕获全部 subprocess.run 的 (cmd, env kwarg) 供四调用点断言。
    docker_host 已是 per-call 参数——不再经构造注入。"""
    dr = runner.DockerRunner()
    calls = []

    def fake_run(cmd, **kw):
        calls.append((cmd, kw.get("env")))
        # stdout 须合法 JSON：status() 会 json.loads(inspect -f 输出)；其余调用点不解析
        class P: returncode = 0; stdout = '{"Running": false, "ExitCode": 0}'; stderr = ""
        return P()

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    return dr, calls


def test_cli_env_absent_without_docker_host(monkeypatch):
    # 不传 = 与父进程等价：env kwarg 为 None（subprocess 继承），不注入任何键
    dr, calls = _capture_cli(monkeypatch)
    dr.image_exists("t:v1"); dr.start(_spec("t:v1")); dr.status("c1"); dr.stop("c1")
    assert calls and all(env is None for _, env in calls)  # 四调用点（stop 内含 status）全不设 env


def test_cli_env_injected_on_all_callsites(monkeypatch):
    # 传入时四调用点 env 均含该值；其余键继承父进程（不漂移）；TLS 键不透传（D5）
    import os
    host = "tcp://172.32.153.183:2375"
    dr, calls = _capture_cli(monkeypatch)
    dr.image_exists("t:v1", docker_host=host); dr.start(_spec("t:v1"), docker_host=host)
    dr.status("c1", docker_host=host); dr.stop("c1", docker_host=host)
    assert calls and all(env is not None for _, env in calls)
    for cmd, env in calls:
        assert env.get("DOCKER_HOST") == host            # 恰一个（dict 键唯一）
        assert env.get("PATH") == os.environ.get("PATH")  # 其余键继承父进程
        assert "DOCKER_TLS_VERIFY" not in env and "DOCKER_CERT_PATH" not in env


def test_cli_env_per_call_routing_same_instance(monkeypatch):
    # 同一 DockerRunner 实例两次调用不同 docker_host 各自注入（池调度前提）：
    # 构造零状态——每次调用按参数路由，互不串扰
    import os
    h1, h2 = "tcp://10.0.0.1:2375", "tcp://10.0.0.2:2375"
    dr, calls = _capture_cli(monkeypatch)
    dr.image_exists("t:v1", docker_host=h1)
    dr.image_exists("t:v1", docker_host=h2)
    dr.image_exists("t:v1")  # 不传 = 本机（None）
    envs = [env for _, env in calls]
    assert envs[0].get("DOCKER_HOST") == h1 and envs[0].get("PATH") == os.environ.get("PATH")
    assert envs[1].get("DOCKER_HOST") == h2
    assert envs[2] is None  # 本机形态 = 不设 env kwarg


def test_run_container_env_face_excludes_docker_host():
    # DOCKER_HOST 只进 CLI 子进程 env；run 容器 --env 面（-e/--env-file）不含它
    dr = runner.DockerRunner()
    argv = dr._docker_cmd(_spec("t:v1", env_file=Path("/tmp/e.env"),
                                container_env=("PA_REPORT_DIR=/x",)))
    join = " ".join(argv)
    assert "DOCKER_HOST" not in join        # 不进 -e 面（pa 容器内进程看不到）
    assert "-e PA_REPORT_DIR=/x" in join    # 非凭据 env 直传照常
    assert "--env-file /tmp/e.env" in join  # 凭据通道照常


def test_fake_runner_records_per_call_docker_host():
    # FakeRunner 同步 per-call 形态：start 记录进容器行、calls 日志可断言路由
    fr = runner.FakeRunner(images={"t:v1"})
    assert fr.image_exists("t:v1", docker_host="tcp://10.0.0.1:2375") is True
    ref = fr.start(_spec("t:v1"), docker_host="tcp://10.0.0.1:2375")
    assert fr.containers[ref]["docker_host"] == "tcp://10.0.0.1:2375"
    fr.status(ref, docker_host="tcp://10.0.0.1:2375"); fr.stop(ref, docker_host=None)
    assert ("image_exists", "tcp://10.0.0.1:2375") in fr.calls
    assert ("start", "tcp://10.0.0.1:2375") in fr.calls
    assert ("status", "tcp://10.0.0.1:2375") in fr.calls and ("stop", None) in fr.calls


# ── real-docker 标记测试（本机有 daemon 且镜像在才跑；冷 checkout 恒 skip）──

_REAL_TAG = "pa-executor:v0.1.3"  # 正式镜像 tag（随 release 演进时同步更新）


def _real_docker_ready() -> bool:
    import shutil
    import subprocess
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(
            ["docker", "image", "inspect", _REAL_TAG], capture_output=True, timeout=30
        ).returncode == 0
    except Exception:
        return False


def test_real_docker_mount_contract(tmp_path):
    """真容器挂载契约收口（切片 1 核心设计值的首证）：inject-to-inject 端到端——
    镜像 ENTRYPOINT 收敛 run_daily（vault 投影 /opt/pa），纯参数经 hub 挂载面
    （state 写卷 + profiles/sources.yaml 只读卷 + PRD /input）驱动 pa 真跑 inject 段，
    manifest 与 PRD 落宿主挂载卷可见。零 LLM、零外呼——纯机械段端到端。"""
    if not _real_docker_ready():
        import pytest
        pytest.skip(f"本机无 docker daemon 或无 {_REAL_TAG} 镜像——真容器契约验证随部署机跑")
    import os
    import subprocess
    import time
    # host 侧 fixtures：per-project 启动必读面（load_sources 无条件读 / inject 校验 profiles）
    host_state = tmp_path / "state"
    host_state.mkdir()
    # 镜像运行用户 pa(uid=1001) 须可写 state 卷——探针放宽 0777；
    # 生产属主对齐（data_root chown 1001 / compose user）是切片 2 compose 部署事项
    os.chmod(host_state, 0o777)
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    os.chmod(profiles, 0o755)
    (profiles / "selfcheck.yaml").write_text("name: selfcheck\nadmission: true\n", encoding="utf-8")
    sources = tmp_path / "sources.yaml"
    sources.write_text("sources: []\n", encoding="utf-8")
    prd = tmp_path / "probe.md"
    prd.write_text(
        "---\nproject: selfcheck\nslug: contract-probe\n---\n\n# 挂载契约探针\n\n纯机械干跑。\n",
        encoding="utf-8",
    )
    dr = runner.DockerRunner()
    spec = runner.RunSpec(
        image_tag=_REAL_TAG,
        command=["--from-stage", "inject", "--to-stage", "inject",
                 "--inject-prd", "/input/probe.md", "--stamp", "20261021"],
        state_root=host_state,
        project="selfcheck",
        prd_input=prd,
        profiles_root=profiles,
        sources_file=sources,
    )
    ref = dr.start(spec)
    for _ in range(120):  # 轮询至终态（≤120s 机械自检）
        st = dr.status(ref)
        if st.state != "running":
            break
        time.sleep(1)
    st = dr.status(ref)
    logs = subprocess.run(["docker", "logs", ref], capture_output=True, text=True, timeout=30)
    assert st.state == "exited" and st.exit_code == 0, logs.stdout + logs.stderr
    out = logs.stdout + logs.stderr
    assert "[inject] ✓" in out                                        # pa inject 段真跑成功
    assert (host_state / "prd_manifest_20261021.json").is_file()      # manifest 落宿主挂载卷
    assert (host_state / "prd" / "selfcheck" / "20261021_contract-probe.md").is_file()
