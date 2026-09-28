#!/bin/bash
# =====================================================================
# 上线前自检（每个版本发布前必须通过）
# 用法：./test_smoke.sh
# =====================================================================
set -e
cd "$(dirname "$0")"
PASS=0; FAIL=0

say() { printf "  %-42s" "$1"; }
ok()  { echo "✅"; PASS=$((PASS+1)); }
bad() { echo "❌ $2"; FAIL=$((FAIL+1)); }

say "Python 语法检查（*.py）"
if python3 -m py_compile bi_server.py gen_fix.py generate_dashboard.py build_cf_pages.py sync_email_spec.py update_dashboard.py 2>/tmp/smoke_py.txt; then ok; else bad "" "$(tail -2 /tmp/smoke_py.txt)"; fi

say "Worker JS 语法检查（node --check）"
if node --check cf-worker/src/index.js 2>/tmp/smoke_js.txt; then ok; else bad "" "$(tail -2 /tmp/smoke_js.txt)"; fi

say "build_cf_pages.py 可生成云端前端"
if python3 build_cf_pages.py >/tmp/smoke_build.txt 2>&1 && [ -f cf-worker/public/index.html ]; then ok; else bad "" "$(tail -2 /tmp/smoke_build.txt)"; fi

say "生产站点可达（/ 返回 200）"
CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 20 "https://tianji-bi.houziyu.workers.dev/" || echo 000)
if [ "$CODE" = "200" ]; then ok; else bad "" "HTTP $CODE"; fi

say "未登录数据不下发（/api/data.js 为空壳）"
if curl -s --max-time 20 "https://tianji-bi.houziyu.workers.dev/api/data.js" | grep -q '"locked":true'; then ok; else bad "" "数据闸门异常"; fi

say "中继通道健康（/api/relay/poll 返回 ok）"
if curl -s --max-time 20 "https://tianji-bi.houziyu.workers.dev/api/relay/poll?token=__skip__" | grep -q '"ok"'; then ok; else bad "" "中继端点无响应"; fi

echo
echo "结果：通过 ${PASS} 项 / 失败 ${FAIL} 项"
if [ "$FAIL" -gt 0 ]; then echo "❌ 有失败项，禁止发布。"; exit 1; fi
echo "✅ 全部通过，可以发布。"
