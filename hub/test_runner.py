"""runner.py 单测——ContainerRunner 接缝（add-hub-skeleton 任务 5.2/6.1-6.3，design D5）。

F13 镜像咬合（5.2）+ Protocol/Fake 可编程面（6.1）+ DockerRunner 命令构造（6.2）+ env-file 注入（6.3）。
"""
from pathlib import Path

import runner


def _spec(tag: str, **over) -> runner.RunSpec:
    kw = dict(
        image_tag=tag,
        command=[
            "python3", "/workspace/vault/Projects/项目推进流水线/scripts/run_daily.py",
            "--from-stage", "inject", "--inject-prd", "/input/prd.md", "--stamp", "20261021",
        ],
        state_root=Path("/data/pa/proj-a/state"),
        project="proj-a",
        prd_input=Path("/data/pa/proj-a/prd/prd.md"),
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
    assert f"{spec.state_root}:/workspace/vault/.project-auto/state" in join  # state 卷 → 镜像内 pa 期望路径
    assert f"{spec.prd_input}:/input/{spec.prd_input.name}:ro" in join       # PRD 只读输入卷（A2）
    assert "--env-file /tmp/e.env" in join                                    # 与切片 2 同通道（D6）
    assert "--cpus 2" in join and "--memory 4g" in join
    assert "pa-executor:v0.1.3" in argv
    cmd_at = argv.index("pa-executor:v0.1.3") + 1
    assert argv[cmd_at:] == spec.command  # 容器命令原样接在 tag 后
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
    monkeypatch.setattr(dr, "image_exists", lambda tag: False)
    started = []
    monkeypatch.setattr(runner.subprocess, "run", lambda cmd, **kw: started.append(cmd))
    try:
        dr.start(_spec("pa-executor:v9.9.9")); raised = False
    except runner.ImageMissingError:
        raised = True
    assert raised and not started  # 不产出容器：docker run 根本不发起


# ── 6.3 env-file 注入（0600 + 即弃）────────────────────────────────


def test_env_file_0600_content_and_transient():
    with runner.env_file({"ANTHROPIC_BASE_URL": "https://x", "GH_TOKEN": "tok"}) as p:
        assert p.exists()
        assert p.stat().st_mode & 0o777 == 0o600  # 0600（spec：凭据文件权限）
        assert p.read_text(encoding="utf-8") == "ANTHROPIC_BASE_URL=https://x\nGH_TOKEN=tok\n"
        captured = str(p)
    assert not Path(captured).exists()  # 容器即弃后删——退出即删（D6）


# ── real-docker 标记测试（本机有 daemon 且镜像在才跑；冷 checkout 恒 skip）──


def _real_docker_ready() -> bool:
    import shutil
    import subprocess
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(
            ["docker", "image", "inspect", "pa-executor:ci"], capture_output=True, timeout=30
        ).returncode == 0
    except Exception:
        return False


def test_real_docker_mechanical_selfcheck():
    if not _real_docker_ready():
        import pytest
        pytest.skip("本机无 docker daemon 或无 pa-executor:ci 镜像——真容器自检随切片 3 部署机跑")
    dr = runner.DockerRunner()
    spec = runner.RunSpec(
        image_tag="pa-executor:ci",
        command=["python3", "-c", "print('mechanical-ok')"],  # 机械自检段：零 LLM、零外呼
        state_root=Path("/tmp/hub-real-docker-state"),
        project="selfcheck",
    )
    ref = dr.start(spec)
    import time
    for _ in range(120):  # 轮询至终态（≤120s 机械自检）
        st = dr.status(ref)
        if st.state != "running":
            break
        time.sleep(1)
    assert dr.status(ref).state == "exited" and dr.status(ref).exit_code == 0
