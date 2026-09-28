# 天玑 · 售后看板 —— 上线部署手册（路线 B：Oracle Cloud 永久免费机器 + Caddy + systemd）

> 目标：把本机跑的 `bi_server.py` 搬成一台 7×24 常驻的线上服务；
> 全链路 HTTPS；密钥只存在服务器；前端永远拿不到 AI key。

---

## 0. 最终架构

```
同事浏览器
   │ HTTPS（自动证书）
   ▼
Caddy（80/443，自动 TLS + 安全头）
   │ 反向代理到 127.0.0.1:8899
   ▼
bi_server.py（只绑本地回环，永不直接暴露公网）
   ├─ 持有 AI 密钥 → 调 Agnes
   ├─ 调天枢 CRM
   └─ 调飞书云盘
```

关键点：**后端只绑 `127.0.0.1`**，公网只能通过 Caddy 进来；AI 密钥在 `.env` 里，只有服务进程读得到。

---

## 1. 开机器（0 成本）

1. 注册 Oracle Cloud（Always Free）。
2. 新建实例：**Shape = Ampere A1 (ARM)**，1~4 OCPU / 6~24GB（免费额度内），系统选 **Ubuntu 22.04**。
3. 下载 SSH 私钥，记下公网 IP。

> 免费额度是 4 OCPU / 24GB / 200GB 存储的 ARM 资源池，跑这个服务绰绰有余。
> 若 Oracle 注册受阻，备选：Fly.io（小额度）、公司闲置旧电脑/树莓派 + Cloudflare Tunnel。

## 2. 放行端口

- **VCN 安全列表**（云控制台）：入站放行 `22`(SSH)、`80`、`443`；其余全关。
- **机器本机防火墙**（Ubuntu 默认可能开了 iptables）：
  ```bash
  sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
  sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
  sudo netfilter-persistent save
  ```
  （若用 ufw：`sudo ufw allow 80,443/tcp`）

## 3. 装依赖

```bash
sudo apt update && sudo apt install -y python3 rsync
# Caddy 官方源
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy
```

> 本项目 **只用 Python 标准库**，无需 pip 安装任何包（见 `requirements.txt`）。

## 4. 建目录、传代码

```bash
sudo useradd -r -m -d /home/tianji -s /usr/sbin/nologin tianji || true
sudo mkdir -p /opt/tianji-bi && sudo chown -R tianji:tianji /opt/tianji-bi
# 从你本机同步（换成你的 IP / 密钥）
rsync -avz -e "ssh -i ~/.ssh/oracle.key" \
  "客户续费BI看板/" ubuntu@<服务器IP>:/tmp/tianji-bi/
ssh -i ~/.ssh/oracle.key ubuntu@<服务器IP> \
  "sudo cp -r /tmp/tianji-bi/* /opt/tianji-bi/ && sudo chown -R tianji:tianji /opt/tianji-bi"
```

**必须带上**的数据文件（否则看板没数据）：`assets/`（含 `data.js`）、`index.html`、`tianji.html`、`*.json` 数据、`归档记录.json`、`生成结果/`、`批量打包/`。

## 5. 配置 .env（关键：密钥只在这里）

```bash
sudo cp /opt/tianji-bi/deploy/.env.example /opt/tianji-bi/.env
sudo nano /opt/tianji-bi/.env      # 填 AI_API_KEY 等
sudo chmod 600 /opt/tianji-bi/.env
sudo chown tianji:tianji /opt/tianji-bi/.env
```

`BI_COOKIE_SECURE=1`（HTTPS）、`BI_ALLOWED_ORIGIN` 留空（同源部署最安全）。

## 6. 起服务

```bash
sudo cp /opt/tianji-bi/deploy/tianji-bi.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tianji-bi
systemctl status tianji-bi
curl -I http://127.0.0.1:8899/tianji.html     # 本地应返回 200
```

## 7. 配 HTTPS

1. 域名 A 记录指向服务器 IP（没有域名可先用 `Caddyfile` 里注释的自签方案）。
2. ```bash
   sudo cp /opt/tianji-bi/deploy/Caddyfile /etc/caddy/Caddyfile
   sudo nano /etc/caddy/Caddyfile     # 把 bi.example.com 换成你的域名
   sudo systemctl reload caddy
   ```
3. 浏览器打开 `https://你的域名/tianji.html`，证书应为 Let's Encrypt 自动签发。

## 8. 验收清单

- [ ] `https://域名/tianji.html` 正常打开、能登录（天枢账号 + 短信码）
- [ ] `curl http://<公网IP>:8899/` **连不上**（后端不暴露公网 ✅）
- [ ] 页面上抓不到任何 `sk-` 开头的密钥
- [ ] 连续输错密码 8 次会被锁 15 分钟
- [ ] 服务重启后登录态仍在（会话已落 SQLite）
- [ ] `journalctl -u tianji-bi` 里错误有完整堆栈，但**前端看不到堆栈**

## 9. 备份（强烈建议）

```bash
sudo crontab -e -u tianji
# 每天 03:30 打包数据（含客户数据，注意存放安全）
30 3 * * * cd /opt/tianji-bi && tar czf /var/backups/tianji-$(date +\%F).tgz assets 归档记录.json sessions.db 生成结果 批量打包 && find /var/backups -name 'tianji-*.tgz' -mtime +14 -delete
```

## 10. 日常更新

```bash
rsync -avz -e "ssh -i ~/.ssh/oracle.key" "客户续费BI看板/" ubuntu@<IP>:/tmp/up/
ssh -i ~/.ssh/oracle.key ubuntu@<IP> "sudo rsync -a --exclude='.env' --exclude='sessions.db' /tmp/up/ /opt/tianji-bi/ && sudo chown -R tianji:tianji /opt/tianji-bi && sudo systemctl restart tianji-bi"
```

---

## ⚠️ 上线前必办的一件事：飞书上传依赖

现在 `/api/fix`、`/api/batch` 生成后是调 **`lark-cli` 这个本地二进制**上传到飞书云盘的。它随 Loomy 客户端装在 macOS 上，**Linux 服务器上并没有**。两条路：

1. **简单**：在服务器上装 lark-cli 的 Linux 版本（若有），并在 `.env` 里设 `LARK_CLI=/usr/local/bin/lark-cli`；同时把飞书登录态/密钥放到服务器（注意权限）。
2. **干净（推荐）**：把上传逻辑从"调 CLI"改成"直接调飞书 OpenAPI"（你已有自建应用的 App ID/Secret）。这样彻底去掉二进制依赖，只要一个 HTTP 请求即可，跨平台、更稳。**需要的话我可以帮你改这一块。**

不接飞书也能先上：生成好的文件会落在服务器 `生成结果/`、`批量打包/`，通过归档抽屉本地下载完全可用，只是少了"自动传云盘"这一步。

---

## 安全设计要点（为什么这样做是安全的）

- **密钥零暴露**：`AI_API_KEY` 只进服务器进程环境；浏览器只调你自己的 `/api/fix`。
- **后端不露面**：只绑 `127.0.0.1`，公网入口只有 Caddy；想更狠可在 Caddyfile 里开 IP 白名单。
- **入口有闸**：天枢账号 + 短信验证码登录；再加登录失败锁定；会话 HttpOnly + Secure + SameSite。
- **不泄露内部**：500 错误只回一句话，堆栈只进服务器日志。
- **最小跨域**：默认不放行任何跨域来源。
- **最小权限**：systemd 里 `NoNewPrivileges`、`ProtectSystem/Home`、专属低权用户。
