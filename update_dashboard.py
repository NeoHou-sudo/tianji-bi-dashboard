#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
看板每日数据更新
1. 检查天枢 token；有效 → 拉最新售后看板+运营统览，重算 data.js
2. 失效 → 发飞书提醒用户续期
用法: python3 update_dashboard.py [--notify]
"""
import json, os, subprocess, sys, datetime

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.dirname(BASE)
CREDS = os.environ.get("TS_CREDS", os.path.expanduser("~/.baixyn-api/credentials.json"))
LARK = os.environ.get("LARK_CLI", "/Applications/Loomy.app/Contents/Resources/lark-cli/lark-cli")
USER_OPEN_ID = "ou_00ffadb7f292638bfbb424976499d36d"   # 用户(主管)
RENEW = os.environ.get("RENEW_SCRIPT", os.path.expanduser("~/.baixyn-api/renew_tokens.py"))
STAMP = datetime.datetime.now().strftime("%Y%m%d")


def notify(text):
    if os.environ.get("SKIP_NOTIFY") == "1":
        print("[notify-skipped] " + text.replace("\n", " "))
        return
    try:
        subprocess.run([LARK, "im", "+messages-send", "--user-id", USER_OPEN_ID,
                        "--text", text, "--as", "bot"], capture_output=True, text=True, timeout=60)
    except Exception as e:
        print("notify failed:", e)


def check_token():
    r = subprocess.run(["python3", RENEW, "check"], capture_output=True, text=True, timeout=60)
    return "[天枢] ✅ 有效" in r.stdout or "✅ 有效" in r.stdout


def api_post(tok, path, body):
    r = subprocess.run(["curl", "-s", "--max-time", "90", "-X", "POST",
                        "-H", f"Authorization: Bearer {tok}", "-H", "Content-Type: application/json",
                        "-d", json.dumps(body), "https://ts.baixyn.com" + path],
                       capture_output=True, text=True, timeout=95)
    try:
        return json.loads(r.stdout)
    except Exception:
        return {}


def pull_and_update():
    tok = os.environ.get("TS_TOKEN_OVERRIDE") or json.load(open(CREDS))["ts_baixyn"]["access_token"]
    # 售后看板
    d1 = api_post(tok, "/api/admin-api/oc/workbench/list", {"pageNo": 1, "pageSize": 600})
    lst = (d1.get("data") or {}).get("list") or []
    if not lst:
        return False, "售后看板拉取为空（token 可能失效）"
    import csv
    with open(os.path.join(DATA_DIR, f"天枢售后看板全量_{STAMP}.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        keys = ["customerId", "customerName", "riskLevel", "riskScore", "serviceStatus", "daysToExpire",
                "serviceStartTime", "serviceEndTime", "pointsRemaining", "pointsTotal", "totalClueCount",
                "taggedCount", "reachClueCount", "inquiryClueCount", "lastLoginTime", "ownerName",
                "serviceName", "bdName", "accountVersionName"]
        w.writerow(keys)
        for c in lst:
            w.writerow([c.get(k) for k in keys])
    # 运营统览
    d2 = api_post(tok, "/api/admin-api/oc/zoe-operation/overview/queryZoeOperationDetails", {"pageNo": 1, "pageSize": 900})
    lst2 = (d2.get("data") or {}).get("list") or []
    if lst2:
        cols = list(lst2[0].keys())
        with open(os.path.join(DATA_DIR, f"天枢Zoe运营统览_{STAMP}.csv"), "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for c in lst2:
                w.writerow({k: c.get(k) for k in cols})
    # 记录本次抓取的真实时刻，供 data.js 的「数据快照时间」使用
    try:
        open(os.path.join(BASE, "_snapshot_time.txt"), "w", encoding="utf-8").write(
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
    except Exception:
        pass
    # 重算 data.js（generate_dashboard.py 用的是"天枢售后看板全量"等模糊匹配，最新文件优先）
    r = subprocess.run(["python3", os.path.join(BASE, "generate_dashboard.py")],
                       capture_output=True, text=True, timeout=300, cwd=BASE)
    return ("[OK]" in r.stdout), r.stdout[-200:] + r.stderr[-200:]


def main():
    # 若由页面「快照」按钮传入最新登录 token，则直接用
    if not os.environ.get("TS_TOKEN_OVERRIDE"):
        # 先用 refresh_token 自动续期（无需短信验证码），成功即无需人工干预
        try:
            rr = subprocess.run(["python3", RENEW, "refresh"], capture_output=True, text=True, timeout=60)
            if rr.stdout and "自动刷新" in rr.stdout:
                print(rr.stdout.strip()[:160])
        except Exception:
            pass
    if not os.environ.get("TS_TOKEN_OVERRIDE") and not check_token():
        msg = ("⚠️ 客户续费看板：天枢登录已过期，今晚数据未能自动更新。\n"
               "请回复「续期」并发我验证码，我更新完数据继续。")
        notify(msg)
        print("token 失效，已提醒")
        return
    ok, info = pull_and_update()
    if ok:
        notify(f"✅ 客户续费看板数据已于 {datetime.datetime.now():%Y-%m-%d %H:%M} 自动更新完成")
        print("更新成功:", info)
    else:
        notify(f"⚠️ 客户续费看板数据更新失败：{info[:120]}")
        print("更新失败:", info)


if __name__ == "__main__":
    main()
