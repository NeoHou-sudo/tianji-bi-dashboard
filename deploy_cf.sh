#!/bin/bash
# =====================================================================
# 天玑 · 售后看板 —— 一键更新并推送到 Cloudflare
#
#   ./deploy_cf.sh              # 重新生成云端前端 + 部署 Worker + 推送数据
#   ./deploy_cf.sh --data-only  # 只把最新数据推到 KV（日常更新用这个，几秒钟）
#   ./deploy_cf.sh --build-only # 只重新生成 cf-worker/public/，不推送
#
# 凭据读自 ~/.baixyn-api/cloudflare.env（CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID）
# =====================================================================
set -e
cd "$(dirname "$0")"
ROOT="$(pwd)"
CF_DIR="$ROOT/cf-worker"
DATA="$ROOT/assets/data.js"
ENV_FILE="$HOME/.baixyn-api/cloudflare.env"

# ⚠️ 必须用真正的 node：Loomy 自带的 node 是 Electron 壳，
#    会把 wrangler 的参数搞乱（报 "Unknown argument: .../cli.js"）
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

if [ ! -f "$ENV_FILE" ]; then
  echo "❌ 找不到 $ENV_FILE"
  echo "   请先在里面写两行："
  echo "     export CLOUDFLARE_API_TOKEN=\"...\""
  echo "     export CLOUDFLARE_ACCOUNT_ID=\"...\""
  exit 1
fi
set -a; . "$ENV_FILE"; set +a

MODE="all"
case "$1" in
  --build-only) MODE="build" ;;
  --data-only)  MODE="data" ;;
esac

# ---------- 1. 重新生成云端前端 ----------
if [ "$MODE" = "data" ]; then
  echo "==> 1/3 跳过前端生成（--data-only）"
else
  echo "==> 1/3 重新生成云端前端 cf-worker/public/"
  python3 build_cf_pages.py
  if [ "$MODE" = "build" ]; then
    echo "==> 只构建，未推送。产物：$CF_DIR/public"
    exit 0
  fi
fi

# ---------- 2. 部署 Worker ----------
if [ "$MODE" = "all" ]; then
  echo "==> 2/3 部署 Worker（wrangler deploy）"
  ( cd "$CF_DIR" && npx --yes wrangler@latest deploy )
else
  echo "==> 2/3 跳过部署（只推数据）"
fi

# ---------- 3. 推送数据到 KV（直接调 API，不依赖 wrangler）----------
NS=$(grep -E '^id *=' "$CF_DIR/wrangler.toml" | head -1 | sed -E 's/.*"([^"]+)".*/\1/')
if [ -z "$NS" ] || [ "$NS" = "在这里填" ]; then
  echo "❌ cf-worker/wrangler.toml 里没有有效的 KV 命名空间 id"
  exit 1
fi
echo "==> 3/3 推送数据到 KV（namespace $NS）"
RESP=$(curl -s -X PUT \
  -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  "https://api.cloudflare.com/client/v4/accounts/$CLOUDFLARE_ACCOUNT_ID/storage/kv/namespaces/$NS/values/data.js" \
  --data-binary "@$DATA")
echo "$RESP" | grep -q '"success":true' && echo "✅ 数据已推送（$(du -h "$DATA" | cut -f1)）" || { echo "❌ 推送失败：$RESP"; exit 1; }

echo "==> 完成。看板：https://tianji-bi.houziyu.workers.dev"
