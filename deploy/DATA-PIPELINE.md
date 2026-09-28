# 天玑 · 每日数据抓取流水线

> 结论：**架构完全有能力做。** 常驻 Linux 机器 + systemd 定时器，是跑这类"每日 ETL"的标准姿势。
> 真正的约束不是架构，而是**凭证时效**（天枢登录要短信码、听言 token 会过期）——见文末。

---

## 1. 流水线做什么

`daily_update.py` 是统一入口，每天定时跑，依次：

1. **抓听言主表**：`~/.baixyn-api/tingyan_usage_pull.py` 拉「客户使用情况主表 / 全量搜索词明细 / 诊断历史明细」三份 CSV（看板重算的必需输入）。脚本路径可用环境变量 `TINGYAN_PULL_SCRIPT` 覆盖。
2. **抓听言业务信息**：`pull_bizinfo.py` 逐客户增量拉 business_info（只补缺的）。
3. **抓天枢**：`update_dashboard.py` 拉售后看板全量 + Zoe 运营统览，并重算 `assets/data.js`（内部先查 token，失效则跳过）。
3. **校验**：确认 `data.js` 能被 JSON 解析、客户数 > 0；**不合格就自动回滚**到本次运行前的备份。
4. **打快照**：存 `snapshots/data_YYYYMMDD.js`，保留最近 30 份（可回滚、可对比趋势）。
5. **写日志**：`日志/每日更新.log`。
6. **飞书告警**：成功 / 未产生新数据 / 失败，都会给你发一条。

任何一步失败都不影响旧数据——这是刻意设计的。

## 2. 安装（在服务器上）

```bash
sudo cp /opt/tianji-bi/deploy/tianji-update.service /etc/systemd/system/
sudo cp /opt/tianji-bi/deploy/tianji-update.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tianji-update.timer

# 查看下次触发时间
systemctl list-timers | grep tianji

# 立刻手动跑一次，验证
sudo systemctl start tianji-update.service
journalctl -u tianji-update -n 120 --no-pager
```

默认**每天 06:30**（带 5 分钟随机延迟）。改时间就编辑 `.timer` 里的 `OnCalendar`，然后 `systemctl daemon-reload && systemctl restart tianji-update.timer`。

## 3. 需要放到服务器的凭证文件

流水线依赖你本机 `~/.baixyn-api/` 下的东西。服务器上用低权用户 `tianji`，把它放到 `/home/tianji/.baixyn-api/`：

- `credentials.json`（天枢 token + 听言 diag_token + 各 base_url）
- `renew_tokens.py`（天枢续期检查）
- `csm_feishu_map.json`（客户经理→飞书映射）

权限一律 `chmod 600`，属主 `tianji`。路径可用环境变量覆盖：`TS_CREDS`、`RENEW_SCRIPT`、`CSM_MAP_FILE`。

同时服务器需要能跑 `lark-cli`（发告警和传云盘都用它），在 `.env` 里设 `LARK_CLI=/usr/local/bin/lark-cli`。


## 3.1 页面上的「⟳ 快照」按钮

看板页首（数据快照时间旁边）有一个「⟳ 快照」按钮：点击后由后端在**后台线程**执行本流水线，前端每 2.5 秒轮询进度，完成后自动刷新页面。

- 走的是 `/api/refresh`（需登录）+ `/api/refresh/status`
- **不消耗 AI 额度**（抓取是纯接口 + 本地计算）
- 若天枢登录已过期：不会报错崩溃，会提示"未产生新数据（可能登录已过期）"
- 重算前会备份 `data.js` 并校验，任何异常都会**自动回滚**，不会破坏现有数据

## 4. 手动操作与排错

```bash
cd /opt/tianji-bi
python3 daily_update.py --dry-run          # 只演练编排
SKIP_NOTIFY=1 python3 daily_update.py      # 真跑但不发飞书
sudo systemctl start tianji-update.service # 走 systemd 跑一次
tail -f 日志/每日更新.log                   # 看实时日志
```

## 5. ⚠️ 凭证时效：能不能"永远无人值守"

这是唯一的天花板，如实说明：

- **天枢**：登录要手机短信验证码，且它的 token refresh 接口参数格式一直走不通。所以 token 一过期，**必须人工发一次验证码**。现行设计是"过期就飞书提醒你"。
- **听言**：`diag_token` 是浏览器 Cookie，也会过期；同理。

想做到真正无人值守，三条路（按推荐度）：

1. **找天枢/听言官方要一个"服务账号 / 长效 API Key"**（不绑短信、可长期有效）。这是最干净的解法，一次沟通换永久省心。
2. **摸清 token 的真实刷新链路**：抓到刷新接口的正确参数后，就能自动续期。我可以帮你对着抓包再试一次。
3. **让有效期尽量长**：人工续一次能撑多久就撑多久，配合"临期告警"提前提醒。

在没有 (1)/(2) 之前，实际效果就是：**平时每天自动更新，token 过期时自动提醒你花两分钟续一下**——已经比手点强太多了。

## 6. 建议的监控增强（可选，后续可加）

- 连续 N 天没更新成功 → 升级告警（比如打你电话/多发一次）。
- 每次更新把"客户数 / 余额合计"等关键指标记进一张 `metrics.csv`，方便看趋势。
- 定时清理 `生成结果/`、`批量打包/` 里过期文件，省磁盘。

---

## 7. 数据切片快照（对下属诊断 & 成果对比）

每日更新后会**自动调 `snapshot.py take`**，把数据按「数据日期」切片存档。产出都在 `snapshots/`：

- `data_YYYYMMDD.js.gz` —— 当日**全量数据快照**（gzip 压缩，按数据日期切片；默认保留 365 份，可用 `SNAP_KEEP` 调）。
- `metrics.json` —— **逐日 × 逐经理**的关键指标累积表：客户数、活跃数、近7/30天跟进数、30天覆盖率、余额合计、月均消耗合计、30天内到期、高危客户、优先谈续费、风险流失预警、非WA询盘、平均精准率。**这是做成果对比的底座。**
- `changes_YYYYMMDD.csv` —— 与上一份快照相比，**客户维度的字段变化清单**（服务状态、服务期、余额、月均消耗、续费判断、风险等级、效果评级、最近跟进）。**这是做数据诊断的底座**：谁跟进了、谁没动、谁的客户在恶化，一目了然。

### 常用命令

```bash
cd /opt/tianji-bi
python3 snapshot.py take                       # 手动采一份快照（每日流水线已自动调用）
python3 snapshot.py list                       # 列出所有快照日期
python3 snapshot.py report                     # 最近两份对比
python3 snapshot.py report 20260901 20260915   # 指定两个日期对比
python3 snapshot.py report --json              # 机器可读，便于再接可视化
```

对比报告会按「全局 + 各经理」输出每个指标的 `旧值 → 新值 (▲/▼ 增减)`，例如：

```
【各经理】
  姜南雪   近30跟进 10→17 (+7) ｜ 覆盖率 0.204→0.347 (+0.143)
  代佳文   近30跟进 28→24 (-4) ｜ 覆盖率 0.519→0.444 (-0.074)
```

### 想更好用？可以再接一层可视化

后端能力（快照列举 + 两两对比）已具备，我可以再加一个前端面板「📈 快照对比：选两个日期 → 逐经理增减表 + 客户变动明细」，让你在页面上点两下就出结论，不用敲命令。需要就说。

