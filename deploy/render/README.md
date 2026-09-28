# 天玑 · 售后看板 —— Render 部署（手把手）

> 目标：把看板部署到 Render，拿到一个 `https://xxx.onrender.com` 的网址给团队访问。
> **先读最后一节「免费层的真相」**——它决定这个方案适不适合你正式用。

---

## 第 0 步：把项目放到 GitHub（Render 只从 Git 仓库部署）

1. 打开 **desktop.github.com**，下载 **GitHub Desktop**（图形化，不用敲命令）。
2. 用邮箱注册一个 GitHub 账号（免费）。
3. 打开 GitHub Desktop → 菜单 `File` → `Add Local Repository` → 选择这个文件夹：
   `客户续费BI看板`
   它会提示"这里还不是仓库"，点 **create a repository** → 再点 **Create Repository**。
4. 右上角点 **Publish repository**。
   ⚠️ **务必把「Keep this code private」的勾打上（私有仓库）**——因为 `assets/data.js` 里含 566 家客户名单，公开就等于把客户数据挂网上了。

推上去后，你的项目就在 GitHub 私有仓库里了。

## 第 1 步：注册 Render

打开 **render.com** → 右上角 `Get Started` → 选 **用 GitHub 登录**（免信用卡）。首次会要求授权 Render 访问你的仓库，同意即可。

## 第 2 步：新建服务（两种，任选一种）

**方式 A（省事，推荐）：用 Blueprint 一键建**
Dashboard → `New +` → **Blueprint** → 选你刚推的仓库 → 它会读到根目录的 `render.yaml` → 点 **Apply**。

**方式 B（手动，看得更清楚）**
`New +` → **Web Service** → 选仓库 → 然后：
- Runtime 选 **Docker**
- Dockerfile Path 填 **`deploy/render/Dockerfile`**
- Instance Type 选 **Free**
- 点 **Create Web Service**

等构建完成（几分钟），你会拿到一个网址，形如 `https://tianji-bi-xxxx.onrender.com`。

## 第 3 步：填密钥

进服务页 → 左侧 **Environment** → 添加这三个（值从你本机 `~/.baixyn-api/` 里取）：

- `AI_API_KEY`
- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`

保存后 Render 会自动重新部署一次。

## 第 4 步：访问验证

浏览器打开 `https://你的服务名.onrender.com/tianji.html`，看到登录页就成功了（用你的天枢账号+短信验证码登录）。

---

## ⚠️ 免费层的真相（务必知道）

**一、15 分钟没人访问就休眠。** 下次有人打开要等 **30~60 秒冷启动**，团队大概率会以为"网站挂了"。想随时在线，必须付费。

**二、磁盘不持久。** 每次重启/重新部署/休眠唤醒后，写在服务器上的文件都会回到镜像初始状态。对你这套看板的具体影响是：
- **登录态（sessions.db）会丢** → 大家要重新登录（而登录要短信验证码，很烦）；
- **快照（snapshots/）会丢** → 你刚要的"历史切片/成果对比"用不了；
- **生成结果、归档记录会丢** → 归档抽屉里的记录恢复成初始状态；
- 只有随镜像打包的 `assets/data.js` 还在（也就是你推送那一刻的数据）。

**三、想持久化要花钱。** Render 的持久磁盘只支持 **Starter 计划（$7/月起 ≈ 600 元/年）**，而且挂了磁盘还得把程序的运行目录指过去（这一步我可以帮你改）。

**四、国内访问速度一般。** 海外节点，能打开但时快时慢。

---

## 所以，Render 适合怎么用

**适合**：想先"看看整套东西在云上跑起来是什么样"、做个演示、验证 Docker 打包没问题。10 分钟就能上线。

**不适合**：当团队天天用的正式服务。原因就是上面那三个——会休眠、会丢数据、持久化要 $7/月。

**如果只是想让团队看数据**，其实有个更聪明的免费组合：**把看板做成"只读静态版"推到 Cloudflare Pages，再挂一层 Cloudflare Access 登录**——不用你开机、不花钱、有登录，国内也能访问。代价是网页上的"一键生成/打包"按钮用不了（下属只看不受影响）。需要的话我帮你改成这个版本。

**如果预算能到一年几十块**，还是国内云轻量最省心（38~79 元/年、常驻、国内秒开）。见 `deploy/国内部署-手把手.md`。
