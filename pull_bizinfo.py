#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
抓取听言「用户基础信息」→ business_info缓存.json
------------------------------------------------------------------
数据来源：GET /api/user/{id}/business_info   （只读、快、不消耗对方 AI 额度）
⚠️ 旧版走的是 POST /api/diagnosis —— 那是「AI 诊断生成」接口，等于为了拿基础信息
   让听言逐客户跑一遍 AI，又慢又浪费。此版本已弃用该路径。

用法：
  python3 pull_bizinfo.py            # 只补/刷新「数据不全」的客户
  python3 pull_bizinfo.py --force    # 全部重新拉
环境变量：TY_WORKERS（并发，默认 8）、TY_CREDS（凭证路径）
"""
import json, os, subprocess, threading, sys
from concurrent.futures import ThreadPoolExecutor

BASE = os.path.dirname(os.path.abspath(__file__))
CREDS = os.path.expanduser(os.environ.get("TY_CREDS", "~/.baixyn-api/credentials.json"))
WORKERS = int(os.environ.get("TY_WORKERS", "8"))
FORCE = "--force" in sys.argv

d = json.load(open(CREDS, encoding="utf-8"))
ca = d["collocation_alert"]
tok = ca["diag_token"]
URL = ca["base_url"].rstrip("/")

s = open(os.path.join(BASE, "assets", "data.js"), encoding="utf-8").read()
cs = json.loads(s[s.find("=") + 1:].rstrip().rstrip(";"))["customers"]
out_file = os.path.join(BASE, "business_info缓存.json")
cache = json.load(open(out_file, encoding="utf-8")) if os.path.exists(out_file) else {}

OLD_KEYS = {"产品目录", "细分行业", "应用场景", "官网"}     # 旧版的 4 个字段

def stale(v):
    return (not v) or (set(v.keys()) <= OLD_KEYS)          # 空 或 仍是旧结构

miss = [c for c in cs if c.get("id") and (FORCE or stale(cache.get(c["name"])))]
print(f"待拉 {len(miss)} 家（共 {len(cs)} 家，并发 {WORKERS}）", flush=True)

lock = threading.Lock()
cnt = [0]

def work(c):
    uid = int(c["id"])
    cmd = ["curl", "-s", "--max-time", "30",
           "-H", "Cookie: diag_token=" + tok,
           f"{URL}/api/user/{uid}/business_info"]
    raw = subprocess.run(cmd, capture_output=True, text=True, timeout=40).stdout
    try:
        bi = json.loads(raw)
    except Exception:
        bi = {}
    with lock:
        cache[c["name"]] = bi if isinstance(bi, dict) else {}
        cnt[0] += 1
        if cnt[0] % 25 == 0 or cnt[0] == len(miss):
            json.dump(cache, open(out_file, "w", encoding="utf-8"), ensure_ascii=False)
        print(f"[{cnt[0]}/{len(miss)}] {c['name'][:20]}", flush=True)

if miss:
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        list(ex.map(work, miss))
json.dump(cache, open(out_file, "w", encoding="utf-8"), ensure_ascii=False)
print("完成", flush=True)
