# 天玑 · 售后看板 —— Cloudflare 云端版（部署说明 + 现状）

> 云端地址：**https://tianji-bi.houziyu.workers.dev**
> 部署状态：✅ 已上线（2026-09-28 完成，数据已推送）
> 登录方式：每个人用自己的天枢账号 + 密码 + 短信验证码
> 生成类功能（一键生成 / 批量打包 / 快照 / 归档）仍在本机看板上操作

---

## 0. 架构

```
   同事浏览器  ──登录（各自的天枢账号+密码+短信验证码）──▶  Cloudflare Worker
                                                              │
                                    ①登录：转发到天枢后端验证 ┤
                                    ②会话：签名 Cookie（8 小时）│
                                    ③数据：验证通过才下发      │
                                                              ▼
                                                    Cloudflare KV（存 data.js）

   本机 Mac（每天 22:00 定时任务）
     update_dashboard.py → assets/data.js → ./deploy_cf.sh --data-only → KV
```

要点：
1. 登录走天枢本身的接口，不共用账号，也不需要额外建账号。
2. 客户数据不在网页文件里，而是存在 KV，只有带有效登录凭证的请求才拿得到；直接扒站拿不到名单。
3. 生成类功能留在本机（要调天枢、Agnes AI、飞书云盘上传，还要用你的账号权限）。

---

## 1. 已经配好的东西（不用再动）

- 云端 Worker：`tianji-bi` → https://tianji-bi.houziyu.workers.dev
- Worker 的 workers.dev 子域名：`houziyu`
- KV 命名空间 `BI_KV`，id 已写进 `cf-worker/wrangler.toml`
- 会话签名密钥：已写到 Worker 的 secret `BI_SESSION_SECRET`
- Cloudflare 凭据（API Token / Account ID）：`/Users/Apple/.baixyn-api/cloudflare.env`（权限 600）

## 2. 日常更新（你只需要这一条）

```bash
cd /Users/Apple/Desktop/外贸客户资料/客户续费BI看板
./deploy_cf.sh --data-only      # 几秒钟，把最新 assets/data.js 推到 KV
```

改了页面（index.html / drilldown.html 等）之后，跑一次全量的：

```bash
./deploy_cf.sh                  # 重新生成前端 + 部署 Worker + 推数据
```

> 注意：云端前端是从本机页面自动生成的，**不要直接改 `cf-worker/public/` 里的文件**，会被覆盖。
> 脚本里已经处理了 node 环境问题（Loomy 自带的 node 是 Electron 壳，会让 wrangler 参数错乱，
> 所以脚本强制用 /opt/homebrew/bin 下的真 node）。

## 3. 发短信中继（重要，已搭好）

**问题**：天枢对「发送短信验证码」接口做了来源风控——本机（国内网络）调用正常，
Cloudflare 云端（境外 IP）调用会返回「系统异常」。只有发短信这一步受影响，登录、取数据都正常。

**方案**：云端发短信失败时，把任务放进 Cloudflare 上的中继队列（Durable Object），
本机的小代理每 3 秒取一次任务，用本机网络把短信发出去，再把结果交回云端。
全程不需要公网地址、不需要开端口、不需要装额外软件（不用 cloudflared）。

相关文件与服务：

- 代理脚本：`/Users/Apple/.baixyn-api/sendcode_agent.py`
- 常驻服务：`/Users/Apple/Library/LaunchAgents/com.baixyn.sendcode-relay.plist`（开机自启、挂了自动拉起）
- 日志：`/Users/Apple/.baixyn-api/sendcode_agent.log`
- 共享密钥：`BI_RELAY_TOKEN`（Worker 侧 secret + 本机 `cloudflare.env` 各存一份）

查状态 / 重启：

```bash
launchctl list | grep sendcode                      # 看有没有在跑
launchctl kickstart -k gui/$(id -u)/com.baixyn.sendcode-relay   # 重启
tail -20 ~/.baixyn-api/sendcode_agent.log           # 看日志
```

实测：云端点「发送验证码」→ 约 5 秒内手机收到短信（先试云端直发，失败自动切中继）。
**前提是这台 Mac 保持开机联网**；Mac 关了会退回"打开天枢点一次发送验证码"的兜底提示，登录本身不受影响。

## 4. API Token 安全（重要）

那个 token 曾经在聊天里出现过，**建议到面板 Roll 一次**：
`dash.cloudflare.com` → 头像 → `My Profile` → `API Tokens` → 找到它 → `Roll` → 复制新值。

复制到 `/Users/Apple/.baixyn-api/cloudflare.env` 里替换 `CLOUDFLARE_API_TOKEN` 那一行（**别再贴到聊天里**）。
旧 token 一旦 Roll 就失效，如果不更新这个文件，`./deploy_cf.sh` 会推不上去（网站本身不受影响，照常运行）。

## 5. 数据范围（按登录人裁剪）

### 身份怎么认（关键）

同一个账号密码既能登天枢、也能登听言。天枢侧的昵称有时是账号名或英文名（例如 `lily`、手机号），
跟看板里的「客户成功经理」对不上，所以**身份以听言为准**：登录时用同一套账号密码调听言
`/api/login`，从返回的 token 里取听言昵称（例如 `18320976850 → 李莉`、`ziyu → 侯子昱`）。
拿不到听言信息时，退回天枢的真实姓名/昵称。

### 范围怎么算

- 姓名在**管理名单**（`BI_SCOPE_ALL`）里 → 看全部客户；
  当前名单：`侯子昱, ziyu, 李莉, lily, 18320976850`
- 姓名命中某位「客户成功经理」→ **云端只下发这个人名下的客户**（不是前端隐藏，数据根本不发到浏览器），
  经理筛选自动锁定本人，顶部提示条说明范围；
- 名字谁都没对上 → 默认**不锁人**：先显示全部并在顶部提示"请管理员核对姓名映射"。
  想改成严格模式（对不上就看不到数据），把 `cf-worker/wrangler.toml` 里的
  `BI_SCOPE_FALLBACK` 设为 `none` 再 `./deploy_cf.sh`。

### 实测

- 黄芊芊 → 78 家、段子烽 → 63 家（只看到自己的）
- 侯子昱 / ziyu / 李莉 / 18320976850 → 全部 571 家（管理名单）
- 名字对不上的人 → 全部 + 顶部提示（默认），或 0 家 + 提示（严格模式）

> 改名单：编辑 `cf-worker/wrangler.toml` 里的 `BI_SCOPE_ALL`（姓名或账号，逗号分隔）→ `./deploy_cf.sh`。
> 以后新增主管，把他加进这个名单即可。

## 6. 权限与白名单（可选）

- **谁能登录**：目前任何有天枢账号的人都能登录。想只放行指定账号，改 `cf-worker/wrangler.toml`：

  ```toml
  [vars]
  BI_ALLOWED_ACCOUNTS = "zhangsan,lisi"
  ```

  然后跑一次 `./deploy_cf.sh` 生效。

- **登录后看到什么**：客户成功经理本人登录后默认只看自己名下客户（和本机一致），想看全部用顶部筛选切回。
- **防暴破**：同账号 + IP 连错 8 次锁 15 分钟；会话 8 小时过期；数据响应 `no-store`，退出后立刻拿不到旧数据。

## 7. 本地开发/联调

```bash
node cf-worker/test/dev_server.mjs
```

本机跑起同一份 Worker 代码（假天枢 + 内存 KV），测试账号 `test001` / 密码 `goodpass` / 验证码 `1234`，
访问 http://127.0.0.1:8913 即可验证登录与数据下发流程。

## 8. 常见问题

- **同事打不开/很慢**：`workers.dev` 在国内直连不稳，你们带梯子没问题；想更顺可以给 Worker 绑自定义域名
  （Cloudflare 面板 → Worker → Settings → Domains & Routes → Add → Custom Domain，免费）。
- **数据要不要放 R2**：现在用 KV（免费、免开卡、写一次读多次）。R2 需要先开通（要绑卡），
  而且公开地址等于谁有链接谁能下全量名单；除非以后要把数据喂给别的系统，否则不建议换。
  真要换：`wrangler.toml` 里注释掉 KV、启用 R2 binding 即可（代码里两条路径都支持）。
- **本地看板登录后要手动刷新**：已修复（登录成功会自动重载），本机版与云端版都受益。
