#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
手工配词（高质量通道）：把人工写好的方案 JSON 直接套用系统排版脚本，跳过 AI。
用法：python3 make_kw.py <客户名> <2.5|3.0> <方案JSON路径>
"""
import json, os, subprocess, sys
BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "生成结果")
KW = os.path.expanduser("~/.config/loomy-opencode/skills/company-private-keyword-config/scripts")
if len(sys.argv) < 4:
    print(__doc__); sys.exit(1)
name, ver, src = sys.argv[1], sys.argv[2], sys.argv[3]
data = json.load(open(src, encoding="utf-8"))
data.setdefault("company_name", name)
os.makedirs(OUT, exist_ok=True)
safe = name.replace("/", "").replace("\\", "")
if ver == "2.5":
    inp = os.path.join(OUT, f"{safe}_kw25_input.json"); out = os.path.join(OUT, f"{safe}配词方案_2.5.xlsx")
    cmd = [sys.executable, os.path.join(KW, "keywords_xlsx_generator.py"), inp, out]
else:
    inp = os.path.join(OUT, f"{safe}_kw30_input.json"); out = os.path.join(OUT, f"{safe}配词方案_3.0.txt")
    cmd = [sys.executable, os.path.join(KW, "keywords_txt_generator.py"), inp, out, "--external"]
json.dump(data, open(inp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
print("输出文件:", out if os.path.exists(out) else "❌ 生成失败")
if r.stdout: print((r.stdout or "")[-400:])
if r.stderr: print("stderr:", (r.stderr or "")[-300:])
