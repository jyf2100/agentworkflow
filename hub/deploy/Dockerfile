# hub thin 镜像（add-hub-slice-2a 5.1，design D6）——与执行镜像零共享层：
# hub 是控制面常驻物（API + daemon 线程 + docker CLI），执行镜像是目标面即弃载体
# （vault 全投影 + claude runtime）。职责正交，不共用 build context。
# build：docker build -t pa-hub:<tag> hub/ -f hub/deploy/Dockerfile（context = hub/；base 3.11 = pa 全家 Python ≥3.11 口径）
FROM python:3.11-slim

# docker CLI：宿主 daemon 的 socket 客户端（hub 经 docker run 起兄弟执行容器，D1）。
# 注意装 docker-cli 而非 docker.io——Debian trixie 起 docker.io 已拆分**不含 CLI**（实测
# 只装出 docker-init；5.3 真机验证坐实派发报 No such file or directory: 'docker'）
RUN apt-get update \
    && apt-get install -y --no-install-recommends docker-cli \
    && rm -rf /var/lib/apt/lists/*

# 运行依赖（对齐 hub/quality.sh 清单 + uvicorn/cryptography；httpx 2b 起为运行依赖——
# MM OAuth2 出站调用，2a 时期曾标「仅测试」已翻案，见 add-hub-slice-2b D3/D8）
RUN pip install --no-cache-dir fastapi uvicorn cryptography pypinyin httpx

COPY . /opt/hub
WORKDIR /opt/hub

# 运行用户对齐执行镜像 pa(uid=1001)（D5：hub 与执行容器同 uid——state/vault 卷属主一致，
# 拒 777；compose 侧另有 user: "1001:1001" 双保险，此处保证脱离 compose 直跑也安全）
USER 1001:1001

ENTRYPOINT ["python3", "serve.py"]
