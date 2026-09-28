#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
客户续费 BI 看板 · 数据生成脚本
用法: python3 generate_dashboard.py
读取同目录下的天枢售后看板 / 运营统览 / 听言主表 CSV，生成 assets/data.js
诊断阈值可在 DIAG 配置区调整
"""
import csv, json, os, sys
from datetime import datetime, timedelta
from collections import defaultdict, Counter

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.dirname(BASE)   # CSV 源文件在上一级目录
# 「数据基准日」：默认取运行当天；可用环境变量 AS_OF_DATE=YYYY-MM-DD 固定
_asof = os.environ.get("AS_OF_DATE", "").strip()
TODAY = datetime.strptime(_asof, "%Y-%m-%d") if _asof else datetime.now()

# ============ 配置区（阈值可调）============
DIAG = {
    "pool_min": 2000,        # 客户池不足
    "prec_min": 0.30,        # 准确率低
    "unlock_min": 0.10,      # 解锁率低
    "open_min": 0.06,        # 邮件打开差
    "email_inq_min": 0.005,  # 邮件询盘率低（0.5%）
}
# =========================================


# 品类中文映射
_CAT_MAP = {}
try:
    import json as _j
    _CAT_MAP = _j.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "品类中文映射.json"), encoding="utf-8"))
except Exception:
    _CAT_MAP = {}
_BIZ = {}
try:
    _BIZ = _j.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "business_info缓存.json"), encoding="utf-8"))
except Exception:
    _BIZ = {}


def _flat_any(v):
    """兼容三种形态：嵌套JSON / JSON字符串 / 纯字符串数组"""
    if not v:
        return ""
    if isinstance(v, list) and v and not isinstance(v[0], dict):
        return "、".join(str(x).strip() for x in v if str(x).strip())
    return _flat_catalog(v)


_CAT2 = {}
try:
    _CAT2 = _j.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "品类归类.json"), encoding="utf-8"))
except Exception:
    _CAT2 = {}
_SUM = {}
try:
    _SUM = _j.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "公司简述缓存.json"), encoding="utf-8"))
except Exception:
    _SUM = {}


def _idkey(x):
    try:
        return str(int(float(x)))
    except Exception:
        return str(x)


_SUPP_MAP = {}
try:
    _SUPP_MAP = _j.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "品类补充映射.json"), encoding="utf-8"))
except Exception:
    _SUPP_MAP = {}

def _first_product(t):
    pc = t.get("卖家产品目录") or ""
    if pc:
        flat = _flat_catalog(pc)
        if flat:
            f = flat.split("、")[0].strip()
            if f: return f[:40]
    bt = t.get("主要业务类型") or ""
    if bt:
        return bt.split("/")[0].strip()[:40]
    return ""

def num(v):
    try: return float(v)
    except: return None

def _flat_catalog(raw):
    """把听言的产品目录(嵌套JSON)压平成产品名列表"""
    if not raw: return ""
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return str(raw).strip()
    names = []
    def walk(nodes):
        if not isinstance(nodes, list): return
        for n in nodes:
            if isinstance(n, str):                       # 纯字符串数组（如「卖家应用场景」）
                t = n.strip().rstrip(";").strip()
                if t: names.append(t)
                continue
            if not isinstance(n, dict): continue
            if n.get("name"): names.append(str(n["name"]).strip())
            if n.get("children"): walk(n["children"])
    walk(data)
    # 去重保序
    seen = set(); out = []
    for x in names:
        if x and x not in seen:
            seen.add(x); out.append(x)
    return "、".join(out)
def dt(s):
    if not s: return None
    try: return datetime.strptime(s[:10], "%Y-%m-%d")
    except: return None
def find(pattern):
    for f in os.listdir(DATA_DIR):
        if pattern in f and f.endswith(".csv"):
            return os.path.join(DATA_DIR, f)
    raise SystemExit(f"找不到含 '{pattern}' 的 CSV")

after = list(csv.DictReader(open(find("天枢售后看板全量"), encoding="utf-8-sig")))
ops = {}
for r in csv.DictReader(open(find("天枢Zoe运营统览"), encoding="utf-8-sig")):
    ops[str(r["customerId"])] = r
ty = {}
for r in csv.DictReader(open(find("听言客户使用情况检索表"), encoding="utf-8-sig")):
    ty[r["公司名"]] = r

customers = []
for c in after:
    name = c["customerName"]; cid = str(c.get("customerId"))
    op = ops.get(cid, {}); t = ty.get(name, {})
    _biz = _BIZ.get(name, {}) or {}
    ver = t.get("用户版本") or c.get("version") or ""
    is3 = "OntoZ" in ver
    rate = 17 if is3 else 1
    ptsT = num(c.get("pointsTotal")); ptsR = num(c.get("pointsRemaining"))
    used = (ptsT - ptsR) if (ptsT is not None and ptsR is not None) else None
    cnyLeft = round(ptsR / rate, 2) if ptsR is not None else None
    cnyUsed = round(used / rate, 2) if used is not None and used > 0 else None
    sd = dt(c.get("serviceStartTime")); ed = dt(c.get("serviceEndTime"))
    svcDays = max(1, (TODAY - sd).days) if sd else None
    dailyAvg = round(cnyUsed / svcDays, 2) if (cnyUsed and svcDays) else None
    monthlyAvg = round(dailyAvg * 30, 2) if dailyAvg else None
    daysToBurn = None; burnDate = None
    if dailyAvg and dailyAvg > 0 and cnyLeft is not None:
        if cnyLeft <= 0:
            daysToBurn = 0; burnDate = TODAY.strftime("%Y-%m-%d")
        else:
            daysToBurn = round(cnyLeft / dailyAvg)
            burnDate = (TODAY + timedelta(days=daysToBurn)).strftime("%Y-%m-%d")
    expiryType = None
    if burnDate and ed: expiryType = "点数到期" if dt(burnDate) <= ed else "时长到期"
    elif burnDate: expiryType = "点数到期"

    emailI = num(op.get("emailInquiryCount")) or 0
    waI = num(op.get("whatsappInquiryCount")) or 0
    marketed = num(op.get("marketedClueCount")) or 0
    realI = num(op.get("inquiryClueCount")) or 0
    nonWa = max(0, realI - waI)
    ratio = round(nonWa / marketed * 100, 2) if marketed > 0 else None
    price = round(cnyUsed / nonWa, 2) if (cnyUsed and nonWa > 0) else None
    prec = num(t.get("整体精准率"))
    if prec is None:                      # 主表 CSV 该列为空 → 用「听言基础信息」里的精准率兜底
        prec = num(_biz.get("精准率"))
    # 「近期精准度」= 听言最近 100 条标注的精准率（取听言 business_info，CSV 该列常为空）
    prec100 = num(_biz.get("最近100条精准率"))
    if prec100 is None:
        prec100 = num(t.get("最近100条精准率"))
    # 「最近标注时间」= 听言侧最后一次标注时间
    lastAnnot = (_biz.get("最近标注时间") or t.get("最近标注时间") or "").strip()
    lastAnnot = (lastAnnot[:16].replace("T", " ") or None) if lastAnnot else None
    lastDays = (TODAY - dt(c.get("lastLogin"))).days if dt(c.get("lastLogin")) else None
    dl = num(c.get("daysToExpire"))

    # ===== 诊断 =====
    pool = num(op.get("totalClueCount")) or 0
    unlockCnt = num(op.get("unlockedCount")) or 0
    unlockRatio = (unlockCnt / pool) if pool > 0 else 0
    reachedEmail = num(op.get("reachedEmailCount")) or 0
    openRatio = num(op.get("openEmailRatio"))
    emailInqRate = (emailI / reachedEmail) if reachedEmail > 0 else None

    # ===== 诊断（按客户漏斗层级：量→准确率→解锁→邮件打开→邮件转化）=====
    pool = num(op.get("totalClueCount")) or 0
    unlockCnt = num(op.get("unlockedCount")) or 0
    unlockRatio = (unlockCnt / pool) if pool > 0 else 0
    reachedEmail = num(op.get("reachedEmailCount")) or 0
    openRatio = num(op.get("openEmailRatio"))
    emailInqRate = (emailI / reachedEmail) if reachedEmail > 0 else None

    # 按漏斗顺序收集命中的问题标签（越靠前越上游）
    funnel = []
    if pool < DIAG["pool_min"]:
        funnel.append("客户池不足")                      # L1 量
    if prec is not None and prec < DIAG["prec_min"]:
        funnel.append("准确率低")                        # L2 词（准确才解锁）
    if unlockRatio < DIAG["unlock_min"]:
        funnel.append("解锁率低(3.0可默认邮件)" if is3 else "未解锁受限(2.0无法邮件)")  # L3 解锁
    if reachedEmail > 0 and openRatio is not None and openRatio < DIAG["open_min"]:
        funnel.append("邮件打开差")                      # L4 打开
    if reachedEmail > 0 and emailInqRate is not None and emailInqRate < DIAG["email_inq_min"]:
        funnel.append("邮件转化差")                      # L5 转化

    diag = funnel[0] if funnel else "健康"               # 根因（最上游）
    cascade = funnel[1:]                                 # 连带问题（上游导致）
    tags = funnel if funnel else ["健康"]                # 全部标签（共存，按漏斗序）

    ADVICE = {
        "客户池不足": "扩充线索量：加大搜索词覆盖、提升池子规模",
        "准确率低": "调整配词：删除宽泛词，增加'设备+身份'细分词（注意：不准的客户通常不会被解锁，会连带影响下游）",
        "未解锁受限(2.0无法邮件)": "2.0未解锁无法邮件营销——先解锁优质线索的公司信息，邮件才发得出去",
        "解锁率低(3.0可默认邮件)": "3.0可发默认邮件，建议提升解锁率以拿到联系方式、扩大触达面",
        "邮件打开差": "优化邮件标题/发送时间，检查域名信誉",
        "邮件转化差": "优化邮件正文与CTA，突出客户利益点",
        "健康": "保持维护，关注询盘跟进",
    }
    advice = ADVICE.get(diag, "")
    if cascade:
        advice += f"｜连带问题：{'、'.join(cascade)}（多为上游问题所致，先解上游）"

    score = 0
    if ratio is not None:
        if ratio >= 0.9: score += 2
        elif ratio >= 0.4: score += 1
    if price is not None:
        if price <= 1000: score += 2
        elif price <= 2000: score += 1
    if lastDays is not None and lastDays <= 30: score += 1

    # === 效果评级（判断服务效果好不好）===
    eff = 0
    if prec is not None:
        if prec >= 0.6: eff += 2
        elif prec >= 0.4: eff += 1
    if nonWa >= 10: eff += 2
    elif nonWa >= 3: eff += 1
    if ratio is not None:
        if ratio >= 0.9: eff += 2
        elif ratio >= 0.4: eff += 1
    if price is not None and price <= 1000: eff += 1
    if eff >= 5: effect_level = "A·优质标杆"
    elif eff >= 4: effect_level = "B·效果良好"
    elif eff >= 2: effect_level = "C·效果一般"
    else: effect_level = "D·待改善"

    if dl is None: judge, diff = "数据缺失", "待定"
    elif dl < 0: judge, diff = "已到期待挽回", "高"
    elif dl <= 30 and score >= 3: judge, diff = "优先谈续费", "低"
    elif dl <= 30: judge, diff = "风险流失预警", "高"
    elif dl <= 90 and score >= 3: judge, diff = "提前铺续费", "低"
    elif score >= 3: judge, diff = "健康维护", "低"
    elif score == 2: judge, diff = "常规维护", "中"
    else: judge, diff = "效果存疑需干预", "中"

    status = c.get("serviceStatus") or ""
    customers.append({
        "id": num(cid), "name": name, "mgr": c.get("serviceName") or "未分配", "ver": ver,
        "status": status, "alive": ("已到期" not in status),
        "start": c.get("serviceStartTime") or "", "end": c.get("serviceEndTime") or "",
        "daysLeft": int(dl) if dl is not None else None, "svcDays": svcDays,
        "ptsLeft": ptsR, "cnyLeft": cnyLeft, "cnyUsed": cnyUsed,
        "dailyAvg": dailyAvg, "monthlyAvg": monthlyAvg,
        "daysToBurn": daysToBurn, "burnDate": burnDate, "expiryType": expiryType,
        "pool": int(pool), "unlockRatio": round(unlockRatio, 3),
        "marketed": int(marketed), "emailInq": int(emailI), "waInq": int(waI), "nonWaInq": int(nonWa),
        "reachedEmail": int(reachedEmail),
        "openRatio": round(openRatio, 3) if openRatio is not None else None,
        "emailInqRate": round(emailInqRate * 100, 3) if emailInqRate is not None else None,
        "inqRatio": ratio, "avgPrice": price, "score": score,
        "risk": c.get("riskLevel") or "", "riskScore": num(c.get("riskScore")) or 0,
        "prec": round(prec * 100, 1) if prec is not None else None,
        "prec100": round(prec100 * 100, 1) if prec100 is not None else None,
        "lastAnnot": lastAnnot,
        "lastLogin": lastDays, "judge": judge, "diff": diff,
        "diag": diag, "diagTags": tags, "cascade": cascade, "advice": advice,
        "effectLevel": effect_level, "effectScore": eff,
        "bizType": (t.get("主要业务类型") or "").strip() or _flat_any(_biz.get("主要业务类型")),
        # 主营产品/业务：优先用「AI 总结的产品线」（可读性好），没有才回退到原始产品目录关键词
        "productCatalog": ((_SUM.get(_idkey(cid)) or {}).get("product_line")
                           or _flat_catalog(t.get("卖家产品目录"))
                           or _flat_any(_biz.get("卖家产品目录"))),
        "scenario": (t.get("卖家应用场景") or "").strip() or _flat_any(_biz.get("卖家应用场景")),
        "targetCountries": (t.get("想开拓的国家") or "").strip(),
        "catCn": ((_CAT2.get(_idkey(cid)) or {}).get("cat")
                  or _SUPP_MAP.get(name, _CAT_MAP.get(_first_product(t), ""))),
        "catSub": (_CAT2.get(_idkey(cid)) or {}).get("sub") or "",
        "summary": (_SUM.get(_idkey(cid)) or {}).get("summary") or "",
        "productLine": (_SUM.get(_idkey(cid)) or {}).get("product_line") or "",
    })

mg = defaultdict(lambda: {"count": 0, "aliveCnt": 0, "cnyLeft": 0, "cnyUsed": 0, "avgSum": 0, "judges": Counter(), "risks": Counter(), "rs": 0, "exp": 0})
for c in customers:
    m = mg[c["mgr"]]; m["count"] += 1
    m["cnyLeft"] += c["cnyLeft"] or 0; m["cnyUsed"] += c["cnyUsed"] or 0
    if c["alive"]: m["aliveCnt"] += 1; m["avgSum"] += c["monthlyAvg"] or 0
    m["judges"][c["judge"]] += 1; m["risks"][c["risk"]] += 1; m["rs"] += c["riskScore"] or 0
    if c["daysLeft"] is not None and 0 <= c["daysLeft"] <= 30: m["exp"] += 1
managers = [{"name": n, "count": m["count"], "aliveCnt": m["aliveCnt"],
             "cnyLeft": round(m["cnyLeft"], 2), "cnyUsed": round(m["cnyUsed"], 2),
             "monthlyAvgAlive": round(m["avgSum"], 2), "judges": dict(m["judges"]),
             "risks": dict(m["risks"]), "avgRisk": round(m["rs"] / m["count"], 1),
             "expiring": m["exp"]} for n, m in sorted(mg.items(), key=lambda x: -x[1]["count"])]

# ===== 合并售后服务记录 =====
_svc_file = os.path.join(BASE, "服务记录_最新.json")
if os.path.exists(_svc_file):
    try:
        _svc = json.load(open(_svc_file, encoding="utf-8"))
        for c in customers:
            r = _svc.get(c["name"])
            if not r:
                c["lastFollowTime"] = c["lastFollowText"] = c["lastFollowBy"] = None
                continue
            st = r.get("serviceTime")
            c["lastFollowTime"] = datetime.fromtimestamp(st / 1000).strftime("%Y-%m-%d") if st else None
            c["lastFollowBy"] = r.get("creator")
            content = r.get("content") or {}
            if isinstance(content, str):
                c["lastFollowText"] = content
            else:
                c["lastFollowText"] = "；".join(str(v) for v in content.values() if v)
    except Exception as e:
        print("服务记录合并失败:", e)
else:
    for c in customers:
        c["lastFollowTime"] = c["lastFollowText"] = c["lastFollowBy"] = None

# 数据快照时间：优先用抓取脚本记录的真实时刻，否则用当前时刻
_snap_file = os.path.join(BASE, "_snapshot_time.txt")
_snap_time = ""
if os.path.exists(_snap_file):
    try:
        _snap_time = open(_snap_file, encoding="utf-8").read().strip()
    except Exception:
        _snap_time = ""
if not _snap_time:
    _snap_time = datetime.now().strftime("%Y-%m-%d %H:%M")
data = {"generatedAt": TODAY.strftime("%Y-%m-%d"), "snapshotTime": _snap_time,
        "customers": customers, "managers": managers}
out = os.path.join(BASE, "assets", "data.js")
os.makedirs(os.path.dirname(out), exist_ok=True)
open(out, "w", encoding="utf-8").write("window.DASHBOARD_DATA = " + json.dumps(data, ensure_ascii=False) + ";")
print(f"[OK] {len(customers)}客户 已写入 assets/data.js")
print("主诊断:", dict(Counter(c["diag"] for c in customers).most_common()))
