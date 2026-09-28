#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
snap_diff.py v1.3.0 —— 按时间段对比客户情况变化。

自动从 GitHub 快照仓（tianji-bi-snapshots，tag 格式 snap-YYYYMMDD）拉取指定日期的
客户数据切片，逐客户计算起止两个时间点之间的变化：

  消耗（元）         cnyUsed / dailyAvg / monthlyAvg / daysToBurn / burnDate
  余额（元）         cnyLeft
  邮件发送（封）     marketed
  邮件触达（封）     reachedEmail
  打开率（百分点）   openRatio
  邮件询盘（封）     emailInq / nonWaInq / waInq
  询盘率（百分点）   emailInqRate / inqRatio
  池子与精准         pool / unlockRatio / prec100 / prec
  时间               daysLeft / svcDays / burnDate
  续费诊断           judge / risk / effectLevel（续费判断、风险等级、效果等级）

用法：
  python3 snap_diff.py 20260920 20260928      # 对比两个日期（都从 GitHub 拉）
  python3 snap_diff.py 20260920 --current     # 对比某天 vs 最新快照
  python3 snap_diff.py --list                 # 列出快照仓可用日期
  python3 snap_diff.py 20260920 20260928 --csv <路径>
输出：终端摘要 + 完整明细 CSV（生成结果/快照对比_起止_时间.csv）。
"""
import csv
import gzip
import io
import os
import re
import sys
import subprocess
import tarfile
import datetime
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ARCH = "/Users/Apple/.baixyn-api/tianji-snapshots"
OUT_DEFAULT = os.path.join(HERE, "生成结果")

# ---------------------------------------------------------------- 指标定义
# (key, 中文标签, 单位, 类别)
# 类别: "num" 计数/金额(取整), "pct" 百分数(保留2位), "text" 文本变化, "date" 日期
FIELDS = [
    # 消耗
    ("cnyUsed",    "消耗",          "元",   "num"),
    ("cnyLeft",    "余额",          "元",   "num"),
    ("dailyAvg",   "日均消耗",      "元/天", "num"),
    ("monthlyAvg", "月均消耗",      "元/月", "num"),
    ("daysToBurn", "预计可消耗天数", "天",   "num"),
    ("burnDate",   "预计耗尽日期",  "",     "date"),
    # 邮件
    ("marketed",     "邮件发送",   "封",   "num"),
    ("reachedEmail", "邮件触达",   "封",   "num"),
    ("openRatio",    "打开率",     "百分点", "pct"),
    ("emailInq",     "邮件询盘",   "封",   "num"),
    ("emailInqRate", "邮件询盘率", "百分点", "pct"),
    ("nonWaInq",     "非WA询盘",   "封",   "num"),
    ("waInq",        "WA询盘",     "封",   "num"),
    ("inqRatio",     "询盘占比",   "百分点", "pct"),
    # 池子与精准
    ("pool",       "客户池",   "条",    "num"),
    ("unlockRatio", "解锁率",   "百分点", "pct"),
    ("prec100",    "精准度",   "百分点", "pct"),
    ("prec",       "精准率",   "百分点", "pct"),
    # 时间
    ("daysLeft", "剩余天数", "天", "num"),
    ("svcDays",  "服务天数", "天", "num"),
    # 诊断（文本变化）
    ("judge",       "续费判断", "", "text"),
    ("risk",        "风险等级", "", "text"),
    ("effectLevel", "效果等级", "", "text"),
]


def load_snapshot(date, slice_dir=None):
    """读取某日期的客户数据。
    date: YYYYMMDD；slice_dir: 可选，本地目录（含 data_<date>.js.gz），
    默认从 GitHub 快照仓按 tag snap-<date> 拉取。返回客户 dict 列表。"""
    if slice_dir:
        path = os.path.join(slice_dir, f"data_{date}.js.gz")
        with gzip.open(path, "rb") as f:
            d = json_loads(f.read().decode())
    else:
        tar = subprocess.run(["git", "archive", "--format=tar", f"snap-{date}"],
                             cwd=ARCH, capture_output=True, check=True).stdout
        tf = tarfile.open(fileobj=io.BytesIO(tar))
        names = [m.name for m in tf.getmembers()
                 if m.isfile() and m.name.startswith("snapshots/")
                 and re.search(rf"data_{re.escape(date)}\.js\.gz$", m.name)]
        if not names:
            raise SystemExit(f"❌ 快照仓没有 {date} 的切片（可用 --list 查看）")
        raw = gzip.decompress(tf.extractfile(names[0]).read())
        d = json_loads(raw.decode())
    return d if isinstance(d, list) else d.get("customers", [])


def json_loads(raw):
    import json
    return json.loads(raw)


def list_dates():
    tags = subprocess.run(["git", "tag", "--list", "snap-*"], cwd=ARCH,
                          capture_output=True, check=True, text=True).stdout
    dates = sorted(t[5:] for t in tags.split() if t.startswith("snap-")
                   and re.fullmatch(r"\d{8}", t[5:]))
    return dates


# ---------------------------------------------------------------- 差值计算
def fmt_delta(unit, kind, va, vb, d, min_delta):
    """单个指标的变化描述：变化量（满足阈值才显示），以及 A→B 原值。"""
    if va is None and vb is None:
        return ""
    if va is None or vb is None:
        if vb is None:
            return f"{va} → 无"
        return f"无 → {vb}"
    d = (vb - va) if kind in ("num", "pct") else None
    if kind == "text":
        if str(va) == str(vb):
            return ""
        return f"{va} → {vb}"
    if kind == "date":
        if str(va) == str(vb):
            return ""
        return f"{va} → {vb}"
    if unit == "元" or unit == "封" or unit == "条" or unit == "天" or unit == "元/天" or unit == "元/月":
        dv = round(d)
        sign = "+" if dv > 0 else ""
        return f"{sign}{dv}" if abs(dv) >= min_delta else ""
    if unit == "百分点":
        dv = round(d, 2)
        sign = "+" if dv > 0 else ""
        return f"{sign}{dv}" if abs(dv) >= min_delta else ""
    return ""


def fmt_value(unit, kind, v):
    if v is None:
        return "—"
    if kind == "text":
        return str(v)
    if kind == "date":
        return str(v)
    if unit in ("元", "封", "条", "天", "元/天", "元/月"):
        return f"{v:.0f}"
    if unit == "百分点":
        return f"{v:.2f}"
    return str(v)


def compute_deltas(a, b, min_delta=0.01):
    """对比两个客户 dict，返回 (指标列表, 新增标记, 判断变化描述)。"""
    rows = []
    for key, label, unit, kind in FIELDS:
        va, vb = a.get(key), b.get(key)
        if va is None and vb is None:
            continue
        d = (b.get(key) - a.get(key)) if kind in ("num", "pct") and va is not None and vb is not None else None
        change = fmt_delta(unit, kind, va, vb, d, min_delta)
        rows.append({
            "key": key, "label": label, "unit": unit, "kind": kind,
            "va": va, "vb": vb, "d": d, "change": change,
        })
    judge_change = ""
    ja, jb = a.get("judge"), b.get("judge")
    if ja and jb and str(ja) != str(jb):
        judge_change = f"续费判断：{ja} → {jb}"
    risk_change = ""
    ra, rb = a.get("risk"), b.get("risk")
    if ra and rb and str(ra) != str(rb):
        risk_change = f"风险：{ra} → {rb}"
    return rows, judge_change, risk_change


# ---------------------------------------------------------------- 主流程
def main():
    args = [a for a in sys.argv[1:]]
    csv_path = None
    if "--csv" in args:
        i = args.index("--csv")
        csv_path = args[i + 1]
        args = args[:i] + args[i + 2:]
    if args == ["--list"]:
        dates = list_dates()
        print("快照仓可用日期（snap-YYYYMMDD）：")
        for d in dates:
            print(f"  {d}")
        return
    if len(args) > 2 or not any(a != "--current" for a in args):
        raise SystemExit(__doc__)
    positional = [a for a in args if a != "--current"]
    dA = positional[0] if positional else None
    dB = positional[1] if len(positional) > 1 else None
    if dA is None:
        # 默认对比 7 天前
        past = (datetime.date.today() - datetime.timedelta(days=7)).strftime("%Y%m%d")
        dA = past
    dates_avail = list_dates()
    if dA not in dates_avail:
        cands = [d for d in dates_avail if d < dB]
        if cands:
            print(f"⚠️ {dA} 无快照，用最近可用日期 {cands[-1]} 代替")
            dA = cands[-1]
    if dB is None or dB == "current":
        dates_avail = sorted(dates_avail + [today8()])
        dB = max(d for d in dates_avail if d >= dA)
    print(f"[1/2] 读取 {dA} 快照…")
    snapA = load_snapshot(dA)
    print(f"      共 {len(snapA)} 家")
    print(f"[2/2] 读取 {dB} 快照…")
    snapB = load_snapshot(dB)
    print(f"      共 {len(snapB)} 家")
    print(f"\n对比 {dA} → {dB}：共 {len(snapB)} 家")
    rows = []
    amap = {c.get("name"): c for c in snapA}
    bmap = {c.get("name"): c for c in snapB}
    bnames = set(bmap)
    for cb in sorted(bmap.values(), key=lambda c: -(c.get("cnyUsed") or 0)):
        na = cb.get("mgr") or "未分配"
        name = cb.get("name")
        ca = amap.get(name)
        deltas, judge_change, risk_change = compute_deltas(ca, cb) if ca else ([], "", "")
        changed = [x for x in deltas if x["change"]]
        delta_mail_sent = next((x["d"] for x in changed if x["key"] == "marketed"), 0) or 0
        delta_mail_inq = next((x["d"] for x in changed if x["key"] == "emailInq"), 0) or 0
        delta_inq_rate = next((x["d"] for x in changed if x["key"] == "emailInqRate"), 0) or 0
        delta_nonwa = next((x["d"] for x in changed if x["key"] == "nonWaInq"), 0) or 0
        status = "新增客户" if ca is None else ("消失客户" if False else "")
        rows.append({
            "name": name, "mgr": na, "status": status,
            "judge": cb.get("judge"), "judge_change": judge_change, "risk_change": risk_change,
            "deltas": deltas, "changed": changed, "has_change": bool(changed or judge_change or risk_change or status),
            "cny_d": next((x["d"] for x in changed if x["key"] == "cnyUsed"), 0) or 0,
            "sent_d": delta_mail_sent, "inq_d": delta_mail_inq, "inqrate_d": delta_inq_rate, "nonwa_d": delta_nonwa,
        })
    n_chg = sum(1 for r in rows if r["has_change"])
    n_new = sum(1 for r in rows if r["status"] == "新增客户")
    n_gone = sum(1 for n in amap if n not in bmap)
    print(f"其中有变化 {n_chg} 家｜新增 {n_new} 家｜消失 {n_gone} 家\n")

    # 总览
    sum_cny = sum(r["cny_d"] for r in rows)
    sum_sent = sum(r["sent_d"] for r in rows)
    sum_inq = sum(r["inq_d"] for r in rows)
    avg_inqrate = (sum(r["inqrate_d"] for r in rows) / n_chg) if n_chg else 0
    print("总览：")
    print(f"  变动客户 {n_chg} 家｜消耗 {sum_cny:+.0f} 元｜邮件发送 {sum_sent:+.0f} 封｜"
          f"邮件询盘 {sum_inq:+.0f} 封｜邮件询盘率 {avg_inqrate:+.2f} 百分点")

    # 按经理汇总（只列有变化的）
    print("\n按经理汇总：")
    mgr_agg = defaultdict(lambda: {"n": 0, "cny": 0, "sent": 0, "inq": 0, "nonwa": 0,
                                   "inqrate": 0.0, "open": 0.0, "unlock": 0.0, "prec100": 0.0, "prec": 0.0})
    for r in rows:
        if not r["has_change"]:
            continue
        agg = mgr_agg[r["mgr"]]
        agg["n"] += 1
        agg["cny"] += r["cny_d"]
        agg["sent"] += r["sent_d"]
        agg["inq"] += r["inq_d"]
        agg["nonwa"] += r["nonwa_d"]
        for key, slot in [("emailInqRate", "inqrate"), ("openRatio", "open"),
                          ("unlockRatio", "unlock"), ("prec100", "prec100"), ("prec", "prec")]:
            dd = next((x["d"] for x in r["changed"] if x["key"] == key), 0) or 0
            agg[slot] += dd
    for mgr, agg in sorted(mgr_agg.items(), key=lambda kv: -kv[1]["n"]):
        print(f"  {mgr:<8} {agg['n']:>2} 家｜消耗 {agg['cny']:+.0f} 元｜发送 {agg['sent']:+.0f} 封｜"
              f"询盘 {agg['inq']:+.0f} 封｜询盘率 {agg['inqrate']/agg['n']:+.2f}｜"
              f"打开率 {agg['open']/agg['n']:+.2f}｜解锁率 {agg['unlock']/agg['n']:+.2f}")

    # 指标变化 TOP
    def top_by(key, n=10):
        vals = [(r, next((x["d"] for x in r["changed"] if x["key"] == key), 0) or 0) for r in rows]
        vals = [(r, d) for r, d in vals if abs(d) > 1e-9]
        vals.sort(key=lambda t: -abs(t[1]))
        return vals[:n]

    labels = [("cnyUsed", "消耗"), ("marketed", "邮件发送"), ("reachedEmail", "邮件触达"),
              ("openRatio", "打开率"), ("emailInq", "邮件询盘"), ("emailInqRate", "邮件询盘率"),
              ("nonWaInq", "非WA询盘"), ("pool", "客户池"), ("unlockRatio", "解锁率"),
              ("prec100", "精准度"), ("prec", "精准率"), ("dailyAvg", "日均消耗"),
              ("monthlyAvg", "月均消耗"), ("daysToBurn", "预计可消耗天数")]
    for key, label in labels:
        tv = top_by(key)
        if not tv:
            continue
        print(f"\n{label}变化 TOP10：")
        for r, d in tv:
            unit = next((x["unit"] for x in r["deltas"] if x["key"] == key), "")
            va = next((x["va"] for x in r["deltas"] if x["key"] == key), None)
            vb = next((x["vb"] for x in r["deltas"] if x["key"] == key), None)
            if unit in ("元", "封", "条", "天", "元/天", "元/月"):
                base = f"{va:.0f} → {vb:.0f}"
                dd = f"{d:+.0f}"
            elif unit == "百分点":
                base = f"{va:.2f} → {vb:.2f}"
                dd = f"{d:+.2f}"
            else:
                base = f"{va} → {vb}"
                dd = f"{d:+.2f}"
            print(f"  {r['name']:<18} {dd} {base}")

    # 判断变化
    chg_judge = [r for r in rows if r["judge_change"] or r["risk_change"]]
    if chg_judge:
        print("\n续费判断变化：")
        for r in chg_judge:
            parts = [r["judge_change"]] if r["judge_change"] else []
            if r["risk_change"]:
                parts.append(r["risk_change"])
            eff = next((x["change"] for x in r["changed"] if x["key"] == "effectLevel"), "")
            if eff:
                parts.append(f"效果 {eff}")
            print(f"  {r['name']}：{'｜'.join(parts)}")

    # 写 CSV
    out = csv_path or os.path.join(OUT_DEFAULT,
                                   f"快照对比_{dA}_{dB}_{datetime.datetime.now():%Y%m%d_%H%M}.csv")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    cols = ["客户", "经理", "新增/消失", "续费判断"]
    for key, label, unit, kind in FIELDS:
        cols += [f"{label}·A", f"{label}·B", f"{label}·Δ"]
    cols += ["续费判断变化", "风险变化"]
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            row = {"客户": r["name"], "经理": r["mgr"], "新增/消失": r["status"],
                   "续费判断": r["judge"] or ""}
            for x in r["deltas"]:
                row[f"{x['label']}·A"] = fmt_value(x["unit"], x["kind"], x["va"])
                row[f"{x['label']}·B"] = fmt_value(x["unit"], x["kind"], x["vb"])
                row[f"{x['label']}·Δ"] = x["change"] or ""
            row["续费判断变化"] = r["judge_change"]
            row["风险变化"] = r["risk_change"]
            w.writerow(row)
    print(f"\n完整明细 CSV：{out}")


def today8():
    return datetime.date.today().strftime("%Y%m%d")


if __name__ == "__main__":
    main()
