#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_diagnosis.py v1.0 —— 诊断对比窗口：按时间段输出客户续费诊断变化。

给定基准日期（默认自动选 N 天前最近的快照），对比到最新快照，输出：
  一、续费判断 / 风险等级 / 效果等级变化的客户清单（含当前建议）
  二、关键指标变化 TOP10（消耗、邮件发送、触达、打开率、邮件询盘、询盘率等）
  三、建议重点关注客户（判断恶化 或 消耗/询盘明显下滑）
  四、完整明细 CSV（每个指标的 A/B/Δ 与建议）

用法：
  python3 gen_diagnosis.py                    # 默认基准 = 7 天前最近的可用快照
  python3 gen_diagnosis.py 20260920           # 指定基准日期
  python3 gen_diagnosis.py 20260920 --window 14
  python3 gen_diagnosis.py --list             # 列出可用快照日期
输出：生成结果/诊断对比_<基准>_<当前>_<时间>.txt 与同名 .csv
"""
import csv
import datetime
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import snap_diff as SD  # noqa: E402

OUT_DEFAULT = os.path.join(HERE, "生成结果")

# 续费判断 → 紧急度（数值越大越紧急），用于判断"恶化/好转"
JUDGE_PRIORITY = {
    "已到期待挽回": 5,
    "风险流失预警": 4,
    "效果存疑需干预": 3,
    "健康维护": 2,
    "优先谈续费": 2,
    "提前铺续费": 2,
    "常规维护": 1,
}

# 报告中展示的指标（key, 中文标签）
METRICS = [
    ("cnyUsed", "消耗"),
    ("marketed", "邮件发送"),
    ("reachedEmail", "邮件触达"),
    ("openRatio", "打开率"),
    ("emailInq", "邮件询盘"),
    ("emailInqRate", "邮件询盘率"),
    ("nonWaInq", "非WA询盘"),
    ("waInq", "WA询盘"),
    ("inqRatio", "询盘占比"),
    ("pool", "客户池"),
    ("unlockRatio", "解锁率"),
    ("prec100", "精准度"),
    ("prec", "精准率"),
    ("dailyAvg", "日均消耗"),
    ("monthlyAvg", "月均消耗"),
    ("daysToBurn", "预计可消耗天数"),
    ("daysLeft", "剩余天数"),
]

# 写 CSV 用的固定指标列（只含数值指标，不含文本判断列）
CSV_COLS = [k for k, _ in METRICS]


def pick_base_date(args_date, window):
    dates = SD.list_dates()
    if not dates:
        raise SystemExit("❌ 快照仓没有可用日期（--list 查看）")
    today = datetime.date.today()
    if args_date:
        if args_date not in dates:
            cands = [d for d in dates if d < today.strftime("%Y%m%d")]
            msg = f"❌ {args_date} 无快照"
            if cands:
                msg += f"，用最近可用日期 {cands[-1]} 代替"
            raise SystemExit(msg)
        return args_date, dates
    cutoff = (today - datetime.timedelta(days=window)).strftime("%Y%m%d")
    cands = [d for d in dates if d < cutoff]
    if not cands:
        cands = [d for d in dates if d < today.strftime("%Y%m%d")]
    if not cands:
        raise SystemExit("❌ 没有早于今天的快照，无法对比")
    return cands[-1], dates


def fmt_delta_cell(x):
    """把 compute_deltas 的指标行格式化成 CSV 的 Δ 单元格。"""
    if not x["change"]:
        return ""
    return x["change"]


def fmt_ab(x):
    if x["va"] is None and x["vb"] is None:
        return "—"
    if x["va"] is None:
        return f"无→{SD.fmt_value(x['unit'], x['kind'], x['vb'])}"
    if x["vb"] is None:
        return f"{SD.fmt_value(x['unit'], x['kind'], x['va'])}→无"
    if x["kind"] == "text":
        return f"{x['va']} → {x['vb']}" if str(x["va"]) != str(x["vb"]) else str(x["va"])
    if x["unit"] in ("元", "封", "条", "天", "元/天", "元/月"):
        return f"{x['va']:.0f} → {x['vb']:.0f}"
    if x["unit"] == "百分点":
        return f"{x['va']:.2f} → {x['vb']:.2f}"
    return f"{x['va']} → {x['vb']}"


def main():
    args = sys.argv[1:]
    csv_path = None
    window = 7
    if "--csv" in args:
        i = args.index("--csv")
        csv_path = args[i + 1]
        args = args[:i] + args[i + 2:]
    if "--window" in args:
        i = args.index("--window")
        window = int(args[i + 1])
        args = args[:i] + args[i + 2:]
    if args == ["--list"]:
        dates = SD.list_dates()
        print("快照仓可用日期（snap-YYYYMMDD）：")
        for d in dates:
            print(f"  {d}")
        return
    base_date, dates = pick_base_date(args[0] if args else None, window)
    today8 = datetime.date.today().strftime("%Y%m%d")
    cur_date = max(d for d in dates if d >= base_date)
    if cur_date == base_date:
        raise SystemExit(f"❌ {base_date} 之后没有更新的快照（最新 = {cur_date}）")

    print(f"[1/2] 读取 {base_date} 快照…")
    snapA = SD.load_snapshot(base_date)
    print(f"      共 {len(snapA)} 家")
    print(f"[2/2] 读取 {cur_date} 快照…")
    snapB = SD.load_snapshot(cur_date)
    print(f"      共 {len(snapB)} 家")

    amap = {c.get("name"): c for c in snapA}
    bmap = {c.get("name"): c for c in snapB}
    gone = sorted(n for n in amap if n not in bmap)
    new = sorted(n for n in bmap if n not in amap)

    rows = []
    for cb in sorted(bmap.values(), key=lambda c: -(c.get("cnyUsed") or 0)):
        name = cb.get("name")
        ca = amap.get(name)
        deltas, judge_change, risk_change = SD.compute_deltas(ca, cb) if ca else ([], "", "")
        changed = {x["key"]: x for x in deltas if x["change"]}
        eff_change = changed.get("effectLevel", None)
        judge_new, judge_old = cb.get("judge"), (ca or {}).get("judge")
        risk_new, risk_old = cb.get("risk"), (ca or {}).get("risk")
        pri_old = JUDGE_PRIORITY.get(str(judge_old), 0)
        pri_new = JUDGE_PRIORITY.get(str(judge_new), 0)
        worsened = (pri_new > pri_old) if (ca and judge_old and judge_new) else False
        key_num = lambda k: changed.get(k, {}).get("d")
        rows.append({
            "name": name, "mgr": cb.get("mgr") or "未分配",
            "status": "新增客户" if ca is None else "",
            "ca": ca, "cb": cb, "deltas": deltas, "changed": changed,
            "judge_change": judge_change, "risk_change": risk_change,
            "eff_change": eff_change, "worsened": worsened,
            "advice": cb.get("advice") or "",
            "cny_d": key_num("cnyUsed") or 0, "sent_d": key_num("marketed") or 0,
            "inq_d": key_num("emailInq") or 0, "nonwa_d": key_num("nonWaInq") or 0,
            "inqrate_d": key_num("emailInqRate") or 0, "open_d": key_num("openRatio") or 0,
            "daily_d": key_num("dailyAvg") or 0,
            "has_change": bool(changed or judge_change or risk_change or ca is None),
        })
    chg = [r for r in rows if r["has_change"]]
    worsened = [r for r in rows if r["worsened"]]

    # ---------- 报告 ----------
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    txt_path = os.path.join(OUT_DEFAULT, f"诊断对比_{base_date}_{cur_date}_{stamp}.txt")
    os.makedirs(OUT_DEFAULT, exist_ok=True)
    L = []
    L.append("=" * 56)
    L.append("诊断对比窗口报告")
    L.append("=" * 56)
    L.append(f"窗口：{base_date} → {cur_date}（约 {(int(cur_date) - int(base_date)) / 10000:.0f} 天）")
    L.append(f"客户总数：{len(bmap)} 家｜有变化：{len(chg)} 家｜新增 {len(new)} 家｜消失 {len(gone)} 家")
    L.append("")

    L.append("一、续费判断 / 风险 / 效果变化")
    judge_chg = [r for r in chg if r["judge_change"] or r["risk_change"] or r["eff_change"]]
    if judge_chg:
        for i, r in enumerate(judge_chg, 1):
            L.append(f"  {i}) {r['name']}（{r['mgr']}）")
            parts = []
            if r["judge_change"]:
                parts.append(r["judge_change"])
            if r["risk_change"]:
                parts.append(r["risk_change"])
            if r["eff_change"]:
                parts.append(f"效果 {r['eff_change']['change']}")
            L.append("     " + "｜".join(parts))
            if r["advice"]:
                L.append(f"     建议：{r['advice']}")
    else:
        L.append("  （无）")
    L.append("")

    L.append("二、关键指标变化 TOP10")
    for key, label in METRICS:
        tv = [r for r in rows if (d := r["changed"].get(key, {}).get("d")) is not None and abs(d) > 1e-9]
        tv.sort(key=lambda r: -abs(r["changed"][key]["d"]))
        if not tv:
            continue
        L.append(f"  {label}变化：")
        for r in tv[:10]:
            x = r["changed"][key]
            unit = x["unit"]
            if unit in ("元", "封", "条", "天", "元/天", "元/月"):
                dd = f"{x['d']:+.0f}"
                base = f"{x['va']:.0f} → {x['vb']:.0f}"
            elif unit == "百分点":
                dd = f"{x['d']:+.2f}"
                base = f"{x['va']:.2f} → {x['vb']:.2f}"
            else:
                dd = f"{x['d']:+.2f}"
                base = fmt_ab(x)
            L.append(f"    {r['name']:<18} {dd}（{base}）")
    L.append("")

    L.append("三、建议重点关注（判断恶化 或 指标明显下滑）")
    bad_metric = lambda r: (r["cny_d"] < -500 or r["daily_d"] < -10 or r["sent_d"] < -500
                            or r["inq_d"] < -2 or r["inqrate_d"] < -0.05)
    seen, focus = set(), []
    for r in worsened + [r for r in chg if bad_metric(r)]:
        if r["name"] in seen:
            continue
        seen.add(r["name"])
        focus.append(r)
    focus.sort(key=lambda r: -(abs(r["cny_d"]) + abs(r["daily_d"]) * 10 + abs(r["inq_d"]) * 100))
    if not focus:
        L.append("  （无）")
    for i, r in enumerate(focus[:20], 1):
        line = f"  {i}) {r['name']}（{r['mgr']}）"
        if r["worsened"]:
            line += f"｜判断恶化：{r['cb'].get('judge')}"
        if r["cny_d"]:
            line += f"｜消耗 {r['cny_d']:+.0f} 元"
        if r["daily_d"]:
            line += f"｜日均消耗 {r['daily_d']:+.0f} 元/天"
        if r["sent_d"]:
            line += f"｜发送 {r['sent_d']:+.0f} 封"
        if r["inq_d"]:
            line += f"｜询盘 {r['inq_d']:+.0f} 封"
        if r["inqrate_d"]:
            line += f"｜询盘率 {r['inqrate_d']:+.2f}"
        L.append(line)
        if r["advice"]:
            L.append(f"      建议：{r['advice']}")
    L.append("")

    L.append("四、数据说明")
    L.append(f"  数据来源：快照仓 tianji-bi-snapshots，tag snap-{base_date} / snap-{cur_date}")
    L.append("  指标口径同 snap_diff.py；百分比为原始值（如 0.085 即 0.085 个百分点）。")
    L.append("")

    body = "\n".join(L)
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(body)
    print(body)
    print(f"报告已写出：{txt_path}")

    # ---------- CSV ----------
    out = csv_path or os.path.join(OUT_DEFAULT,
                                   f"诊断对比_{base_date}_{cur_date}_{stamp}.csv")
    cols = ["客户", "经理", "新增/消失"]
    for key, label in METRICS:
        cols += [f"{label}·A", f"{label}·B", f"{label}·Δ"]
    cols += ["续费判断(A→B)", "风险(A→B)", "效果(A→B)", "建议"]
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            row = {"客户": r["name"], "经理": r["mgr"], "新增/消失": r["status"]}
            xd = {x["key"]: x for x in r["deltas"]}
            for key, label in METRICS:
                if key in xd:
                    row[f"{label}·A"] = SD.fmt_value(xd[key]["unit"], xd[key]["kind"], xd[key]["va"])
                    row[f"{label}·B"] = SD.fmt_value(xd[key]["unit"], xd[key]["kind"], xd[key]["vb"])
                    row[f"{label}·Δ"] = fmt_delta_cell(xd[key])
            row["续费判断(A→B)"] = fmt_ab(xd["judge"]) if "judge" in xd else ""
            row["风险(A→B)"] = fmt_ab(xd["risk"]) if "risk" in xd else ""
            row["效果(A→B)"] = fmt_ab(xd["effectLevel"]) if "effectLevel" in xd else ""
            row["建议"] = r["advice"]
            w.writerow(row)
    print(f"CSV 明细：{out}")


if __name__ == "__main__":
    main()
