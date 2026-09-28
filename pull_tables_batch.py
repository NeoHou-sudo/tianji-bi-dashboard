#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pull_tables_batch.py —— 批量拉取一批客户的「听言全部诊断表格」，并输出配词依据摘要。

用途：把「步骤 0：先拉全表格」这一步标准化，避免再次出现只读搜索词 CSV 就配词的情况。

用法：
  python3 pull_tables_batch.py 客户A 客户B 客户C
  python3 pull_tables_batch.py --manager 代佳文          # 拉该客户成功经理名下全部客户
  python3 pull_tables_batch.py --file 名单.txt           # 每行一个客户名
可选：
  --force   忽略缓存，强制重新拉取

产出：
  1. 依据摘要汇总：生成结果/_批量配词依据_{时间戳}.txt
  2. 结构化摘要：生成结果/_批量诊断摘要_{时间戳}.json
  3. 每家的多 sheet xlsx：~/Desktop/外贸客户资料/{客户名}/源文件/（由 tingyan_pull 写入）
"""
import argparse, json, os, sys, time

B = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, B)
OUT = os.path.join(B, "生成结果")
import kw_context  # noqa: E402


def names_from_manager(mgr):
    """从看板数据里取某经理名下客户名"""
    s = open(os.path.join(B, "assets", "data.js"), encoding="utf-8").read()
    custs = json.loads(s[s.find("=") + 1:].rstrip(";"))["customers"]
    return [c["name"] for c in custs if c.get("mgr") == mgr]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*", help="客户名（可多个）")
    ap.add_argument("--manager", help="按客户成功经理拉取")
    ap.add_argument("--file", help="客户名单文件，每行一个")
    ap.add_argument("--force", action="store_true", help="忽略缓存强制重拉")
    a = ap.parse_args()

    names = list(a.names)
    if a.manager:
        names += names_from_manager(a.manager)
    if a.file:
        names += [l.strip() for l in open(a.file, encoding="utf-8") if l.strip()]
    names = list(dict.fromkeys(names))
    if not names:
        print(__doc__); sys.exit(1)

    os.makedirs(OUT, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M")
    all_raw, all_digest, failed = {}, [], []
    for i, n in enumerate(names, 1):
        try:
            d, r = kw_context.build_context(n, force=a.force)
            all_digest.append(d)
            all_raw[n] = {"version": r["version"], "sheets": r["sheets"],
                          "portraits": r["portraits"], "products": r["products"],
                          "identities": r["identities"], "summary": r["summary"],
                          "search_terms": r.get("search_terms", []),
                          "positive": r.get("positive", []), "negative": r.get("negative", [])}
            print(f"[{i}/{len(names)}] ✅ {n}（{r['version']}｜{len(r['sheets'])} 张表｜"
                  f"画像 {len(r['portraits'])} 个）")
        except Exception as e:
            failed.append((n, str(e)))
            print(f"[{i}/{len(names)}] ❌ {n}：{e}")

    dp = os.path.join(OUT, f"_批量配词依据_{ts}.txt")
    open(dp, "w", encoding="utf-8").write("\n\n".join(all_digest))
    jp = os.path.join(OUT, f"_批量诊断摘要_{ts}.json")
    json.dump(all_raw, open(jp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print(f"\n成功 {len(all_digest)} 家，失败 {len(failed)} 家")
    print("依据汇总:", dp)
    print("结构化摘要:", jp)
    for n, e in failed:
        print(f"  ❌ {n}：{e}")


if __name__ == "__main__":
    main()
