#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把《外贸开发信提示词生成器》skill 的正文同步成后端的邮件提示词规范文件。

- 来源：~/.config/loomy-opencode/skills/b2b-cold-email-prompt-generator/SKILL.md
- 产物：本目录下的 邮件提示词规范.md（gen_fix.py 生成「3 轮邮件提示词」时作为系统提示词）
- 抽取范围：〇（邮件系统生成逻辑）+ 一 ~ 六（版本感知 / 递进式 / 卖点精选 / 信任锚与三门禁 /
  整封模板 / 单一线索锁定 / 反套话 / 主题行 / 大小写 / CTA / 退出文案 / 地区路由 / 语言地道度 / 禁用词与合规）
- 有意剔除：交付命名铁律、铁律 A~D、输入面最小化、执行清单、版本记录、失败案例库、生成后后评估
  （这些是"人 + 大模型对话"时的流程规则，与后端逐客户批量生成无关，且会显著拉长提示词）

用法：python3 sync_email_spec.py
"""
import os
import re
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.expanduser(
    os.environ.get("EMAIL_SKILL_FILE",
                   "~/.config/loomy-opencode/skills/b2b-cold-email-prompt-generator/SKILL.md"))
OUT = os.path.join(BASE, "邮件提示词规范.md")

# 抽取的起止标题（含）
START_HEAD = "## 〇、邮件系统生成逻辑"
END_HEAD = "## 六·五 生成后后评估"


def main():
    if not os.path.exists(SKILL):
        print("找不到 skill 文件：" + SKILL, file=sys.stderr)
        sys.exit(1)
    lines = open(SKILL, encoding="utf-8").read().split("\n")

    def find(prefix, default=None):
        for i, l in enumerate(lines):
            if l.startswith(prefix):
                return i
        return default

    i0 = find(START_HEAD)
    i1 = find("## 一、")
    i2 = find(END_HEAD)
    if i0 is None or i1 is None or i2 is None:
        print("定位章节失败，请检查 skill 结构（标题是否变化）", file=sys.stderr)
        sys.exit(1)

    ver = ""
    m = re.search(r"version:\s*([0-9.]+)", "\n".join(lines[:20]))
    if m:
        ver = m.group(1)

    preamble = "\n".join(lines[i0:i1]).strip()
    core = "\n".join(lines[i1:i2]).strip()

    head = f"""# 邮件提示词规范（后端生成用 · 抽取自《外贸开发信提示词生成器》v{ver}）

> 本文件是 `gen_fix.py` 生成「3 轮邮件提示词」时使用的系统提示词。
> 内容自动抽取自 skill：`{SKILL.replace(os.path.expanduser('~'), '~')}`
> 抽取范围：〇（邮件系统生成逻辑）+ 一 ~ 六（版本感知 / 递进式 / 卖点精选 / 信任锚与三门禁 /
> 整封模板 / 单一线索锁定 / 反套话 / 主题行 / 大小写 / CTA / 退出文案 / 地区路由 / 语言地道度 / 禁用词与合规）。
> 有意剔除的是与"后端逐客户批量生成"无关的元规则（交付命名铁律、铁律 A~D、输入面最小化、
> 执行清单、版本记录、失败案例库、生成后后评估）。
> ⚠️ skill 升级后重跑 `python3 sync_email_spec.py` 即可同步本文件。

---

"""

    open(OUT, "w", encoding="utf-8").write(head + preamble + "\n\n---\n\n" + core + "\n")
    print(f"已同步 → {OUT}（skill v{ver}，{len(head) + len(preamble) + len(core)} 字符）")


if __name__ == "__main__":
    main()
