#!/bin/bash
# =====================================================================
# 版本发布：测试 → 提交 → 打 tag → 推送 GitHub → 部署生产
#
# 用法：
#   ./release.sh "修复发送验证码按钮交互与缓存"     # 全流程（含部署生产）
#   ./release.sh --no-deploy "文案修改"             # 只提交打tag，不部署
#   ./release.sh --version v1.2.0 "新增功能"        # 指定版本号
#
# 规则：test_smoke.sh 不通过 → 拒绝提交与发布。
# =====================================================================
set -e
cd "$(dirname "$0")"

MODE="deploy"; VER=""; MSG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --no-deploy) MODE="nodeploy" ;;
    --version)   VER="$2"; shift ;;
    *)           MSG="${MSG:-}$1 " ;;
  esac
  shift
done
MSG="$(echo "${MSG:-更新}" | sed 's/ *$//')"

echo "==> 1/5 上线前自检（不通过即终止）"
./test_smoke.sh

echo "==> 2/5 生成/构建产物"
python3 build_cf_pages.py >/dev/null 2>&1 || true

echo "==> 3/5 提交 + 打 tag"
if [ -z "$VER" ]; then
  LAST=$(git describe --tags --abbrev=0 2>/dev/null || echo v0.9.9)
  VER=$(echo "$LAST" | awk -F. '{print "v" $1+0 "." $2+0 "." $3+1}')
fi
git add -A
git commit -m "$MSG" || echo "（无变更可提交）"
git tag "$VER" 2>/dev/null || echo "（tag $VER 已存在，跳过）"
echo "版本号：$VER"

echo "==> 4/5 推送 GitHub"
if git remote get-url origin >/dev/null 2>&1; then
  git push origin master 2>/dev/null || git push origin main 2>/dev/null || git push
  git push origin "$VER"
else
  echo "⚠️ 尚未配置 remote origin，本地提交与 tag 已就绪；配置后执行：git push origin --tags"
fi

if [ "$MODE" = "deploy" ]; then
  echo "==> 5/5 部署生产（Cloudflare）"
  ./deploy_cf.sh
  echo "✅ 已发布 v${VER#v} 到生产：https://tianji-bi.houziyu.workers.dev"
else
  echo "==> 5/5 跳过生产部署（--no-deploy）"
fi
