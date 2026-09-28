#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成「公司简述 / 产品线简述」——供 BI 展示
======================================================================
数据来源：business_info缓存.json（听言的用户基础信息，只读接口拉的）
生成方式：用【自家 AI】基于基础信息写摘要（不去调听言的 AI 诊断接口）

- 批量：每次送 10 家，省调用次数
- 缓存：按「客户ID + 输入指纹」命中即跳过，重跑不重复花钱
- 限流：与 gen_fix 一致（默认每分钟 10 次）

用法：
  python3 gen_summary.py                 # 生成/补齐全部
  python3 gen_summary.py --limit 20      # 只处理前 20 家（试跑）
  python3 gen_summary.py --batch 10      # 每批客户数
输出：公司简述缓存.json  { 客户ID: {name, summary, product_line, fp} }
"""
import json, os, sys, time, hashlib, re
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
BIZ = os.path.join(BASE, "business_info缓存.json")
DATA_JS = os.path.join(BASE, "assets", "data.js")
OUT = os.path.join(BASE, "公司简述缓存.json")
AI_CFG = json.load(open(os.path.expanduser(
    os.environ.get("AI_CONFIG_FILE", "~/.baixyn-api/ai_config.json")), encoding="utf-8"))

BATCH = 10
for a in sys.argv:
    if a.startswith("--batch="):
        BATCH = int(a.split("=")[1])
LIMIT = None
for a in sys.argv:
    if a.startswith("--limit="):
        LIMIT = int(a.split("=")[1])

RPM = 10
MIN_INTERVAL = 60.0 / RPM + 1.0
_last = [0.0]


def throttle():
    dt = time.time() - _last[0]
    if dt < MIN_INTERVAL:
        time.sleep(MIN_INTERVAL - dt)
    _last[0] = time.time()


def _key(x):
    """把 ID 归一成字符串（模型可能回 1259 或 1259.0）"""
    try:
        return str(int(float(x)))
    except Exception:
        return str(x)


def names_of(v, cap=12):
    """把嵌套的行业/产品目录拍平成名字列表（兼容 JSON 字符串形态）"""
    if isinstance(v, str):
        t = v.strip()
        if t[:1] in "[{":
            try:
                v = json.loads(t)
            except Exception:
                pass
    if isinstance(v, str):
        parts = [x.strip() for x in re.split(r"[、,，/]", v) if x.strip()]
        return parts[:cap]
    out = []

    def walk(x):
        if len(out) >= cap:
            return
        if isinstance(x, dict):
            if x.get("name"):
                out.append(str(x["name"]))
            for c in (x.get("children") or []):
                walk(c)
        elif isinstance(x, list):
            for c in x:
                walk(c)
        elif isinstance(x, str):
            for p in re.split(r"[、,，/]", x):
                p = p.strip()
                if p:
                    out.append(p)
    walk(v)
    seen, res = set(), []
    for n in out:
        if n not in seen:
            seen.add(n); res.append(n)
    return res[:cap]


def load_customers():
    s = open(DATA_JS, encoding="utf-8").read()
    return json.loads(s[s.find("=") + 1:].rstrip().rstrip(";"))["customers"]


def build_input(c, biz):
    b = biz.get(c["name"]) or {}
    return {
        "id": _key(c.get("id")),
        "公司名称": b.get("公司名称") or c.get("name"),
        "细分行业": names_of(b.get("细分行业")),
        "卖家产品目录": names_of(b.get("卖家产品目录")),
        "卖家应用场景": names_of(b.get("卖家应用场景")),
        "公司官网": b.get("公司官网") or "",
        "想开拓的国家": b.get("想开拓的国家") or "",
    }


SYS = ("你是外贸 B2B 行业的资深分析师。请根据给定的企业与产品信息，输出简洁、专业、"
       "可直接展示给业务主管看的中文摘要。严格只输出 JSON 数组，不要任何多余文字。")
PROMPT = """请为下面每一家企业生成两个字段：
- summary（公司简述）：1~2 句，说明这是什么公司、做什么、面向什么客户/市场；
- product_line（产品线简述）：1~2 句，概括其核心产品线与典型应用场景。
要求：只依据给定信息，不要编造；每条 60 字以内；语气专业、书面。

企业列表（JSON）：
%s

请输出 JSON 数组，元素格式：{"id":<原样>, "summary":"...", "product_line":"..."}"""


def ai_call(items):
    body = {"model": AI_CFG.get("model", "agnes-2.5-flash"),
            "messages": [{"role": "system", "content": SYS},
                         {"role": "user", "content": PROMPT % json.dumps(items, ensure_ascii=False)}],
            "temperature": 0.3}
    req = urllib.request.Request(
        AI_CFG["base_url"].rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + AI_CFG["api_key"]},
        method="POST")
    with urllib.request.urlopen(req, timeout=180) as r:
        d = json.loads(r.read().decode("utf-8", "replace"))
    txt = d["choices"][0]["message"]["content"]
    txt = re.sub(r"```(?:json)?", "", txt)          # 去掉 ```json 围栏
    m = re.search(r"\[[\s\S]*\]", txt)
    return json.loads(m.group(0)) if m else []


def main():
    biz = json.load(open(BIZ, encoding="utf-8")) if os.path.exists(BIZ) else {}
    cache = json.load(open(OUT, encoding="utf-8")) if os.path.exists(OUT) else {}
    cs = [c for c in load_customers() if c.get("id")]
    todo = []
    for c in cs:
        inp = build_input(c, biz)
        fp = hashlib.md5(json.dumps(inp, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:12]
        rec = cache.get(_key(c["id"]))
        if rec and rec.get("fp") == fp:
            continue
        todo.append((c["id"], fp, inp))
    if LIMIT:
        todo = todo[:LIMIT]
    print(f"待生成 {len(todo)} 家（已缓存 {len(cache)}）", flush=True)
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
        by_id = {_key(r.get("id")): r for r in res if isinstance(r, dict)}
        for cid, fp, inp in chunk:
            r = by_id.get(_key(cid)) or {}
            cache[_key(cid)] = {"name": inp["公司名称"],
                                    "summary": (r.get("summary") or "").strip(),
                                    "product_line": (r.get("product_line") or "").strip(),
                                    "fp": fp}
            done += 1
        json.dump(cache, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"[{min(i+BATCH,len(todo))}/{len(todo)}] 已生成 {done}", flush=True)
    print("完成", flush=True)


if __name__ == "__main__":
    main()
