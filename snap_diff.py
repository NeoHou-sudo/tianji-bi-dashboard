#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
天玑 · 快照对比（时间段客户情况变化）

自动从 GitHub 快照仓（tianji-bi-snapshots）拉取指定日期的数据切片，
对比两个时间点之间的客户变化：消耗、邮件询盘、触达、客户池、精准率等。

用法：
  python3 snap_diff.py 20260920 20260928             # 两个日期都从 GitHub 拉
  python3 snap_diff.py 20260920 --current            # 某天 vs 当前本地数据
  python3 snap_diff.py 20260920 20260928 --csv 输出.csv   # 只出 CSV
  python3 snap_diff.py --list                        # 列出 GitHub 上可用的快照日期

说明：
  - 快照 tag 命名 snap-YYYYMMDD；同一日可能有多份（data_YYYYMMDD.js.gz 为当日主切片，
    data_YYYYMMDD-HHMM 为当天多次操作的时间切片），默认优先用主切片。
  - "消耗"字段=cnyUsed（累计消耗，两日期差值=该时间段内消耗）；
    "邮件询盘"=nonWaInq（非 WhatsApp 询盘）等，见报告表头。
"""
import argparse
import csv
import gzip
import io
import json
import os
import subprocess
import sys
from datetime import datetime

ARCH = os.path.expanduser(os.environ.get("SNAP_ARCHIVE", "~/.baixyn-api/tianji-snapshots"))
OUT_DEFAULT = os.path.expanduser("~/Desktop/外贸客户资料/客户续费BI看板/生成结果")

# 参与对比的字段：显示名 -> (键, 是否比率)
FIELDS = [
    ("消耗(元)", "cnyUsed", False),
    ("余额(元)", "cnyLeft", False),
    ("邮件触达(封)", "reachedEmail", False),
    ("邮件询盘(封)", "nonWaInq", False),
    ("WA询盘(封)", "waInq", False),
    ("客户池(条)", "pool", False),
    ("解锁率(%)", "unlockRatio", True),
    ("精准率(%)", "prec", False),
    ("询盘占比(%)", "inqRatio", False),
    ("剩余天数", "daysLeft", False),
]


def log(*a):
    print(*a, file=sys.stderr)


def ensure_tags():
    """把 GitHub 上所有 snap-* tag 拉到本地"""
    if not os.path.isdir(ARCH):
        raise SystemExit(f"找不到快照仓：{ARCH}")
    if subprocess.run(["git", "remote", "get-url", "origin"], cwd=ARCH,
                      capture_output=True).returncode == 0:
        subprocess.run(["git", "fetch", "--tags", "origin"], cwd=ARCH,
                       capture_output=True, text=True)


def available_dates():
    r = subprocess.run(["git", "tag"], cwd=ARCH, capture_output=True, text=True)
    tags = [t.strip() for t in r.stdout.splitlines() if t.strip().startswith("snap-")]
    return sorted(tags)


def load_snapshot(date):
    """拉取某日快照并读取主切片 data_<date>.js.gz，返回客户 dict{name: {...}}"""
    tag = f"snap-{date}"
    ok = subprocess.run(["git", "rev-parse", tag], cwd=ARCH,
                        capture_output=True).returncode == 0
    if not ok:
        raise SystemExit(f"❌ 没有 {date} 的快照。可用日期：{available_dates()}")
    tar = subprocess.run(["git", "archive", "--format=tar", tag], cwd=ARCH,
                         capture_output=True, check=True).stdout
    import tarfile
    tf = tarfile.open(fileobj=io.BytesIO(tar))
    files = [m.name for m in tf.getmembers() if m.name.startswith("snapshots/")]
    # 当日主切片：优先 data_<date>.js.gz，其次时间切片里最新的
    candidates = sorted(
        [f for f in files if f.endswith(f"data_{date}.js.gz")] +
        [f for f in files if f.endswith(f"data_{date}-") and f.endswith(".js.gz")])
    target = None
    if any(f.endswith(f"data_{date}.js.gz") for f in candidates):
        target = [f for f in candidates if f.endswith(f"data_{date}.js.gz")][0]
    elif candidates:
        target = candidates[-1]
    if not target:
        raise SystemExit(f"❌ {date} 快照里没有数据切片")
    raw = tf.extractfile(target).read()
    data = json.loads(gzip.decompress(raw).decode("utf-8"))
    if isinstance(data, dict) and "customers" in data:
        data = data["customers"]
    return {c.get("name"): c for c in data}


def load_current():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "data.js")
    s = open(p, encoding="utf-8").read()
    d = json.loads(s[s.find("=") + 1:].rstrip().rstrip(";"))
    return {c.get("name"): c for c in d.get("customers", [])}


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fmt(v, ratio=False, signed=False):
    if v is None:
        return ""
    if ratio:
        return f"{v * 100:.1f}%"
    if signed and v:
        return f"{v:+.1f}"
    return f"{v:.1f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v)


def diff(a, b):
    """返回 A/B 两点之间的对比行：A值、B值、差值（B-A）"""
    row = {}
    for label, key, ratio in FIELDS:
        va, vb = num(a.get(key)), num(b.get(key))
        d = (vb - va) if (va is not None and vb is not None) else None
        row[label] = (va, vb, d, ratio)
    return row


def main():
    ap = argparse.ArgumentParser(description="快照对比：计算时间段内客户情况变化")
    ap.add_argument("dates", nargs="*", help="起止日期 YYYYMMDD（可加 --current）")
    ap.add_argument("--current", action="store_true", help="终点用当前本地数据")
    ap.add_argument("--csv", help="输出 CSV 文件路径")
    ap.add_argument("--list", action="store_true", help="列出可用快照日期")
    ap.add_argument("--min-delta", type=float, default=0.01,
                    help="只输出变化超过该阈值(元/封/条)的客户，默认 0.01")
    args = ap.parse_args()

    if args.list:
        ensure_tags()
        print("可用快照日期：")
        for t in available_dates():
            print("  ", t)
        return

    if len(args.dates) < 1 or len(args.dates) > 2:
        ap.error("需要 1 个日期（配 --current）或 2 个日期")
    ensure_tags()
    dA = args.dates[0]
    if len(args.dates) == 2:
        dB = args.dates[1]
    else:
        if not args.current:
            ap.error("只给一个日期时必须加 --current")
        dB = None
    log(f"[1/2] 读取 {dA} 快照…")
    A = load_snapshot(dA)
    log(f"      共 {len(A)} 家")
    if dB:
        log(f"[2/2] 读取 {dB} 快照…")
        B = load_snapshot(dB)
    else:
        log("[2/2] 使用当前本地数据…")
        B = load_current()
    log(f"      共 {len(B)} 家")

    names = sorted(set(A) | set(B))
    rows = []
    for n in names:
        a, b = A.get(n, {}), B.get(n, {})
        if not a and not b:
            continue
        row = {"客户": n, "经理": (b or a).get("mgr", "")}
        d = diff(a, b)
        row["新增/消失"] = "新增" if (not a) else ("消失" if (not b) else "")
        for label, (va, vb, dd, ratio) in d.items():
            row[f"{label}·A"] = fmt(va, ratio)
            row[f"{label}·B"] = fmt(vb, ratio)
            row[f"{label}·Δ"] = fmt(dd, ratio=False) if dd is None or abs(dd) >= args.min_delta else ""
        row["续费判断"] = f"{a.get('judge','')} → {b.get('judge','')}" if (a.get('judge') or '') != (b.get('judge') or '') else (b.get('judge') or '')
        rows.append(row)

    # 打印摘要
    changed = [r for r in rows if any(r.get(f"{l}·Δ") for l, _, _ in FIELDS)]
    print(f"\n对比 {dA} → {dB or '当前'}：共 {len(rows)} 家，其中有变化 {len(changed)} 家\n")
    if changed:
        by_mgr = {}
        for r in changed:
            by_mgr.setdefault(r["经理"], []).append(r)
        for mgr, rs in sorted(by_mgr.items(), key=lambda x: -len(x[1])):
            cny = sum(float(r.get("消耗(元)·Δ") or 0) for r in rs)
            inq = sum(float(r.get("邮件询盘(封)·Δ") or 0) for r in rs)
            reach = sum(float(r.get("邮件触达(封)·Δ") or 0) for r in rs)
            print(f"  {mgr:8s} 变动客户 {len(rs):3d} 家｜消耗 {cny:+8.0f} 元｜邮件触达 {reach:+6.0f} 封｜邮件询盘 {inq:+5.0f} 封")
        print("\n  消耗变化 TOP10：")
        top = sorted(changed, key=lambda r: -(float(r.get("消耗(元)·Δ") or 0)))
        for r in top[:10]:
            print(f"    {r['客户'][:22]:24s} {r['消耗(元)·Δ']} 元（{r['消耗(元)·A']} → {r['消耗(元)·B']}）")
        print("\n  邮件询盘变化 TOP10：")
        top = sorted(changed, key=lambda r: -(float(r.get("邮件询盘(封)·Δ") or 0)))
        for r in top[:10]:
            print(f"    {r['客户'][:22]:24s} {r['邮件询盘(封)·Δ']} 封（{r['邮件询盘(封)·A']} → {r['邮件询盘(封)·B']}）")

    # 写 CSV（有 --csv 用指定路径，否则落默认目录）
    out = args.csv or os.path.join(OUT_DEFAULT, f"快照对比_{dA}_{dB or '当前'}_{datetime.now():%Y%m%d_%H%M}.csv")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    cols = ["客户", "经理", "新增/消失", "续费判断"]
    for label, _, _ in FIELDS:
        cols += [f"{label}·A", f"{label}·B", f"{label}·Δ"]
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(f"\n完整明细 CSV：{out}")


if __name__ == "__main__":
    main()
