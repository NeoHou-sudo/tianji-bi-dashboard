#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
夜间重建总调度
======================================================================
依次执行：
  1) 生成「公司简述 / 产品线简述」（增量，已缓存则跳过）
  2) 统一「客户品类」归类（增量，已缓存则跳过）
  3) 重算 assets/data.js（把上面两项写进看板）
  4) 打快照

设计：每一步互相独立；都已做「指纹缓存」，重复运行只处理新增/变更客户，
      所以以后每天跑只会花几分钟（存量客户不再重复生成）。
用法：python3 rebuild_all.py
日志：日志/夜间重建.log
"""
import os, sys, subprocess, datetime

BASE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(BASE, "日志", "夜间重建.log")


def log(m):
    line = "[%s] %s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), m)
    print(line, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        open(LOG, "a", encoding="utf-8").write(line + "\n")
    except Exception:
        pass


def run(tag, args):
    log("▶ 开始：" + tag)
    r = subprocess.run([sys.executable] + args, cwd=BASE, capture_output=True, text=True)
    tail = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()[-2:]
    log("■ 结束：%s  rc=%s  | %s" % (tag, r.returncode, " / ".join(tail)))
    return r.returncode == 0


if __name__ == "__main__":
    log("=" * 56)
    log("夜间重建开始")
    run("公司简述 / 产品线简述", ["gen_summary.py", "--batch=8"])
    run("统一客户品类归类", ["gen_category.py", "--batch=20"])
    run("重算看板数据 data.js", ["generate_dashboard.py"])
    run("打快照", ["snapshot.py", "take"])
    log("夜间重建结束")
