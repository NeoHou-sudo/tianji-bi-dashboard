#!/bin/bash
# =====================================================================
# 天玑 · 取某一天的数据快照
# 用法：./fetch_snapshot.sh 20260928 [目标目录]
#   默认解压到 ~/Desktop/外贸客户资料/快照/<日期>/
#   找不到就列出最近的可用快照日期
# =====================================================================
set -e
ARCH="${SNAP_ARCHIVE:-$HOME/.baixyn-api/tianji-snapshots}"
D="${1:?用法：./fetch_snapshot.sh YYYYMMDD [目标目录]}"
OUT="${2:-$HOME/Desktop/外贸客户资料/快照/$D}"

if [ ! -d "$ARCH" ]; then
  echo "❌ 找不到归档仓：$ARCH"; exit 1
fi
cd "$ARCH"

# 先把远端 tags 拉下来（本地可能没同步）
if git remote get-url origin >/dev/null 2>&1; then
  git fetch --tags origin >/dev/null 2>&1 || true
fi

TAG="snap-$D"
if ! git rev-parse "$TAG" >/dev/null 2>&1; then
  echo "❌ 没有 $D 的快照（tag $TAG 不存在）。最近可用的："
  git tag | grep '^snap-' | sort | tail -10
  exit 1
fi

mkdir -p "$OUT"
git archive --format=tar "$TAG" | tar -x -C "$OUT"
echo "✅ 已拉取 $D 快照 → $OUT"
echo "    内容：$(ls "$OUT/snapshots" 2>/dev/null | tr '\n' ' ')"
