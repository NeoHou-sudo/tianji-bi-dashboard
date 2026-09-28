#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用诊断接口补全缺失的品类（后台运行）"""
import json, os, subprocess, time

BASE = os.path.dirname(os.path.abspath(__file__))
d = json.load(open(os.path.expanduser('~/.baixyn-api/credentials.json')))
ca = d['collocation_alert']; tok = ca['diag_token']; URL = ca['base_url']
cmap = json.load(open(os.path.join(BASE, '品类中文映射.json'), encoding='utf-8'))

s = open(os.path.join(BASE, 'assets', 'data.js'), encoding='utf-8').read()
cs = json.loads(s[s.find('=')+1:].rstrip(';'))['customers']
done_file = os.path.join(BASE, '品类补充映射.json')
added = json.load(open(done_file, encoding='utf-8')) if os.path.exists(done_file) else {}
miss = [c for c in cs if not c.get('catCn') and c['name'] not in added]
print(f"待补 {len(miss)} 家", flush=True)


def get_biz(uid):
    cmd = ["curl", "-s", "--max-time", "120", "-X", "POST", "-H", f"Cookie: diag_token={tok}",
           "-H", "Content-Type: application/json", "-d", json.dumps({"user_id": str(uid)}), URL + "/api/diagnosis"]
    raw = subprocess.run(cmd, capture_output=True, text=True, timeout=130).stdout
    for ln in raw.split("\n"):
        if not ln.startswith("data: "):
            continue
        b = ln[6:]
        if b == "[DONE]":
            continue
        try:
            j = json.loads(b)
        except Exception:
            continue
        if j.get("type") == "snapshot":
            return (j.get("snapshot") or {}).get("business_info") or {}
    return {}


from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
_lock = threading.Lock()
done_cnt = [0]

def work(c):
    uid = c.get('id')
    if not uid: return
    bi = get_biz(int(uid))
    first = ""
    for field in ["卖家产品目录", "细分行业"]:
        raw = bi.get(field) or ""
        try:
            arr = json.loads(raw) if isinstance(raw, str) else raw
            cand = ""
            if arr and arr[0].get("children"):
                cand = arr[0]["children"][0].get("name", "")
            elif arr:
                cand = arr[0].get("name", "")
            if cand and cand in cmap:
                first = cand; break
            if cand and not first:
                first = cand
        except Exception:
            pass
    with _lock:
        if first and first in cmap:
            added[c['name']] = cmap[first]
        done_cnt[0] += 1
        json.dump(added, open(done_file, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print(f"  [{done_cnt[0]}/{len(miss)}] {c['name'][:20]} → {added.get(c['name'],'(未识别)')}", flush=True)

with ThreadPoolExecutor(max_workers=6) as ex:
    list(ex.map(work, miss))

print(f"\n完成：补到 {len(added)} 家", flush=True)
