#!/bin/bash
# =====================================================================
# 天玑 · 每日数据快照 → GitHub 归档
# 用法：./archive_snapshot.sh
#  1) 打当日快照（snapshot.py take）
#  2) 把快照文件同步进私有归档仓（~/.baixyn-api/tianji-snapshots）
#  3) git 提交 + 打 tag snap-YYYYMMDD + 推送 GitHub
# =====================================================================
set -e
PROJ="$(cd "$(dirname "$0")" && pwd)"
ARCH="${SNAP_ARCHIVE:-$HOME/.baixyn-api/tianji-snapshots}"
DATE="$(date +%Y%m%d)"

echo "==> 1/3 打当日快照"
cd "$PROJ"
python3 snapshot.py take
if [ ! -f snapshots/metrics.json ]; then
  echo "⚠️ metrics.json 未生成，继续（不影响主快照）"
fi

echo "==> 2/3 同步到归档仓"
mkdir -p "$ARCH/snapshots"
cp -f snapshots/data_*.js.gz snapshots/changes_*.csv "$ARCH/snapshots/" 2>/dev/null || true
cp -f snapshots/metrics.json "$ARCH/" 2>/dev/null || true

echo "==> 3/3 提交 + 打 tag + 推送"
cd "$ARCH"
git add -A
if [ -n "$(git status --porcelain)" ]; then
  git commit -q -m "snapshot $DATE"
  echo "已提交"
else
  echo "（本日无新增快照文件，跳过提交）"
fi
TAG="snap-$DATE"
git tag "$TAG" 2>/dev/null || echo "（tag $TAG 已存在，跳过）"

if git remote get-url origin >/dev/null 2>&1; then
  git push origin main 2>/dev/null
  git push origin "$TAG" 2>/dev/null
  echo "✅ 已推送到 GitHub：$TAG"
else
  echo "⚠️ 归档仓尚未配置 GitHub remote，已本地提交（$TAG）。"
  echo "   配置方式：cd $ARCH && git remote add origin git@github.com:NeoHou-sudo/tianji-bi-snapshots.git && git push -u origin main --tags"
fi
