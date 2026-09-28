#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
客户修正文件生成引擎
- gen_keywords(customer, version) -> 生成配词方案 (2.5→xlsx / 3.0→txt，对外版)
- gen_email(customer) -> 生成3轮邮件提示词 (txt)
调用 Agnes AI + skill 自带的生成器脚本
"""
import json, os, csv, re, subprocess, sys, time, urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.dirname(BASE)
OUT_DIR = os.path.join(BASE, "生成结果")
_cfg_path = os.path.expanduser(os.environ.get("AI_CONFIG_FILE", "~/.baixyn-api/ai_config.json"))
AI_CFG = json.load(open(_cfg_path)) if os.path.exists(_cfg_path) else {}
AI_CFG["base_url"] = os.environ.get("AI_BASE_URL") or AI_CFG.get("base_url", "")
AI_CFG["api_key"] = os.environ.get("AI_API_KEY") or AI_CFG.get("api_key", "")
if not AI_CFG.get("api_key"):
    print("[warn] 未配置 AI 密钥：请设置环境变量 AI_API_KEY，或提供 ~/.baixyn-api/ai_config.json", file=sys.stderr)
KW_SCRIPTS = os.path.expanduser("~/.config/loomy-opencode/skills/company-private-keyword-config/scripts")
TERMS_CSV = None  # 听言搜索词明细，惰性加载

# ===== 免费版限流配置（Agnes 免费/默认用户 RPM=10）=====
RPM_LIMIT = 10                 # 每分钟请求上限
MIN_INTERVAL = 60.0 / RPM_LIMIT + 1.0   # 每次请求最小间隔（秒），留 1s 余量 → 7s
DAILY_LIMIT = 300              # 每日生成上限（可调）
_last_call = [0.0]
_cache = {}
CACHE_FILE = os.path.join(OUT_DIR, "_cache.json")
_usage_file = os.path.join(OUT_DIR, "_usage.json")


def _load_cache():
    global _cache
    if os.path.exists(CACHE_FILE):
        try:
            _cache = json.load(open(CACHE_FILE, encoding="utf-8"))
        except Exception:
            _cache = {}


def _save_cache():
    try:
        os.makedirs(OUT_DIR, exist_ok=True)
        json.dump(_cache, open(CACHE_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception:
        pass


def cache_key(cust, kind):
    """缓存键：客户名 + 产物类型 + 数据指纹（精准率/池子/余额，变了就重算）；
    邮件产物额外带上「邮件提示词规范」的指纹，规范更新后自动失效重算。"""
    fp = f"{cust.get('prec')}|{cust.get('pool')}|{cust.get('cnyLeft')}|{cust.get('nonWaInq')}"
    if kind == "email":
        try:
            st = os.stat(EMAIL_SPEC_FILE)
            fp += f"|spec{int(st.st_mtime)}-{st.st_size}"
        except Exception:
            fp += "|spec?"
    return f"{cust['name']}||{kind}||{fp}"


def daily_used():
    today = time.strftime("%Y%m%d")
    if os.path.exists(_usage_file):
        try:
            d = json.load(open(_usage_file, encoding="utf-8"))
            return d.get(today, 0)
        except Exception:
            return 0
    return 0


def _inc_usage():
    today = time.strftime("%Y%m%d")
    d = {}
    if os.path.exists(_usage_file):
        try:
            d = json.load(open(_usage_file, encoding="utf-8"))
        except Exception:
            d = {}
    d[today] = d.get(today, 0) + 1
    json.dump(d, open(_usage_file, "w", encoding="utf-8"))


def _throttle():
    """按 RPM 限制节流：保证两次请求间隔 >= MIN_INTERVAL"""
    gap = time.time() - _last_call[0]
    if gap < MIN_INTERVAL:
        wait = MIN_INTERVAL - gap
        print(f"[throttle] 等待 {wait:.1f}s（RPM={RPM_LIMIT} 限流）", file=sys.stderr)
        time.sleep(wait)
    _last_call[0] = time.time()


def call_ai(system, user, max_tokens=6000, temperature=0.4, retries=4):
    """流式调用，避免长响应被服务器断开；带自动重试"""
    import time
    last_err = None
    for attempt in range(retries):
        try:
            _throttle()   # RPM 限流
            _inc_usage()  # 用量计数
            body = json.dumps({
                "model": AI_CFG["model"],
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "max_tokens": max_tokens, "temperature": temperature, "stream": True,
            }).encode("utf-8")
            req = urllib.request.Request(
                AI_CFG["base_url"] + "/chat/completions", data=body,
                headers={"Content-Type": "application/json", "Authorization": "Bearer " + AI_CFG["api_key"],
                         "Accept": "text/event-stream"})
            t0 = time.time()
            parts = []
            with urllib.request.urlopen(req, timeout=280) as resp:
                for raw in resp:
                    line = raw.decode("utf-8", errors="ignore").strip()
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        d = json.loads(payload)
                        delta = d.get("choices", [{}])[0].get("delta", {})
                        c = delta.get("content")
                        if c:
                            parts.append(c)
                    except Exception:
                        continue
            out = "".join(parts)
            if out:
                print(f"[AI] {time.time()-t0:.1f}s, 输出{len(out)}字符 (第{attempt+1}次)", file=sys.stderr)
                return out
            last_err = RuntimeError("空响应")
        except Exception as e:
            last_err = e
            print(f"[AI] 第{attempt+1}次失败: {type(e).__name__}", file=sys.stderr)
        time.sleep(2 * (attempt + 1))
    raise last_err

def _find(pattern):
    for f in os.listdir(DATA_DIR):
        if pattern in f and f.endswith(".csv"):
            return os.path.join(DATA_DIR, f)
    return None

def load_customer_terms(name, limit=80):
    """从听言全量搜索词明细读取该客户现有搜索词（含精准率），按精准率降序"""
    global TERMS_CSV
    if TERMS_CSV is None:
        TERMS_CSV = _find("听言全量搜索词明细")
    if not TERMS_CSV: return []
    out = []
    with open(TERMS_CSV, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r.get("公司名") == name:
                out.append({"term": r.get("搜索词",""), "prec": r.get("精准率",""), "n": r.get("标注数","")})
    out.sort(key=lambda x: float(x["prec"] or 0), reverse=True)
    return out[:limit]

def _extract_json(s):
    """从 AI 回复里抽出 JSON（容忍 markdown 代码块）"""
    s = s.strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", s)
    if m: s = m.group(1).strip()
    i, j = s.find("{"), s.rfind("}")
    if i >= 0 and j > i: s = s[i:j+1]
    return json.loads(s)

KW_SYSTEM = """你是资深外贸 B2B 获客配词专家。你按"产品词 × 买家身份词"构造精准英文搜索词，
帮中国出口企业找到海外真实买家。核心原则：
1. 搜索词要具体（设备/产品 + 制造商/进口商/批发商等身份），避免过于宽泛的大词；
2. 排除词用于剔除非目标（如零售商、个人消费者、无关行业）；
3. 只输出 JSON，不要任何解释文字。"""

def gen_keywords(cust, version, use_cache=True):
    """生成配词方案。version: '2.5' 或 '3.0'。命中缓存则直接复用。返回 (文件路径, 方案JSON, log)"""
    _load_cache()
    ck = cache_key(cust, "kw" + version)
    if use_cache and ck in _cache:
        rec = _cache[ck]
        if os.path.exists(rec.get("file", "")):
            print(f"[cache] 命中 {cust['name']} kw{version}", file=sys.stderr)
            return rec["file"], {}, "缓存命中"
    terms = load_customer_terms(cust["name"])
    terms_txt = "\n".join(f"- {t['term']}（精准率{t['prec'] or '未标注'}）" for t in terms[:35]) or "（暂无历史搜索词）"
    # ★ 关键修复：拉取听言诊断的「全部表格」作为权威依据（此前完全没拉，只用了搜索词 CSV）
    diag_txt = ""
    try:
        import kw_context
        diag_txt, _ = kw_context.build_context(cust["name"])
    except Exception as _e:
        print(f"[warn] 诊断表格拉取失败，本次退化为仅用搜索词：{_e}", file=sys.stderr)
    import json as _j
    if version == "2.5":
        schema = '''{
  "company_name": "公司名",
  "target_countries": "全球",
  "search_terms": ["共用搜索词1", "共用搜索词2", "..."],
  "personas": [
    {"name":"买家画像一：xxx制造商 (XXX Manufacturer)","products":"目标买家经营产品,逗号分隔","exclude_products":"非目标产品,逗号分隔","identity":"Manufacturer,Factory","extra_identity":"补充身份","exclude_identity":"排除身份"}
  ],
  "filter_brands": []
}'''
        ver_desc = "2.5 版本：所有画像共用一份 search_terms；排除词只放各画像的 exclude_products"
    else:
        schema = '''{
  "company_name": "公司名",
  "company_desc": "客户背景一段话",
  "positioning": "定位说明一段话",
  "personas": [
    {"name":"画像一：xxx (XXX)(梯队：一)","search_terms":["搜索词1","搜索词2"],"products":["产品1"],"exclude_products":["排除1"],"identity":["Manufacturer"],"extra_identity":["Importer"],"exclude_identity":["Retailer"],"why":"为什么选","features":"特点","attack":"进攻点","reason":"为何列为画像"}
  ]
}'''
        ver_desc = "3.0 版本：每个画像有独立的 search_terms；identity/extra_identity/exclude_identity 必须是数组"
    user = f"""请为以下中国出口企业生成配词方案（{version} 版本，对外版）。

公司名：{cust['name']}
主营方向：请根据下方诊断表格中的「卖家产品目录 / 公司信息 / 典型案例」推断
诊断问题：{cust.get('diag','')}（问题链：{' → '.join(cust.get('diagTags',[]))}）
已知短板：整体精准率 {cust.get('prec','—')}%，客户池 {cust.get('pool',0)} 条

════════ 第一部分：听言诊断表格（★权威依据，最高优先级）════════
{diag_txt or '（本次未能拉取到诊断表格，请仅依据下方搜索词，并在输出后提示需要补拉表格）'}
════════ 第二部分：该客户现有搜索词（含精准率，越高越准）════════
{terms_txt}

请严格按以下规则生成：
1. 【画像以表格为准】「买家画像精准度」表是该客户画像的权威结论：
   - 精准率 ≥ 0.4 的画像 → 保留并强化为主力画像；
   - 精准率 0 < x < 0.4 的画像 → 可保留但必须收窄词面；
   - 精准率 = 0 的画像 → 一律删除，并其身份词写入 exclude_identity；
   - 严禁凭常识新增该表里没有、且数据不支持的全新画像。
2. 【产品以表格为准】产品词优先取「产品下钻」表中样本≥2 且精准率高的词；
   同时对「搜索词精准度分析」里精准率=0 且样本多的词做同族反推，写入 exclude_products / exclude_identity。
3. 生成 3-4 个买家画像，必须与上述表格结论一致；
4. 构造 20-40 个具体、可直接使用的英文搜索词；
5. {ver_desc}；
6. 在 pourquoi 字段（why/reason）中必须引用表格里的具体数字（如"画像精准度 0.80"）作为依据；
7. 严格按此 JSON schema 输出（只输出 JSON）：
{schema}"""
    raw = call_ai(KW_SYSTEM, user, max_tokens=8000)
    data = _extract_json(raw)
    data.setdefault("company_name", cust["name"])
    os.makedirs(OUT_DIR, exist_ok=True)
    safe = cust["name"].replace("/", "").replace("\\", "")
    if version == "2.5":
        inp = os.path.join(OUT_DIR, f"{safe}_kw25_input.json")
        json.dump(data, open(inp, "w", encoding="utf-8"), ensure_ascii=False)
        out = os.path.join(OUT_DIR, f"{safe}配词方案_2.5.xlsx")
        r = subprocess.run(["python3", os.path.join(KW_SCRIPTS, "keywords_xlsx_generator.py"), inp, out],
                           capture_output=True, text=True, timeout=120)
        final = out if os.path.exists(out) else None
        if final:
            _cache[ck] = {"file": final, "ts": time.strftime("%Y-%m-%d %H:%M")}
            _save_cache()
        return final, data, r.stdout + r.stderr
    else:
        inp = os.path.join(OUT_DIR, f"{safe}_kw30_input.json")
        json.dump(data, open(inp, "w", encoding="utf-8"), ensure_ascii=False)
        out = os.path.join(OUT_DIR, f"{safe}配词方案_3.0.txt")
        r = subprocess.run(["python3", os.path.join(KW_SCRIPTS, "keywords_txt_generator.py"), inp, out, "--external"],
                           capture_output=True, text=True, timeout=120)
        final = out if os.path.exists(out) else None
        if final:
            _cache[ck] = {"file": final, "ts": time.strftime("%Y-%m-%d %H:%M")}
            _save_cache()
        return final, data, r.stdout + r.stderr

# ===== 邮件提示词规范（从《外贸开发信提示词生成器》skill 抽取，见 sync_email_spec.py）=====
EMAIL_SPEC_FILE = os.path.expanduser(
    os.environ.get("EMAIL_PROMPT_SPEC", os.path.join(BASE, "邮件提示词规范.md")))
_EMAIL_SPEC = {}

ROUNDS = int(os.environ.get("EMAIL_ROUNDS", "3"))   # 邮件轮数（默认 3 轮）
# 每轮名称 + 任务（skill 默认 3 轮：破冰→价值深化→信任背书；这里沿用系统现行的破冰/价值深化/软退出，
#  要改成 skill 默认把第三个元组换成 ("信任背书", ...) 即可）
ROUND_PLAN = [
    ("破冰", "让对方知道你是谁、为什么联系他：具体观察开场 + 一句话价值 + 低门槛 CTA"),
    ("价值深化", "围绕对方业务场景讲一个具体价值点：换角度深挖一个卖点，不与上一轮重复"),
    ("软退出", "低门槛行动号召收尾：给一个明确交付物，留退路，不做纠缠"),
][:ROUNDS]

EMAIL_ROLE = """你是外贸 B2B 开发信提示词生成专家，为一个外贸获客平台产出「开发信提示词」。
你只产出「提示词」（给大模型的写作指令），不直接写邮件正文。
必须严格按下面《邮件提示词规范》执行；规范没写到的地方，以"含蓄、专业、具体、反套话"为准。
只输出提示词正文，不要任何解释、前言、结语。"""

# 规范文件缺失时的兜底（正常情况不会用到）
EMAIL_SPEC_FALLBACK = """1. 每轮输出「一整段连续纯文本」提示词，不是邮件正文。
2. 卖点精选 2-4 个，按客户画像与本轮任务筛选，不堆砌。
3. 单一线索锁定：主题行、开场、正文、CTA 必须指同一条产品线索，不得中途换品类。
4. 主题行 sentence case，通用产品名小写；CTA 用"单句问句 + 具体交付物 + 留退路"。
5. 每封必带合规退出句，动作词小写。
6. 禁用套话：Hi there / I hope this email finds you well / We are a professional manufacturer 等。
7. 禁止 markdown、表格、列表符号、方括号占位符；语言地道，反中式英语。
8. 采购信息（MOQ/交期/认证/样品）知识库有具体值才写，没有就不写。"""


def email_spec():
    if "v" not in _EMAIL_SPEC:
        try:
            _EMAIL_SPEC["v"] = open(EMAIL_SPEC_FILE, encoding="utf-8").read()
        except Exception as e:
            print(f"[warn] 读取邮件提示词规范失败（{e}），退回内置精简版", file=sys.stderr)
            _EMAIL_SPEC["v"] = EMAIL_SPEC_FALLBACK
    return _EMAIL_SPEC["v"]


def email_system_prompt():
    return EMAIL_ROLE + "\n\n================《邮件提示词规范》================\n" + email_spec()

def gen_email(cust, use_cache=True):
    """生成 N 轮「邮件画板」——按 skill 铁律：每轮一个独立文件，禁止合并。
    返回 (文件列表, 合并原文, 日志)"""
    _load_cache()
    ck = cache_key(cust, "email")
    if use_cache and ck in _cache:
        rec = _cache[ck]
        fs = rec.get("files") or ([rec["file"]] if rec.get("file") else [])
        if fs and all(os.path.exists(f) for f in fs):
            print(f"[cache] 命中 {cust['name']} email", file=sys.stderr)
            return fs, "", "缓存命中"
    tags = "、".join(cust.get("diagTags", []))
    diag_txt = ""
    try:
        import kw_context
        diag_txt, _ = kw_context.build_context(cust["name"])
    except Exception as _e:
        print(f"[warn] 诊断表格拉取失败，本次退化为仅用诊断标签：{_e}", file=sys.stderr)

    os.makedirs(OUT_DIR, exist_ok=True)
    safe = cust["name"].replace("/", "").replace(chr(92), "")
    date = time.strftime("%Y%m%d")
    cn_num = "一二三四五"
    files, raws = [], []
    for idx, (round_name, task_desc) in enumerate(ROUND_PLAN, 1):
        user = f"""为「{cust['name']}」生成【第{idx}轮 · {round_name}】的邮件画板（对外交付版）。

【本轮任务】{task_desc}

【客户背景】
- 主体：外贸出口企业；配词系统版本 {cust.get('ver','')}
- 诊断问题：{cust.get('diag','')}（{tags}）
- 已知数据：邮件触达 {cust.get('reachedEmail',0)} 封，打开率 {cust.get('openRatio','—')}，真实询盘 {cust.get('nonWaInq',0)}，账户余额 {cust.get('cnyLeft','—')} 元
- 问题定位：{cust.get('advice','')}
- 主营产品/业务：{cust.get('productCatalog') or cust.get('bizType','') or '—'}
- 目标国家/地区：{cust.get('targetCountries','') or '未指定（按 country 变量做地区路由）'}
- 版本路由：{'3.0 → 可按客户画像拆分营销逻辑' if cust.get('ver','').startswith('3') else '2.5 → 必须兼容所有画像，不绑定单一画像，按 {company_type} 路由'}

════════ 听言诊断表格（★权威依据：卖点、切入点、身份判断必须以此为准）════════
{diag_txt or '（本次未能拉取到诊断表格）'}

【输出要求】
1. 按《邮件提示词规范》里的「邮件画板（精简版 v1.1）」8 节结构产出**一份**画板，顺序固定：
   一、任务与角色；二、画像路由；三、六个段落；四、写作规则；五、禁用词与红线；六、事实边界；七、本封上下文关系；八、生成前自检 + 生成后后评估。
2. 首行写：`邮件画板（精简版 v1.1）｜{cust['name']}｜第{idx}轮·{round_name}`，第二行写「共用版：一份覆盖多画像；走 3.0 按画像拆分时直接取对应分支」，第三行写「（我方事实由系统知识库自动载入，本文件不复述知识库；本文件只写写作指令与边界）」，各节之间用 `====================` 分隔。
3. 节名用【一、任务与角色】这种方括号形式；节内可以用短横线分点，但**禁止 markdown 表格、代码块、星号强调、井号标题**。
4. 所有 ｛｝ 里的内容都要按本客户实际情况填成具体值（不要留空、不要照抄模板占位），无法确证的项就写"不写/留后轮"，不要编造。
5. 变量原样保留：{{sellerCompanyName}}、{{Target_Name}}、{{companyname}}、{{contactname}}、{{company_type}}、{{product_category}} 等，用规范里的标准变量名，不要自造。
6. 正文语言：指令用中文写，要求大模型生成的邮件用英文。
7. 只输出这份画板正文，不要前言、不要结语、不要说"以下是"。
"""
        raw = call_ai(email_system_prompt(), user, max_tokens=9000)
        out = os.path.join(OUT_DIR, f"{safe}-第{cn_num[idx-1]}轮-{round_name}-邮件提示词_v1.1_{date}.txt")
        open(out, "w", encoding="utf-8").write(raw)
        files.append(out)
        raws.append(raw)

    _cache[ck] = {"files": files, "ts": time.strftime("%Y-%m-%d %H:%M")}
    _save_cache()
    return files, "\n\n".join(raws), ""


if __name__ == "__main__":
    # 命令行测试: python3 gen_fix.py <客户名> <kw25|kw30|email>
    name = sys.argv[1]; kind = sys.argv[2] if len(sys.argv) > 2 else "kw30"
    # 从看板数据读取该客户
    s = open(os.path.join(BASE, "assets", "data.js"), encoding="utf-8").read()
    custs = json.loads(s[s.find("=") + 1:].rstrip(";"))["customers"]
    c = next((x for x in custs if x["name"] == name), None)
    if not c:
        print("未找到客户:", name); sys.exit(1)
    if kind == "email":
        out, data, log = gen_email(c, use_cache=False)
    else:
        out, data, log = gen_keywords(c, "2.5" if kind == "kw25" else "3.0")
    if isinstance(out, list):
        print("输出文件:")
        for _f in out:
            print("  -", _f)
    else:
        print("输出文件:", out)
    print("生成器日志:", log[:300])
    print("方案预览:", (data or "")[:400])
