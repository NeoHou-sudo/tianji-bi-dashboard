/* 天玑 · 售后看板（云端版）配置
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
