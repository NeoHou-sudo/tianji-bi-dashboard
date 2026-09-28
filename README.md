# tianji-bi-dashboard

天玑 · 售后看板（客户续费 BI 看板）源码仓库。

- **本地版**：Python HTTP 服务（`bi_server.py`）+ 前端单文件页面（`index.html` / `kanban.html` / `tianji.html` / `drilldown.html`），数据由每日 22:00 定时任务抓取（天枢 / 听言）并生成 `assets/data.js`。
- **云端版**：Cloudflare Worker + KV（`cf-worker/`），地址 `https://tianji-bi.houziyu.workers.dev`，每人用各自的天枢账号 + 短信验证码登录，数据范围按登录人在听言的身份裁剪下发。
- **版本管理**：`./release.sh`（上线前自检 → 提交 → 打 tag → 推送 → 部署生产），自检脚本 `./test_smoke.sh`。详见 `README-版本管理.md` 与 `CHANGELOG.md`。

> ⚠️ 本仓库只含源码与脚本，**不包含任何客户数据**（`assets/data.js`、CSV、业务缓存均已被 `.gitignore` 排除，且 `~/.baixyn-api/` 下的密钥/凭证不在本仓库内）。
