# 天玑 · 售后看板 — 版本管理规范

> 从 v1.0.0 起，本项目用 Git 做版本管理（GitHub 私有仓库），
> 规则一句话：**测试不过不进版，进了版才上生产。**

## 目录

- 仓库：`NeoHou-sudo/tianji-bi-dashboard`（私有，见第 2 节）
- 版本：语义化 `vX.Y.Z`（v1.0.0 → 修复 bug 走 v1.0.1，加功能走 v1.1.0）
- 入口：`./release.sh`（测试 → 提交 → tag → 推送 → 部署）

## 1. 日常发布流程（只用一条命令）

```bash
./release.sh "本次改动说明"
```

它依次做 5 件事，任何一步失败都会停下来：

1. `test_smoke.sh` 上线前自检（Python 语法 / Worker JS 语法 / 云端前端可生成 / 生产可达 / 数据闸门 / 中继通道），**不通过直接终止，不提交**；
2. 重新生成云端前端产物；
3. `git commit` + 自动递增版本号打 tag（或 `--version v1.2.0` 指定）；
4. 推送到 GitHub（分支 + tag）；
5. `./deploy_cf.sh` 部署生产（Cloudflare）。

只想提交不打生产：`./release.sh --no-deploy "文案修改"`。

## 2. GitHub 仓库（私有）

仓库地址：`git@github.com:NeoHou-sudo/tianji-bi-dashboard.git`

首次配置（一次性）：

```bash
git remote add origin git@github.com:NeoHou-sudo/tianji-bi-dashboard.git
git push -u origin master
git push origin --tags
```

> 建仓方式：GitHub → `New repository` → 名字 `tianji-bi-dashboard` → **Private** → Create（不需要初始化任何文件，README/.gitignore 都不用勾）。

## 3. 什么入库、什么不入库（.gitignore）

入库：源码（`*.py` / `*.html` / `cf-worker/`）、文档、发布脚本、品类映射表。

**绝不入库**（都在 `.gitignore` 里）：

- `assets/data.js`、任何 `*.csv / *.xlsx`（客户数据）
- `生成结果/`、`批量打包/`、`归档/`、`日志/`（业务产物）
- `business_info缓存*.json`、`公司简述缓存.json`、`服务记录_最新.json`（客户信息缓存）
- 密钥：`~/.baixyn-api/`（在本机，不在项目目录内）

## 4. 版本记录

看 `CHANGELOG.md`（每个 tag 对应一条记录，随 release.sh 一起提交）。
