#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
天玑 · 数据快照引擎
======================================================================
把每次更新的看板数据「按数据日期切片」存档，并沉淀成可跨期对比的指标，
支撑两件事：
  · 对下属（客户成功经理）的数据诊断
  · 工作成果的跨期对比

产出（均落在 snapshots/ 目录）：
  data_YYYYMMDD.js.gz    当日全量数据快照（按数据日期切片，gzip 压缩存档）
  metrics.json           逐日 × 逐经理 的关键指标（长期累积，趋势/对比的底座）
  changes_YYYYMMDD.csv   与上一份快照相比，客户维度的字段变化清单（谁做了什么）

命令：
  python3 snapshot.py take                 # 从当前 assets/data.js 采一份快照
  python3 snapshot.py list                 # 列出已有快照日期
  python3 snapshot.py report               # 用最近两份做对比报告
  python3 snapshot.py report 20260901 20260915
  python3 snapshot.py report --json        # 机器可读
"""
import os, sys, json, gzip, csv, glob, datetime

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_JS = os.path.join(BASE, "assets", "data.js")
SNAP_DIR = os.path.join(BASE, "snapshots")
METRICS = os.path.join(SNAP_DIR, "metrics.json")
KEEP = int(os.environ.get("SNAP_KEEP", "365"))
HIDE_MGRS = ["未分配", "李嘉洲", "漠鹰"]      # 与前端保持一致：屏蔽测试/无效负责人


# ------------------------------------------------------------------ 工具
def load_data(path=DATA_JS):
    s = open(path, encoding="utf-8").read()
    return json.loads(s[s.find("=") + 1:].rstrip().rstrip(";"))


def clean_customers(data):
    cs = data.get("customers") or []
    return [c for c in cs if not any(h in (c.get("mgr") or "") for h in HIDE_MGRS)]


def _d(s):
    try:
        return datetime.datetime.strptime(str(s)[:10], "%Y-%m-%d")
    except Exception:
        return None


def within(dtstr, ref, days):
    d = _d(dtstr)
    return bool(d and (ref - d).days <= days and (ref - d).days >= 0)


def read_snap(datekey):
    """读某日快照，兼容压缩与未压缩两种格式"""
    for p in (os.path.join(SNAP_DIR, f"data_{datekey}.js.gz"),
              os.path.join(SNAP_DIR, f"data_{datekey}.js")):
        if os.path.exists(p):
            if p.endswith(".gz"):
                return json.loads(gzip.open(p, "rt", encoding="utf-8").read())
            return json.loads(open(p, encoding="utf-8").read())
    return None


def list_snaps():
    keys = []
    for p in glob.glob(os.path.join(SNAP_DIR, "data_*.js*")):
        b = os.path.basename(p)
        k = b[len("data_"):].split(".")[0]
        keys.append(k)
    return sorted(set(keys))


# ------------------------------------------------------------- 指标计算
def metrics_of(data):
    """逐经理关键指标 + 全局汇总"""
    cs = clean_customers(data)
    ref = _d(data.get("generatedAt")) or datetime.datetime.now()
    agg = {}

    def blank():
        return dict(total=0, active=0, dead=0, followed7=0, followed30=0,
                    balance=0.0, monthly=0.0, expiring30=0, critical=0,
                    priority=0, riskwarn=0, nonWaInq=0, prec_sum=0.0, prec_n=0,
                    followed_mgr7=0)

    for c in cs:
        m = c.get("mgr") or "未分配"
        a = agg.setdefault(m, blank())
        a["total"] += 1
        if c.get("alive"):
            a["active"] += 1
        else:
            a["dead"] += 1
        if within(c.get("lastFollowTime"), ref, 7):
            a["followed7"] += 1
        if within(c.get("lastFollowTime"), ref, 30):
            a["followed30"] += 1
        a["balance"] += (c.get("cnyLeft") or 0)
        a["monthly"] += (c.get("monthlyAvg") or 0)
        dl = c.get("daysLeft")
        if dl is not None and 0 <= dl <= 30:
            a["expiring30"] += 1
        if c.get("risk") == "CRITICAL":
            a["critical"] += 1
        if c.get("judge") == "优先谈续费":
            a["priority"] += 1
        if c.get("judge") == "风险流失预警":
            a["riskwarn"] += 1
        a["nonWaInq"] += (c.get("nonWaInq") or 0)
        if c.get("prec") is not None:
            a["prec_sum"] += c["prec"]; a["prec_n"] += 1

    out = {}
    for m, a in agg.items():
        out[m] = dict(
            total=a["total"], active=a["active"], dead=a["dead"],
            followed7=a["followed7"], followed30=a["followed30"],
            cov30=round(a["followed30"] / a["total"], 4) if a["total"] else 0,
            balance=round(a["balance"], 1), monthly=round(a["monthly"], 1),
            expiring30=a["expiring30"], critical=a["critical"],
            priority=a["priority"], riskwarn=a["riskwarn"], nonWaInq=a["nonWaInq"],
            avgPrec=round(a["prec_sum"] / a["prec_n"], 1) if a["prec_n"] else None,
        )
    tot = blank()
    for a in agg.values():
        for k, v in a.items():
            tot[k] += v
    out["__TOTAL__"] = dict(
        total=tot["total"], active=tot["active"], dead=tot["dead"],
        followed7=tot["followed7"], followed30=tot["followed30"],
        cov30=round(tot["followed30"] / tot["total"], 4) if tot["total"] else 0,
        balance=round(tot["balance"], 1), monthly=round(tot["monthly"], 1),
        expiring30=tot["expiring30"], critical=tot["critical"],
        priority=tot["priority"], riskwarn=tot["riskwarn"], nonWaInq=tot["nonWaInq"],
        avgPrec=round(tot["prec_sum"] / tot["prec_n"], 1) if tot["prec_n"] else None,
    )
    return out


# ------------------------------------------------------------- 变化清单
WATCH = [("alive", "服务状态", lambda c: "活跃" if c.get("alive") else "已到期"),
         ("end", "服务期至", lambda c: c.get("end")),
         ("cnyLeft", "余额", lambda c: None if c.get("cnyLeft") is None else round(c["cnyLeft"], 1)),
         ("monthlyAvg", "月均消耗", lambda c: None if c.get("monthlyAvg") is None else round(c["monthlyAvg"], 1)),
         ("judge", "续费判断", lambda c: c.get("judge")),
         ("risk", "风险等级", lambda c: c.get("risk")),
         ("effectLevel", "效果评级", lambda c: c.get("effectLevel")),
         ("lastFollowTime", "最近跟进", lambda c: c.get("lastFollowTime"))]


def _norm(v):
    """数值归一：抹掉浮点精度噪声，避免把 42101.1 与 42101.100000000006 判为变化"""
    if isinstance(v, float):
        return round(v, 2)
    return v


def _ckey(c):
    """用客户 ID 作唯一键（数据集里有重名客户，不能用名字）"""
    return c.get("id") if c.get("id") is not None else c.get("name")


def changes_between(old_data, new_data):
    """对比两份快照，产出客户维度字段变化清单（按客户 ID 匹配，避免重名误判）"""
    if not old_data:
        return []
    om = {_ckey(c): c for c in (old_data.get("customers") or [])}
    rows = []
    for c in clean_customers(new_data):
        o = om.get(_ckey(c))
        for key, label, fn in WATCH:
            nv = fn(c)
            if o is None:
                rows.append(dict(date=new_data.get("generatedAt"), customer=c["name"],
                                 manager=c.get("mgr"), field=label, old="(新客户)", new=nv))
                continue
            ov = fn(o)
            if _norm(ov) != _norm(nv):
                rows.append(dict(date=new_data.get("generatedAt"), customer=c["name"],
                                 manager=c.get("mgr"), field=label, old=ov, new=nv))
    return rows


# ------------------------------------------------------------------ take
def take():
    data = load_data()
    dk = (data.get("generatedAt") or datetime.datetime.now().strftime("%Y-%m-%d"))[:10]
    key = dk.replace("-", "")
    os.makedirs(SNAP_DIR, exist_ok=True)
    # 同一天已有快照 → 追加时间后缀另存，绝不覆盖历史
    if os.path.exists(os.path.join(SNAP_DIR, f"data_{key}.js.gz")) or \
       os.path.exists(os.path.join(SNAP_DIR, f"data_{key}.js")):
        key = key + "-" + datetime.datetime.now().strftime("%H%M")

    # 1) 全量快照（压缩）
    with gzip.open(os.path.join(SNAP_DIR, f"data_{key}.js.gz"), "wt", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False))
    print(f"[快照] 全量已存档 data_{key}.js.gz")

    # 2) 指标（逐日×逐经理）
    m = json.load(open(METRICS, encoding="utf-8")) if os.path.exists(METRICS) else {}
    m[_fmt(key)] = metrics_of(data)      # 键与快照文件保持一致（含同日时间后缀）
    json.dump(m, open(METRICS, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[快照] 指标已并入 metrics.json（当前共 {len(m)} 个数据日）")

    # 3) 变化清单（与上一份快照比）
    keys = [k for k in list_snaps() if k != key]
    prev_key = keys[-1] if keys else None
    prev = read_snap(prev_key) if prev_key else None
    rows = changes_between(prev, data)
    chp = os.path.join(SNAP_DIR, f"changes_{key}.csv")
    if rows:
        with open(chp, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=["date", "customer", "manager", "field", "old", "new"])
            w.writeheader()
            w.writerows(rows)
        print(f"[快照] 较上一份({prev_key or '无'})有 {len(rows)} 处字段变化 → changes_{key}.csv")
    else:
        print(f"[快照] 较上一份({prev_key or '无'})无字段变化")

    # 4) 清理超期快照
    allk = list_snaps()
    for old in allk[:-KEEP]:
        for suf in (".js.gz", ".js"):
            p = os.path.join(SNAP_DIR, f"data_{old}{suf}")
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
    return key


# ---------------------------------------------------------------- report
def _delta(a, b, key):
    av, bv = a.get(key), b.get(key)
    if isinstance(av, (int, float)) and isinstance(bv, (int, float)):
        return round(bv - av, 4)
    return None


def report(key_a=None, key_b=None, as_json=False):
    ks = list_snaps()
    if key_a and key_b:
        ka, kb = key_a.replace("-", ""), key_b.replace("-", "")
    elif len(ks) >= 2:
        ka, kb = ks[-2], ks[-1]
    else:
        print("⚠️ 至少需要两份快照才能对比。当前快照：", ks or "（无）")
        return None
    m = json.load(open(METRICS, encoding="utf-8")) if os.path.exists(METRICS) else {}
    A, B = m.get(_fmt(ka)), m.get(_fmt(kb))
    if not A or not B:
        print(f"⚠️ metrics.json 里缺少 {ka} 或 {kb} 的指标，请先 snapshot take。")
        return None

    fields = [("total", "客户数"), ("active", "活跃"), ("followed7", "近7天跟进"),
              ("followed30", "近30天跟进"), ("cov30", "30天覆盖率"), ("balance", "余额合计"),
              ("monthly", "月均消耗合计"), ("expiring30", "30天内到期"),
              ("critical", "高危客户"), ("priority", "优先谈续费"),
              ("riskwarn", "风险流失预警"), ("nonWaInq", "非WA询盘"), ("avgPrec", "平均精准率")]
    mgrs = sorted([k for k in B.keys() if k != "__TOTAL__"])
    out = {"from": _fmt(ka), "to": _fmt(kb), "managers": {}}
    for mg in mgrs:
        a, b = A.get(mg, {}), B.get(mg, {})
        out["managers"][mg] = {f: {"a": a.get(f), "b": b.get(f), "d": _delta(a, b, f)} for f, _ in fields}

    if as_json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return out

    print(f"\n📊 快照对比：{_fmt(ka)} → {_fmt(kb)}\n" + "=" * 64)
    a, b = A.get("__TOTAL__", {}), B.get("__TOTAL__", {})
    print("【全局】")
    for f, lab in fields:
        d = _delta(a, b, f)
        arrow = "－" if d in (None, 0) else ("▲" if d > 0 else "▼")
        print(f"  {lab:<10} {a.get(f)} → {b.get(f)}   {arrow}{'' if d in (None,0) else abs(d)}")
    print("\n【各经理】")
    for mg in mgrs:
        a, b = A.get(mg, {}), B.get(mg, {})
        cores = [("total", "客户"), ("followed30", "近30跟进"), ("cov30", "覆盖率"),
                 ("balance", "余额"), ("critical", "高危"), ("priority", "优先续费")]
        seg = []
        for f, lab in cores:
            d = _delta(a, b, f)
            if d in (None, 0):
                seg.append(f"{lab} —")
            else:
                seg.append(f"{lab} {a.get(f)}→{b.get(f)} ({'+' if d>0 else ''}{d})")
        print(f"  {mg:<8} " + " ｜ ".join(seg))
    print("")
    return out


def _fmt(key):
    if len(key) >= 8:
        base = f"{key[:4]}-{key[4:6]}-{key[6:8]}"
        return base + "-" + key[9:] if len(key) > 8 and key[8] == "-" else base
    return key


# -------------------------------------------------------------------- CLI
def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "take"
    if cmd == "take":
        take()
    elif cmd == "list":
        ks = list_snaps()
        print(f"共 {len(ks)} 份快照：")
        for k in ks:
            print("  " + _fmt(k))
    elif cmd == "report":
        args = [a for a in sys.argv[2:] if not a.startswith("--")]
        report(args[0] if len(args) > 0 else None,
               args[1] if len(args) > 1 else None,
               as_json="--json" in sys.argv)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
