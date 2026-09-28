#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kw_context.py —— 把「听言诊断的全部表格」变成配词/邮件可用的依据摘要。

背景：配词与邮件生成过去只读了「搜索词明细 CSV + business_info」，完全没拉诊断表格，
而诊断接口返回的表格才是权威依据（画像精准度、产品下钻、身份下钻、国家下钻、正负案例…），
且表格数量随系统版本不同：2.0 有 6 张、2.5 有 8 张、3.0 有 10 张。

用法：
  python3 kw_context.py "杭州希纳博"            # 打印摘要
  python3 kw_context.py "杭州希纳博" --json     # 输出完整结构化 JSON
作为模块：
  from kw_context import build_context
  digest, raw = build_context("杭州希纳博")
"""
import json, os, sys, time

B = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.expanduser("~/.baixyn-api"))
import tingyan_pull as tp  # noqa: E402

CACHE = os.path.join(B, "生成结果", "_kw_context_cache.json")
CACHE_TTL = 12 * 3600  # 半天


def _num(x):
    try:
        return float(x)
    except Exception:
        return 0.0


def _load_cache():
    if os.path.exists(CACHE):
        try:
            return json.load(open(CACHE, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_cache(d):
    try:
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        json.dump(d, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception:
        pass


def fetch_raw(name, force=False):
    """拉取该客户的诊断快照（带缓存）"""
    c = _load_cache()
    if not force and name in c and time.time() - c[name].get("_ts", 0) < CACHE_TTL:
        return c[name]["snap"], c[name]["version"], "缓存"
    token = tp.load_token()
    hits = tp.find_customers(name, token)
    if not hits:
        raise RuntimeError(f"未找到客户「{name}」")
    if len(hits) > 1:
        exact = [h for h in hits if h["公司名"] == name]
        if not exact:
            raise RuntimeError(f"匹配到 {len(hits)} 个：{[h['公司名'] for h in hits[:8]]}")
        hits = exact
    cust = hits[0]
    snap, _diag, _done = tp.fetch_diagnosis(cust["user_id"], token)
    ver = tp.version_label(snap)
    c[name] = {"_ts": time.time(), "snap": snap, "version": ver, "user_id": cust["user_id"]}
    _save_cache(c)
    return snap, ver, "实时拉取"


def _portrait_rows(snap):
    pd = snap.get("precision_diagnosis") or {}
    rows = (pd.get("portrait_precision") or {}).get("rows") or []
    if rows:
        out = []
        for r in rows:
            like, dis = _num(r.get("like_count")), _num(r.get("dislike_count"))
            prec = _num(r.get("precision"))
            if prec == 0 and (like + dis):
                prec = like / (like + dis)
            out.append({"画像": r.get("portrait_name"), "样本": int(_num(r.get("total_count"))),
                        "赞": int(like), "踩": int(dis), "精准率": round(prec, 3)})
        return out
    out = []
    for r in pd.get("portraits") or []:
        like, dis = _num(r.get("like_count")), _num(r.get("dislike_count"))
        out.append({"画像": r.get("portrait_name"), "样本": int(_num(r.get("sample_size"))),
                    "赞": int(like), "踩": int(dis),
                    "精准率": round(like / (like + dis), 3) if (like + dis) else None})
    return out


def _drill(snap, key, name_field, min_sample=1):
    pd = snap.get("precision_diagnosis") or {}
    out = []
    for r in pd.get(key) or []:
        n = int(_num(r.get("sample_size")))
        if n < min_sample:
            continue
        like, dis = _num(r.get("like_count")), _num(r.get("dislike_count"))
        out.append({"名称": r.get(name_field),
                    "画像": r.get("portrait_name"),
                    "样本": n, "赞": int(like), "踩": int(dis),
                    "精准率": round(_num(r.get("precision")), 3)})
    return out


def _terms_from_csv(name, limit=400):
    """从「听言全量搜索词明细」CSV 取该客户的词级数据。
    2.0 客户的诊断快照不含词级样本，但 CSV 里有真实的标注数/精准率，必须用上。"""
    import csv, glob
    files = sorted(glob.glob(os.path.join(os.path.dirname(B), "听言全量搜索词明细_*.csv")))
    if not files:
        return []
    out = []
    for r in csv.DictReader(open(files[-1], encoding="utf-8-sig")):
        if (r.get("公司名") or "").strip() != name:
            continue
        ann = _num(r.get("标注数"))
        if ann <= 0:
            continue
        out.append({"搜索词": r.get("搜索词"), "样本": int(ann),
                    "精准率": round(_num(r.get("精准数")) / ann, 3) if ann else 0.0,
                    "来源": "CSV"})
    out.sort(key=lambda x: -x["样本"])
    return out[:limit]


def _search_terms(snap, name=None):
    pd = snap.get("precision_diagnosis") or {}
    data = pd.get("search_keywords") or snap.get("search_keywords") or []
    out = []
    for r in data:
        if "搜索词" in r:
            out.append({"搜索词": r.get("搜索词"), "样本": int(_num(r.get("样本数"))),
                        "精准率": _num(r.get("精准率"))})
        elif "search_keyword" in r:
            out.append({"搜索词": r.get("search_keyword"),
                        "样本": int(_num(r.get("sample_size"))),
                        "精准率": round(_num(r.get("precision")), 3)})
    # 快照没有词级样本时，回落到「全量搜索词明细」CSV（2.0 客户主要靠这个）
    if not [x for x in out if x["样本"]] and name:
        out = _terms_from_csv(name)
    return out


def _examples(snap, kind):
    pd = snap.get("precision_diagnosis") or {}
    d = (pd.get("feedback_examples") or {}).get(kind) or snap.get(f"{kind}_examples") or []
    out = []
    for r in d[:60]:
        out.append({"公司": r.get("公司名称") or r.get("name"),
                    "搜索词": r.get("搜索词") or r.get("search_keyword"),
                    "主营产品": r.get("主营产品") or r.get("main_product"),
                    "应用场景": r.get("应用场景") or r.get("application_scenarios")})
    return out


def build_context(name, force=False):
    """返回 (digest_markdown, raw_dict)"""
    snap, ver, src = fetch_raw(name, force)
    bi = snap.get("business_info") or {}
    pd = snap.get("precision_diagnosis") or {}
    s = pd.get("summary") or {}
    portraits = _portrait_rows(snap)
    countries = _drill(snap, "countries", "country_name")
    products = _drill(snap, "products", "product_word")
    identities = _drill(snap, "identities", "identity_name")
    terms = _search_terms(snap, name)
    pos, neg = _examples(snap, "positive"), _examples(snap, "negative")
    pmo = ((pd.get("planner_match_observations") or {}).get("rows") or [])
    flow = snap.get("acquisition_flow") or {}

    # 按实际「非空」的表统计（不同版本支持的表不同：2.0≈6 / 2.5≈8 / 3.0≈11）
    sheets = ["客户信息", "搜索词精准度分析"]
    if pos: sheets.append("典型案例正向")
    if neg: sheets.append("典型案例负向")
    if portraits: sheets.append("买家画像精准度")
    if pmo: sheets.append("匹配观察下钻")
    if countries: sheets.append("国家下钻")
    if products: sheets.append("产品下钻")
    if identities: sheets.append("身份下钻")
    if (flow.get("dimensions") or {}): sheets.append("获客链路维度分布")
    if pd.get("sample_records"): sheets.append("样本记录")

    L = []
    L.append(f"# 听言诊断表格依据 —— {name}")
    L.append(f"（数据版本 {ver}｜表格 {len(sheets)} 张：{'、'.join(sheets)}｜{src}）")
    L.append("")
    L.append("## 表1 客户信息")
    for k in ["公司名称", "主营产品", "主要业务", "细分行业", "卖家产品目录", "卖家应用场景",
              "想开拓的国家", "公司官网", "客户成功经理", "用户版本"]:
        v = bi.get(k)
        if v:
            L.append(f"- {k}：{str(v)[:300]}")
    L.append(f"- 样本量：{s.get('sample_size')}｜点赞 {s.get('like_count')}｜点踩 {s.get('dislike_count')}"
             f"｜样本精准率 {s.get('precision')}")
    L.append("")
    if portraits:
        L.append("## 表2 买家画像精准度【权威：画像增删删改以此表为准】")
        L.append("画像 ｜ 样本 ｜ 赞 ｜ 踩 ｜ 精准率")
        for r in sorted(portraits, key=lambda x: -x["精准率"]):
            L.append(f"- {r['画像']} ｜ {r['样本']} ｜ {r['赞']} ｜ {r['踩']} ｜ {r['精准率']}")
        L.append("")
    if countries:
        L.append("## 表3 国家下钻（样本≥1）")
        for r in sorted(countries, key=lambda x: -x["样本"])[:12]:
            L.append(f"- {r['名称']}：样本 {r['样本']}，精准率 {r['精准率']}")
        L.append("")
    if products:
        L.append("## 表4 产品下钻（样本≥2，按样本降序）")
        for r in sorted(products, key=lambda x: -x["样本"])[:20]:
            L.append(f"- {r['名称']}（{r['画像']}）：样本 {r['样本']}，精准率 {r['精准率']}")
        L.append("")
    if identities:
        L.append("## 表5 身份下钻（样本≥2，按样本降序）")
        for r in sorted(identities, key=lambda x: -x["样本"])[:20]:
            L.append(f"- {r['名称']}（{r['画像']}）：样本 {r['样本']}，精准率 {r['精准率']}")
        L.append("")
    if terms:
        good = sorted([t for t in terms if t["样本"]], key=lambda x: (-x["精准率"], -x["样本"]))[:20]
        bad = sorted([t for t in terms if t["样本"] and t["精准率"] == 0],
                     key=lambda x: -x["样本"])[:20]
        L.append("## 表6 搜索词精准度分析")
        L.append("### 表现最好")
        for t in good:
            L.append(f"- {t['搜索词']}：样本 {t['样本']}，精准率 {t['精准率']}")
        L.append("### 零转化毒瘤词（须排除）")
        for t in bad:
            L.append(f"- {t['搜索词']}：样本 {t['样本']}，精准率 0")
        L.append("")
    if pos:
        L.append("## 表7 典型案例（正向，真实买家样本）")
        for e in pos[:20]:
            L.append(f"- {e['公司']}｜搜索词 {e['搜索词']}｜主营 {str(e['主营产品'])[:60]}")
        L.append("")
    if neg:
        L.append("## 表8 典型案例（负向，误命中样本）")
        for e in neg[:20]:
            L.append(f"- {e['公司']}｜搜索词 {e['搜索词']}｜主营 {str(e['主营产品'])[:60]}")
        L.append("")
    if pmo:
        L.append("## 表9 匹配观察下钻（2.5 专用）")
        for r in pmo[:15]:
            L.append(f"- {r.get('company_name')}｜产品匹配 {r.get('product_matched')}"
                     f"｜身份匹配 {r.get('identity_matched')}｜AI判定身份 {r.get('inferred_decisive_identity')}")
        L.append("")
    dims = (flow.get("dimensions") or {})
    if dims:
        L.append("## 表10 获客链路维度分布")
        for r in (dims.get("portraits") or [])[:12]:
            L.append(f"- 画像 {r.get('portrait_name')}：线索 {r.get('clue_count')}，占比 {r.get('share')}")
        for r in (dims.get("search_keywords") or [])[:12]:
            L.append(f"- 搜索词 {r.get('search_keyword')}：线索 {r.get('clue_count')}，占比 {r.get('share')}")
        L.append("")

    raw = {"version": ver, "sheets": sheets, "portraits": portraits, "countries": countries,
           "products": products, "identities": identities, "search_terms": terms,
           "positive": pos, "negative": neg, "planner_observations": pmo,
           "business_info": bi, "summary": s}
    return "\n".join(L), raw


if __name__ == "__main__":
    nm = sys.argv[1]
    d, r = build_context(nm, force="--force" in sys.argv)
    if "--json" in sys.argv:
        print(json.dumps(r, ensure_ascii=False, indent=1))
    else:
        print(d)
