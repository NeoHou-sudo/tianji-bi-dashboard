#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一「客户品类」归类（替代旧的碎片化 品类中文映射.json）
======================================================================
旧问题：旧映射把 484 个产品词映到 100+ 个粒度不一的大类名（"化工材料/化工原料"、
"建材/建筑材料/建材/地板" 混用），还有带空格的脏值，且只用「第一个产品词」查表，
所以展示出来的品类肉眼可见地乱。

新做法：用【自家 AI】基于客户真实的「卖家产品目录 + 主营业务 + 细分行业」，
把每家客户归到**固定的大类**里（唯一答案、两级：大类 + 子类），结果落 品类归类.json。

- 固定大类（18 类）：机械设备 / 五金工具 / 电子电气 / 化工材料 / 医疗器械 / 汽车配件 /
  建材 / 家居用品 / 纺织服装 / 包装印刷 / 新能源 / 食品饮料 / 农业园林 / 户外运动 /
  宠物用品 / 礼品文具 / 安防消防 / 环保设备 / 其他
- 批量 + 指纹缓存：客户数据没变就跳过，只对新客户/变更客户归类（增量友好）
用法：python3 gen_category.py [--limit=20] [--batch=20]
"""
import json, os, sys, time, hashlib, re
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
BIZ = os.path.join(BASE, "business_info缓存.json")
DATA_JS = os.path.join(BASE, "assets", "data.js")
OUT = os.path.join(BASE, "品类归类.json")
AI_CFG = json.load(open(os.path.expanduser(
    os.environ.get("AI_CONFIG_FILE", "~/.baixyn-api/ai_config.json")), encoding="utf-8"))

CATS = ["机械设备", "五金工具", "电子电气", "化工材料", "医疗器械", "汽车配件", "建材",
        "家居用品", "纺织服装", "包装印刷", "新能源", "食品饮料", "农业园林", "户外运动",
        "宠物用品", "礼品文具", "安防消防", "环保设备", "其他"]

BATCH = 20
LIMIT = None
for a in sys.argv:
    if a.startswith("--batch="): BATCH = int(a.split("=")[1])
    if a.startswith("--limit="): LIMIT = int(a.split("=")[1])
RPM = 10
MIN_INTERVAL = 60.0 / RPM + 1.0
_last = [0.0]


def throttle():
    dt = time.time() - _last[0]
    if dt < MIN_INTERVAL:
        time.sleep(MIN_INTERVAL - dt)
    _last[0] = time.time()


def _key(x):
    try:
        return str(int(float(x)))
    except Exception:
        return str(x)


def names_of(v, cap=10):
    if isinstance(v, str):
        t = v.strip()
        if t[:1] in "[{":
            try: v = json.loads(t)
            except Exception: pass
    if isinstance(v, str):
        return [x.strip() for x in re.split(r"[、,，/]", v) if x.strip()][:cap]
    out = []

    def walk(x):
        if len(out) >= cap: return
        if isinstance(x, dict):
            if x.get("name"): out.append(str(x["name"]))
            for c in (x.get("children") or []): walk(c)
        elif isinstance(x, list):
            for c in x: walk(c)
        elif isinstance(x, str):
            for p in re.split(r"[、,，/]", x):
                p = p.strip()
                if p: out.append(p)
    walk(v)
    seen, res = set(), []
    for n in out:
        if n not in seen:
            seen.add(n); res.append(n)
    return res[:cap]


SYS = ("你是外贸行业资深分类专家。请把每家企业归入给定的固定大类之一，并给出一个简洁子类。"
       "严格只输出 JSON 数组，不要多余文字。")
PROMPT = """可选大类（必须从中选一个，不得自造）：%s

企业列表（JSON）：
%s

请为每家企业输出：{"id":<原样>,"cat":"<大类>","sub":"<子类，4~8字，如 数控刀具 / 空气净化器>"}
只依据给定信息判断；信息不足时归到「其他」。""" % ("、".join(CATS), "%s")


def ai_call(items):
    body = {"model": AI_CFG.get("model", "agnes-2.5-flash"),
            "messages": [{"role": "system", "content": SYS},
                         {"role": "user", "content": PROMPT % json.dumps(items, ensure_ascii=False)}],
            "temperature": 0.1}
    req = urllib.request.Request(
        AI_CFG["base_url"].rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + AI_CFG["api_key"]}, method="POST")
    with urllib.request.urlopen(req, timeout=240) as r:
        d = json.loads(r.read().decode("utf-8", "replace"))
    txt = re.sub(r"```(?:json)?", "", d["choices"][0]["message"]["content"])
    m = re.search(r"\[[\s\S]*\]", txt)
    return json.loads(m.group(0)) if m else []


def main():
    biz = json.load(open(BIZ, encoding="utf-8")) if os.path.exists(BIZ) else {}
    cache = json.load(open(OUT, encoding="utf-8")) if os.path.exists(OUT) else {}
    s = open(DATA_JS, encoding="utf-8").read()
    cs = [c for c in json.loads(s[s.find("=") + 1:].rstrip().rstrip(";"))["customers"] if c.get("id")]
    todo = []
    for c in cs:
        b = biz.get(c["name"]) or {}
        item = {"id": _key(c["id"]), "公司": c["name"],
                "产品目录": names_of(b.get("卖家产品目录")),
                "主营业务": (b.get("主要业务类型") or c.get("bizType") or "")[:60],
                "细分行业": names_of(b.get("细分行业"), 6)}
        fp = hashlib.md5(json.dumps(item, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:12]
        rec = cache.get(_key(c["id"]))
        if rec and rec.get("fp") == fp:
            continue
        todo.append((_key(c["id"]), fp, item))
    if LIMIT:
        todo = todo[:LIMIT]
    print(f"待归类 {len(todo)} 家（已缓存 {len(cache)}）", flush=True)
    if not todo:
        return
    done = 0
    for i in range(0, len(todo), BATCH):
        chunk = todo[i:i + BATCH]
        throttle()
        try:
            res = ai_call([x[2] for x in chunk])
        except Exception as e:
            print("  AI 调用失败:", e, flush=True)
            continue
        by = {_key(r.get("id")): r for r in res if isinstance(r, dict)}
        for cid, fp, item in chunk:
            r = by.get(cid) or {}
            cat = (r.get("cat") or "").strip()
            if cat not in CATS:
                cat = "其他"
            cache[cid] = {"name": item["公司"], "cat": cat,
                          "sub": (r.get("sub") or "").strip(), "fp": fp}
            done += 1
        json.dump(cache, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"[{min(i+BATCH,len(todo))}/{len(todo)}] 已归类 {done}", flush=True)
    print("完成", flush=True)


if __name__ == "__main__":
    main()
