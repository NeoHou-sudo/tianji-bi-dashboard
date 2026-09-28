#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
天玑 · 售后看板 —— 每日数据流水线（供 systemd timer / cron 调用）

依次执行：
  1) 抓取【听言】：逐客户 business_info（增量，只补缺的）
  2) 抓取【天枢】：售后看板全量 + Zoe 运营统览，并重算 assets/data.js
     （内部会先检查天枢 token；失效则跳过并提示续期）
  3) 校验 data.js 可解析；若损坏则自动回滚到本次运行前的备份
  4) 打快照 snapshots/data_YYYYMMDD.js（保留最近 N 份）
  5) 写运行日志 日志/每日更新.log
  6) 汇总结果 → 飞书告警（成功 / 部分失败 / 失败）

用法：
  python3 daily_update.py              # 正式跑
  python3 daily_update.py --dry-run    # 只演练编排，不真正抓取
  SKIP_NOTIFY=1 python3 daily_update.py  # 不发飞书（本地测试用）

设计要点：
- 任何一步失败都不破坏旧数据（重算前先备份，校验不过就回滚）
- 每一步互相独立，前一步失败不影响后一步
- 所有外部依赖路径都可用环境变量覆盖，方便搬到服务器
"""
import os, sys, json, shutil, subprocess, datetime

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.dirname(BASE)        # 源 CSV 目录（上级）
DATA_JS = os.path.join(BASE, "assets", "data.js")
SNAP_DIR = os.path.join(BASE, "snapshots")
LOG_DIR = os.path.join(BASE, "日志")
KEEP_SNAPS = int(os.environ.get("KEEP_SNAPS", "30"))
LARK = os.environ.get("LARK_CLI", "/Applications/Loomy.app/Contents/Resources/lark-cli/lark-cli")
ALERT_TO = os.environ.get("BI_ALERT_OPEN_ID", "ou_00ffadb7f292638bfbb424976499d36d")

DRY = "--dry-run" in sys.argv
SKIP_NOTIFY = os.environ.get("SKIP_NOTIFY") == "1"
STAMP = datetime.datetime.now().strftime("%Y%m%d")


def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg):
    line = "[%s] %s" % (now(), msg)
    print(line, flush=True)
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(os.path.join(LOG_DIR, "每日更新.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def notify(text):
    if SKIP_NOTIFY or DRY:
        log("[notify-skipped] " + text.replace("\n", " ｜ "))
        return
    try:
        subprocess.run([LARK, "im", "+messages-send", "--user-id", ALERT_TO,
                        "--text", text, "--as", "bot"],
                       capture_output=True, text=True, timeout=60)
    except Exception as e:
        log("notify failed: %s" % e)


def run(cmd, timeout=1800, env=None):
    log("→ " + " ".join(cmd))
    if DRY:
        return 0, "(dry-run 跳过)"
    e = dict(os.environ)
    if env:
        e.update(env)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=BASE, env=e)
        return r.returncode, ((r.stdout or "")[-300:] + (r.stderr or "")[-300:])
    except subprocess.TimeoutExpired:
        return 124, "超时（%ss）" % timeout
    except Exception as ex:
        return 1, str(ex)


PROGRESS_FILE = os.path.join(BASE, "_progress.json")


STEP_W = [85, 3, 10, 1, 1]      # 各步骤耗时权重（实测：听言主表占 ~85%）


def set_progress(i, total, label):
    """写进度（供前端进度条读取）。i 为「正在执行的步骤序号」，pct 取其之前的累计权重"""
    step = min(i, total)
    base = min(100, sum(STEP_W[:max(0, i - 1)]))
    try:
        json.dump({"step": step, "total": total, "label": label, "pct": base,
                   "weights": STEP_W,
                   "ts": datetime.datetime.now().strftime("%H:%M:%S")},
                  open(PROGRESS_FILE, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass
    log("[进度 %d/%d] %s" % (step, total, label))


def read_gen():
    try:
        s = open(DATA_JS, encoding="utf-8").read()
        return json.loads(s[s.find("=") + 1:].rstrip().rstrip(";")).get("generatedAt")
    except Exception:
        return None


def data_ok():
    try:
        s = open(DATA_JS, encoding="utf-8").read()
        js = json.loads(s[s.find("=") + 1:].rstrip().rstrip(";"))
        n = len(js.get("customers") or [])
        log("data.js 校验通过，客户 %d 家，数据日期 %s" % (n, js.get("generatedAt")))
        return n > 0
    except Exception as e:
        log("data.js 校验失败：%s" % e)
        return False


def snapshot():
    """调用快照引擎：全量切片存档 + 逐日×逐经理指标 + 客户维度变化清单"""
    if DRY:
        return None
    rc, out = run(["python3", os.path.join(BASE, "snapshot.py"), "take"], timeout=300)
    log("快照引擎：" + (out.replace("\n", " ") if out else ""))
    return ("data_%s.js.gz" % STAMP) if rc == 0 else None


def main():
    log("=" * 56)
    log("每日更新开始" + ("（dry-run 演练）" if DRY else ""))

    gen_before = read_gen()
    log("更新前数据日期：%s" % gen_before)

    # 备份当前 data.js（用于回滚）
    backup = DATA_JS + ".bak"
    if os.path.exists(DATA_JS) and not DRY:
        shutil.copy2(DATA_JS, backup)

    result = {}
    TOTAL = 5

    set_progress(1, TOTAL, "抓取听言客户主表")
    # 0) 听言：客户使用情况主表 + 搜索词明细 + 诊断历史（看板重算所需的三份 CSV）
    ty_script = os.path.expanduser(os.environ.get("TINGYAN_PULL_SCRIPT",
                                                  "~/.baixyn-api/tingyan_usage_pull.py"))
    if os.path.exists(ty_script):
        rc_ty0, out_ty0 = run(["python3", ty_script, "--out", DATA_DIR], timeout=3600)
        result["听言主表"] = (rc_ty0 == 0)
        log("听言主表抓取 rc=%s" % rc_ty0)
        if out_ty0 and out_ty0 != "(dry-run 跳过)":
            log("听言主表输出尾部：" + out_ty0.replace("\n", " ")[:200])
    else:
        log("未找到听言抓取脚本（%s），跳过" % ty_script)

    set_progress(2, TOTAL, "抓取听言业务信息")
    # 1) 听言：业务信息（增量）
    rc_ty, out_ty = run(["python3", os.path.join(BASE, "pull_bizinfo.py")], timeout=3600)
    result["听言"] = (rc_ty == 0)
    log("听言抓取 rc=%s" % rc_ty)
    if out_ty and out_ty != "(dry-run 跳过)":
        log("听言输出尾部：" + out_ty.replace("\n", " ")[:200])

    set_progress(3, TOTAL, "抓取天枢售后数据")
    # 2) 天枢：售后看板 + 运营统览 + 重算 data.js（内部处理 token 失效；静默其自身通知）
    rc_ts, out_ts = run(["python3", os.path.join(BASE, "update_dashboard.py")],
                        timeout=1800, env={"SKIP_NOTIFY": "1"})
    ts_ok = (rc_ts == 0)
    if out_ts and out_ts != "(dry-run 跳过)":
        log("天枢输出尾部：" + out_ts.replace("\n", " ")[:250])

    set_progress(4, TOTAL, "重算看板数据")
    # 3) 校验 & 回滚
    ok = data_ok()
    if not ok:
        if os.path.exists(backup):
            shutil.copy2(backup, DATA_JS)
            log("⚠️ data.js 校验失败，已回滚到更新前版本")
        result["结果"] = "失败（已回滚）"
        notify("❌ 天玑看板每日更新失败：data.js 校验不通过，已回滚到上一版。请查看 日志/每日更新.log")
        log("每日更新结束（失败）")
        return

    gen_after = read_gen()
    result["天枢"] = ts_ok
    result["数据日期"] = gen_after

    set_progress(5, TOTAL, "生成数据快照")
    # 4) 快照
    snap = snapshot()

    # 5) 汇总告警
    ts_txt = "已更新" if (gen_after and gen_after != gen_before) else ("无变化/未更新" if gen_before else "未知")
    ty_txt = "已增量抓取" if result.get("听言") else "跳过/失败（不影响其它）"
    if gen_after and gen_after != gen_before:
        notify("✅ 天玑看板每日更新完成\n天枢：%s（数据日期 %s）\n听言：%s\n快照：%s"
               % (ts_txt, gen_after, ty_txt, snap or "—"))
    else:
        notify("⚠️ 天玑看板每日更新未产生新数据\n天枢：%s（当前数据日期 %s，可能登录已过期，回复「续期」并发我验证码）\n听言：%s"
               % (ts_txt, gen_after, ty_txt))

    set_progress(TOTAL + 1, TOTAL, "完成")
    log("每日更新结束：天枢=%s 听言=%s 数据日期=%s" % (result.get("天枢"), result.get("听言"), gen_after))


if __name__ == "__main__":
    main()
