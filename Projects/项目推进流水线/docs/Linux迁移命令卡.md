# Linux 迁移命令卡（纯命令）

> 配套 `Linux迁移部署清单.md`。只留可复制命令，按执行顺序。【Mac】= 在 Mac 上跑；【Linux】= SSH 上服务器 `ubuntu@172.32.153.184` 后跑。
>
> 坐标：`HOST=ubuntu@172.32.153.184` · `VAULT=/mnt/disk01/workspaces/worksummary/vault` · `目标仓=/mnt/disk01/workspaces/worksummary/cc-web-control` · proxy=本机:4000

---

## ① 装依赖【Linux】

```bash
sudo apt update
sudo apt install python3 python3-yaml git pass gnupg

curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
. "$HOME/.nvm/nvm.sh"
nvm install 18 && nvm use 18 && nvm alias default 18

# gh CLI（按官方源装好后）
gh auth login                                   # [交互] 写 ~/.config/gh/hosts.yml

gpg --generate-key                              # [交互] 记下打印的 key ID
pass init "<GPG key ID>"                        # 用该 key 初始化密码库
```

## ② 迁配置【Linux】

```bash
# settings.json 从 Mac 拷过来（含 ANTHROPIC_AUTH_TOKEN/BASE_URL/MODEL）后确认能通：
claude -p "ping"

# git push 凭据（目标仓用 gh 的 https helper）
gh auth setup-git

# SMTP 授权码（粘贴与 Mac Keychain 相同的 sina 授权码）
pass insert smtp/sina                           # [交互]
# pass insert smtp/newland                     # 可选

# gpg-agent 缓存（cron 非交互读 pass 前置，必做）
mkdir -p ~/.gnupg && cat >> ~/.gnupg/gpg-agent.conf <<'EOF'
default-cache-ttl 86400
max-cache-ttl 2592000
allow-preset-passphrase
EOF
gpgconf --kill gpg-agent && gpgconf --launch gpg-agent
```

## ③ rsync 数据【Mac】（vault 根跑：`cd /Users/roc/dailywork/日常工作`）

```bash
# 服务器先建父目录并改属主
ssh ubuntu@172.32.153.184 'sudo mkdir -p /mnt/disk01/workspaces/worksummary && sudo chown -R ubuntu:ubuntu /mnt/disk01/workspaces/worksummary'

rsync -av Projects/项目推进流水线/ ubuntu@172.32.153.184:/mnt/disk01/workspaces/worksummary/vault/Projects/项目推进流水线/
rsync -av .project-auto/             ubuntu@172.32.153.184:/mnt/disk01/workspaces/worksummary/vault/.project-auto/
rsync -av .claude/agents/pa-radar.md .claude/agents/pa-prd.md .claude/agents/pa-prd-critic.md \
       ubuntu@172.32.153.184:/mnt/disk01/workspaces/worksummary/vault/.claude/agents/
rsync -av Knowledge/微信/            ubuntu@172.32.153.184:/mnt/disk01/workspaces/worksummary/vault/Knowledge/微信/
# 目标仓：排除 node_modules(276M，Mac native 模块 Linux 不兼容)+ Mac 残留产物；到 Linux 后 npm install 干净装
rsync -av --exclude node_modules/ --exclude .playwright-mcp/ --exclude .superpowers/ --exclude .worktrees/ \
       /Users/roc/workspace/cc-web-control/ ubuntu@172.32.153.184:/mnt/disk01/workspaces/worksummary/cc-web-control/
```

## ④ 目标仓就位【Linux】

```bash
cd /mnt/disk01/workspaces/worksummary/cc-web-control
npm install
git config user.name  "roc (项目推进流水线)"
git config user.email "9880962+jyf2100@users.noreply.github.com"
git remote -v                                   # 确认 origin 在
```

## ⑤ profile 路径 + 自测【Linux】

```bash
# 改 profile repo 第 5 行指向 Linux 目标仓（只改 Linux 这份，Mac 的不动）
sed -i 's#^repo:.*#repo: /mnt/disk01/workspaces/worksummary/cc-web-control#' \
    /mnt/disk01/workspaces/worksummary/vault/.project-auto/profiles/cc-web-control.yaml
grep '^repo:' /mnt/disk01/workspaces/worksummary/vault/.project-auto/profiles/cc-web-control.yaml

cd /mnt/disk01/workspaces/worksummary/vault

python3 Projects/项目推进流水线/scripts/run_daily.py --to-stage critic                                              # ① cheap 段
python3 Projects/项目推进流水线/scripts/run_daily.py --from-stage dispatch --to-stage dispatch --dispatch-limit 1    # ② dispatch smoke
python3 Projects/项目推进流水线/scripts/run_daily.py --from-stage report --to-stage report --stamp <YYYYMMDD>       # ③ report（发 sina）
python3 Projects/项目推进流水线/scripts/smtp_send.py --self-test                                                    # ④ SMTP 自测
```

## ⑥ 装 cron【Linux】

```bash
bash /mnt/disk01/workspaces/worksummary/vault/Projects/项目推进流水线/scripts/install_cron.sh
crontab -l | grep run_daily
# 次日 03:17 看日志：/mnt/disk01/workspaces/worksummary/vault/.project-auto/state/cron.log
# 心跳：run_cron.sh 已 export PA_HEARTBEAT=1 → cron 每天发一封状态邮件（全绿也发，标题带「全绿心跳」）。
#       无头服务器上「今天没收到邮件」= 流水线挂了，ssh 上去查 cron.log。
```

---

## 回滚（Mac）

```bash
cd /Users/roc/dailywork/日常工作
git revert 51c3e82                               # 撤回全部跨平台化代码改动（vault 无 remote，git 历史是唯一依据）
```
