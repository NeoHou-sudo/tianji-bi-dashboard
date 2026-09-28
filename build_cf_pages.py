#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把「天玑 · 售后看板」的本机版（依赖 bi_server.py）改造成可上 Cloudflare 的云端只读版。

用法：
    cd /Users/Apple/Desktop/外贸客户资料/客户续费BI看板
    python3 build_cf_pages.py

产物：./cf-worker/public/  （前端页面；配合 cf-worker/src/index.js 一起部署）

云端版相对本机版做了这些改造：
  1. 数据改为向 /api/data.js 取（由 Worker 校验登录后下发，数据存 KV/R2，不随站点公开）
  2. 隐藏依赖本机 Python 服务的按钮：⟳ 快照 / 🗂 归档文件 / 📦 批量生成打包 / 表格「一键修正」列
  3. 拦截这些本机专属接口（/api/status、/api/refresh*、/api/history*、/api/batch、/api/fix*、
     /api/download、/api/file），返回占位结果，避免报错与红色告警
  4. 登录流程保持原样：每个人用自己的天枢账号 + 密码 + 短信验证码登录（由 Worker 转发到天枢）
"""

import os
import shutil
import sys
import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "cf-worker", "public")

MAIN_PAGES = ["index.html", "kanban.html", "tianji.html"]

# 本机专属接口：云端返回占位，避免控制台报错 / 红色告警
FETCH_SHIM = r'''// ===== 云端版：这些接口依赖本机服务，这里直接给占位结果，避免报错 =====
(function(){
  var LOCAL_ONLY = ['/api/status','/api/refresh','/api/history','/api/batch',
                    '/api/fix','/api/fix_only','/api/download','/api/file'];
  var _f = window.fetch.bind(window);
  window.fetch = function(u, o){
    try{
      var s = (typeof u === 'string') ? u : ((u && u.url) || '');
      var path = s.split('?')[0];
      for(var i=0;i<LOCAL_ONLY.length;i++){
        if(path.indexOf(LOCAL_ONLY[i]) >= 0){
          var body = {ok:false, error:'云端只读版未启用该功能，请在本机看板操作', readonly:true};
          if(path.slice(-19) === '/api/refresh/status') body = {ok:true, running:false, finished:false, progress:null, changed:false};
          if(path.slice(-12) === '/api/history')        body = {ok:true, records:[]};
          return Promise.resolve(new Response(JSON.stringify(body),
            {status:200, headers:{'Content-Type':'application/json'}}));
        }
      }
    }catch(e){}
    return _f.apply(window, arguments);
  };
})();

'''

DATA_LOADER = '''<script src="assets/config.js"></script>
<script>
// 数据源：默认向 /api/data.js 取（云端会校验登录，未登录只给空壳）；
// 若 config.js 里配了 dataUrl（例如放 Cloudflare R2），则直接从该地址加载同一份 data.js。
(function(){
  var u = (window.BI_CONFIG && window.BI_CONFIG.dataUrl) || '/api/data.js';
  document.write('<script src="' + u + '"><\\/script>');
})();
</script>'''

CONFIG_JS = '''/* 天玑 · 售后看板（云端版）配置
 *
 * dataUrl 留空  → 走 /api/data.js（推荐）：云端校验登录后下发，数据存 KV/R2，不随站点公开
 * dataUrl 填地址 → 改为从该地址加载 data.js，例如放进 Cloudflare R2 的公开地址：
 *
 *   window.BI_CONFIG = { dataUrl: 'https://data.你的域名.com/kanban/data.js?v=20260928' };
 *
 * ⚠️ 走 R2 公开地址意味着"谁拿到链接谁能看到全部客户名单"，除非那个地址也上了 Access。
 *    所以默认不建议改，保持留空即可。
 */
window.BI_CONFIG = { dataUrl: '' };
'''

MAIN_PATCHES = [
    # 1) 隐藏依赖后端的按钮与列
    (
        '.count-tip{font-size:12px;color:var(--muted)}',
        '.count-tip{font-size:12px;color:var(--muted)}\n'
        '.cf-hide{display:none!important}\n'
        '#btnSnapshot,#btnArchive,#btnBatchGen{display:none!important}',
    ),
    # 2) 数据脚本 → 走 /api/data.js
    ('<script src="assets/data.js"></script>', DATA_LOADER),
    # 3) 拦截本机专属接口
    ('// ===== 登录逻辑 =====', FETCH_SHIM + '// ===== 登录逻辑 ====='),
    # 4) 隐藏表头「全选」checkbox 列（批量打包依赖本机，云端不启用）
    ("<th style=\"width:34px;cursor:default\"><input type=\"checkbox\" id=\"ckAll\" title=\"全选当前筛选\" style=\"cursor:pointer\"></th>",
     "<th class=\"cf-hide\" style=\"width:34px;cursor:default\"></th>"),
    # 5) 行内：隐藏 checkbox 单元格（一键修正列由 isFullAccess() 运行时控制，不在此处隐藏）
    ('return `<tr><td><input type="checkbox" class="rowck" data-name="${c.name.replace(/"/g,\'\')}" style="cursor:pointer"></td>${cells}${isFullAccess() ? \'<td><button class="mini" data-fix="\'+c.id+\'">修正</button></td>\' : \'\'}</tr>`;',
     'return `<tr><td class="cf-hide"></td>${cells}${isFullAccess() ? \'<td><button class="mini" data-fix="\'+c.id+\'" onclick="void(0)">修正</button></td>\' : \'\'}</tr>`;'),
    # 6) 空态 colspan：checkbox 已隐藏（cf-hide），只需覆盖数据列 + 可能的修正列
    ('<tr><td colspan="${COLS.length + 1 + (isFullAccess() ? 1 : 0)}" class="empty">没有符合条件的客户</td></tr>',
     '<tr><td colspan="${COLS.length + 1 + (isFullAccess() ? 1 : 0)}" class="empty">没有符合条件的客户</td></tr>'),
    # 7) 去掉「打开内部口径文档（需登录）」链接
    ('<p><a href="/doc" target="_blank" class="doc-link">📄 打开内部口径文档（需登录）</a></p>', ''),
    # 8) Cloudflare 版：一键修正按钮改为直接下载文本方案（不调 /api/fix_only）
    (
        'function renderLegend(){',
        '// CF override: fix button → downloadFix (no backend call)\n'
        'document.querySelectorAll(\'[data-fix]\').forEach(btn=>{\n'
        '  const nc = D.customers.find(x=>String(x.id)===btn.dataset.fix);\n'
        '  if(nc) btn.onclick = (e)=>{ e.stopPropagation(); downloadFix(nc); };\n'
        '});\n'
        'function renderLegend(){',
    ),
]

DRILL_PATCHES = [
    (
        "    const txt = await (await fetch('assets/data.js?t=' + Date.now(), {cache:'no-store'})).text();\n"
        "    const json = txt.slice(txt.indexOf('=') + 1).replace(/;\\s*$/, '');\n"
        "    const D = JSON.parse(json);",
        "    let D;\n"
        "    if(window.BI_CONFIG && window.BI_CONFIG.dataUrl){\n"
        "      await new Promise((res, rej)=>{ const s = document.createElement('script');\n"
        "        s.src = window.BI_CONFIG.dataUrl; s.onload = res; s.onerror = ()=>rej(new Error('云端数据加载失败'));\n"
        "        document.head.appendChild(s); });\n"
        "      D = window.DASHBOARD_DATA;\n"
        "    } else {\n"
        "      const txt = await (await fetch('/api/data.js?t=' + Date.now(), {cache:'no-store'})).text();\n"
        "      D = JSON.parse(txt.slice(txt.indexOf('=') + 1).replace(/;\\s*$/, ''));\n"
        "    }\n"
        "    if(!D) throw new Error('数据未加载');",
    ),
    (
        '</div>\n\n<script>',
        '</div>\n\n<script src="assets/config.js"></script>\n<script>',
    ),
    (
        '// ============ 独立下钻页：不依赖主页任何脚本，自己取数、自己渲染 ============',
        FETCH_SHIM + '// ============ 独立下钻页：不依赖主页任何脚本，自己取数、自己渲染 ============',
    ),
]


def patch(text, rules, fname):
    for old, new in rules:
        n = text.count(old)
        if n != 1:
            print(f"  [警告] {fname}: 未唯一匹配（{n} 处），跳过：{old[:60]!r}", file=sys.stderr)
            continue
        text = text.replace(old, new)
    return text


def main():
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(os.path.join(OUT, "assets"))

    for f in MAIN_PAGES:
        t = open(os.path.join(BASE, f), encoding="utf-8").read()
        t = patch(t, MAIN_PATCHES, f)
        open(os.path.join(OUT, f), "w", encoding="utf-8").write(t)
        print("生成", os.path.relpath(os.path.join(OUT, f), BASE))

    t = open(os.path.join(BASE, "drilldown.html"), encoding="utf-8").read()
    t = patch(t, DRILL_PATCHES, "drilldown.html")
    open(os.path.join(OUT, "drilldown.html"), "w", encoding="utf-8").write(t)
    print("生成", os.path.relpath(os.path.join(OUT, "drilldown.html"), BASE))

    open(os.path.join(OUT, "assets", "config.js"), "w", encoding="utf-8").write(CONFIG_JS)
    print("生成", os.path.relpath(os.path.join(OUT, "assets", "config.js"), BASE))

    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    open(os.path.join(OUT, "BUILD.txt"), "w", encoding="utf-8").write(
        f"天玑 · 售后看板 云端版\n构建时间：{stamp}\n数据来源：/api/data.js（KV/R2）\n"
    )
    print("完成 →", OUT)
    print("提示：客户数据不在 public/ 里，部署后记得执行 ./deploy_cf.sh 把 assets/data.js 推到 KV。")


if __name__ == "__main__":
    main()
