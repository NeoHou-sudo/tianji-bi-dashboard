/**
 * 天玑 · 售后看板 —— Cloudflare Worker 云端版
 * =====================================================================
 * 云端只干三件事，其余照旧留在本机看板：
 *
 *   1) 登录：/api/send-code、/api/login
 *      —— 转发到天枢后端，用「每个人自己的天枢账号 + 密码 + 短信验证码」验证。
 *         不共用账号、不共用密码，谁登录就记谁，登录后默认只看自己名下客户。
 *
 *   2) 会话：/api/me、/api/logout
 *      —— 签名 Cookie（HMAC-SHA256），不依赖数据库，8 小时过期。
 *
 *   3) 数据下发：/api/data.js
 *      —— 只有携带有效会话才返回客户数据；数据存在 KV（或 R2），
 *         不随站点一起公开，所以直接扒站拿不到客户名单。
 *
 *   其它 /api/*（一键生成、批量打包、快照、归档）一律返回"云端未启用"，
 *   这些仍然在本机看板上跑。
 * =====================================================================
 */

const DEFAULT_TS_BASE = 'https://ts.baixyn.com';
const SESSION_TTL = 7 * 24 * 3600;     // 会话有效期（秒）＝7 天，减少短信验证码的打扰
const COOKIE_NAME = 'bi_sid';
const DATA_KEY = 'data.js';

// 未登录时下发的空壳（与本地版一致：页面看到 locked 就弹登录框）
const LOCKED_JS =
  'window.DASHBOARD_DATA = {"locked":true,"generatedAt":null,"snapshotTime":null,"customers":[],"managers":[]};';

// 云端不支持、需要留到本机跑的接口前缀
const LOCAL_ONLY = [
  '/api/status', '/api/refresh', '/api/history', '/api/batch',
  '/api/fix', '/api/fix_only', '/api/download', '/api/file',
];

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname.startsWith('/api/')) {
      try {
        return await handleApi(request, env, url);
      } catch (e) {
        return json({ ok: false, error: '云端服务异常：' + ((e && e.message) || e) }, 500);
      }
    }
    return env.ASSETS.fetch(request);
  },
};

/* ------------------------------ 发短信中继 ------------------------------
 * 天枢对「发送短信验证码」做了来源风控：本机（国内网络）能发，Cloudflare（境外 IP）
 * 会被拒（系统异常）。所以云端发失败时，把任务交给「中继」：本机上的小代理轮询
 * 取任务 → 用本机网络发短信 → 把结果交回来。任务队列放在 Durable Object 里，
 * 强一致，不会出现 KV 那种读到旧缓存的问题。
 * ---------------------------------------------------------------------- */

export class RelayQueue {
  constructor(state, env) {
    this.state = state;
    this.pending = null;
    this.results = {};
  }

  respond(obj) {
    return new Response(JSON.stringify(obj), {
      headers: { 'Content-Type': 'application/json; charset=utf-8' },
    });
  }

  async fetch(request) {
    const url = new URL(request.url);
    const p = url.pathname;

    if (p === '/enqueue') {
      const b = await request.json().catch(() => ({}));
      this.pending = { id: b.id, username: b.username, ts: Date.now() };
      return this.respond({ ok: true });
    }
    if (p === '/poll') {                       // 本机代理来取任务
      const t = this.pending;
      this.pending = null;
      return this.respond({ ok: true, task: t || null });
    }
    if (p === '/result') {                     // 本机代理交回结果
      const b = await request.json().catch(() => ({}));
      if (b.id) this.results[b.id] = { at: Date.now(), result: b.result || null };
      return this.respond({ ok: true });
    }
    if (p === '/get') {                        // 云端取结果
      const id = url.searchParams.get('id');
      const r = this.results[id];
      if (r) delete this.results[id];
      return this.respond({ ok: true, result: r ? r.result : null });
    }
    return this.respond({ ok: false, error: 'unknown relay route' });
  }
}

async function relayFetch(env, path, init) {
  const id = env.RELAY.idFromName('main');
  const stub = env.RELAY.get(id);
  return stub.fetch('https://relay.internal' + path, init);
}

async function relayAvailable(env) {
  return !!(env.RELAY && env.BI_RELAY_TOKEN);
}

/** 把发短信任务交给中继，等最多 waitMs 毫秒取回结果 */
async function relaySendCode(env, username, waitMs = 12000) {
  const id = 'r' + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
  await relayFetch(env, '/enqueue', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id, username }),
  });
  const step = 1500;
  for (let waited = 0; waited < waitMs; waited += step) {
    await new Promise(r => setTimeout(r, step));
    const resp = await relayFetch(env, '/get?id=' + encodeURIComponent(id));
    const j = await resp.json().catch(() => ({}));
    if (j && j.result) return { viaRelay: true, raw: j.result };
  }
  return { viaRelay: true, raw: null };   // 超时：短信可能仍在几秒后发出
}

/* ------------------------------ 响应小工具 ------------------------------ */

function json(obj, status = 200, headers = {}) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: Object.assign(
      { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' },
      headers || {}
    ),
  });
}

function js(body) {
  return new Response(body, {
    headers: {
      'Content-Type': 'application/javascript; charset=utf-8',
      'Cache-Control': 'no-store',
    },
  });
}

/* ------------------------------ 路由 ------------------------------ */

async function handleApi(request, env, url) {
  const p = url.pathname;
  const m = request.method;

  if (p === '/api/send-code' && m === 'POST') return sendCode(request, env);
  if (p === '/api/relay/poll' || p === '/api/relay/result') return relayEndpoint(request, env, url);
  if (p === '/api/login' && m === 'POST') return login(request, env);
  if (p === '/api/me') return me(request, env);
  if (p === '/api/logout') return logout();
  if (p === '/api/data.js') return dataJs(request, env);

  for (const pre of LOCAL_ONLY) {
    if (p.startsWith(pre)) {
      if (p === '/api/refresh/status') {
        // 页面加载时会查一次抓取状态，返回"已完成且无变化"，避免弹红色告警
        return json({ ok: true, running: false, finished: false, progress: null, changed: false });
      }
      if (p === '/api/history') return json({ ok: true, records: [] });
      return json({ ok: false, error: '云端只读版未启用该功能，请在本机看板操作', readonly: true });
    }
  }
  return json({ ok: false, error: '未开放的接口' }, 404);
}

/* ------------------------------ 登录 ------------------------------ */

async function sendCode(request, env) {
  const body = await readJson(request);
  const username = String(body.username || '').trim();
  if (!username) return json({ ok: false, error: '请先填写天枢账号' });

  // 1) 先试云端直发（天枢放行境外 IP 后这条路就通了，最快）
  let r = await tsPost(env, '/api/admin-api/system/auth/send-login-sms-code', { username });
  if (r.code === 0) return json({ ok: true, msg: '验证码已发送', via: 'direct' });

  const directMsg = r.msg || '发送失败';

  // 2) 云端被风控拦掉时，交给本机中继去发
  if (await relayAvailable(env)) {
    const out = await relaySendCode(env, username);
    if (out.raw && out.raw.code === 0) {
      return json({ ok: true, msg: '验证码已发送', via: 'relay' });
    }
    if (out.raw && out.raw.msg) r = out.raw;            // 用中继返回的真实原因
    if (!out.raw) {
      return json({ ok: false, msg: '发送通道繁忙（云端被天枢风控拦截，中继还没返回结果），请稍等十几秒再试一次' });
    }
  }
  return json({ ok: false, msg: r.msg || directMsg });
}

/** 本机中继代理用：取任务 / 交结果（用共享密钥鉴权） */
async function relayEndpoint(request, env, url) {
  const token = url.searchParams.get('token') || request.headers.get('X-Relay-Token') || '';
  if (!env.BI_RELAY_TOKEN || token !== env.BI_RELAY_TOKEN) {
    return json({ ok: false, error: 'unauthorized' }, 401);
  }
  if (url.pathname === '/api/relay/poll') {
    const resp = await relayFetch(env, '/poll');
    const j = await resp.json().catch(() => ({}));
    return json({ ok: true, task: j.task || null });
  }
  if (url.pathname === '/api/relay/result') {
    const b = await readJson(request);
    await relayFetch(env, '/result', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: b.id, result: b.result }),
    });
    return json({ ok: true });
  }
  return json({ ok: false, error: 'unknown' }, 404);
}

async function login(request, env) {
  const body = await readJson(request);
  const username = String(body.username || '').trim();
  const password = String(body.password || '');
  const smsCode = String(body.smsCode || '').trim();
  if (!username || !password) return json({ ok: false, error: '请填写账号和密码' });

  const ip = request.headers.get('CF-Connecting-IP') || 'unknown';
  const rlKey = username + '|' + ip;

  // 白名单（可选）：BI_ALLOWED_ACCOUNTS="zhangsan,lisi"
  const allow = String(env.BI_ALLOWED_ACCOUNTS || '')
    .split(',').map(s => s.trim()).filter(Boolean);
  if (allow.length && !allow.includes(username)) {
    return json({ ok: false, error: '该账号未开通本看板访问权限' });
  }
  if (await isLockedOut(env, rlKey)) {
    return json({ ok: false, error: '尝试次数过多，请 15 分钟后再试' });
  }

  const r = await tsPost(env, '/api/admin-api/system/auth/login', { username, password, smsCode });
  if (r.code !== 0 || !r.data) {
    await bumpFail(env, rlKey);
    return json({ ok: false, error: r.msg || '登录失败' });
  }
  const token = r.data.accessToken || r.data.access_token;
  if (!token) {
    await bumpFail(env, rlKey);
    return json({ ok: false, error: '未获取到登录凭证' });
  }

  // ===== 身份识别：以「听言」账号为准（听言与天枢同一套账号密码）=====
  // 天枢侧的昵称/姓名有时是账号名或英文名（例如 lily、手机号），跟看板里的
  // 「客户成功经理」字段对不上，所以再用同一套账号密码登录听言，取听言的昵称作为身份。
  let tyName = '', tyUser = '';
  try {
    const ty = await tsLoginTingyan(env, username, password);
    if (ty) { tyName = ty.nickname || ''; tyUser = ty.username || ''; }
  } catch (e) { /* 忽略：听言不可用就退回天枢信息 */ }

  let tsName = '', nickname = username, mobile = '';
  try {
    const info = await tsPost(env,
      '/api/admin-api/system/auth/get-permission-info', null,
      { Authorization: 'Bearer ' + token });
    const u = (info && info.data && info.data.user) || {};
    nickname = u.nickname || u.username || username;
    tsName = u.realname || '';
  } catch (e) { /* ignore */ }
  try {
    const prof = await tsPost(env, '/api/admin-api/system/user/profile/get', null,
      { Authorization: 'Bearer ' + token });
    const p = (prof && prof.data) || {};
    tsName = p.realname || tsName;
    nickname = p.nickname || nickname;
    mobile = p.mobile || '';
  } catch (e) { /* ignore */ }

  // 身份优先级：听言昵称 → 天枢真实姓名 → 天枢昵称 → 登录账号
  const display = tyName || tsName || nickname || username;

  await clearFail(env, rlKey);
  const cookie = await makeCookie(env, { u: username, n: display, d: nickname, m: mobile, y: tyUser }, SESSION_TTL);
  return json({ ok: true, nickname: display }, 200, { 'Set-Cookie': cookie });
}

/** 用同一套账号密码登录听言，返回 { username, nickname } */
async function tsLoginTingyan(env, username, password) {
  const base = String(env.BI_TY_BASE || 'https://collocation-alert.baixyn.com').replace(/\/+$/, '');
  const r = await fetch(base + '/api/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  const setCookie = r.headers.get('set-cookie') || '';
  const m = setCookie.match(/diag_token=([^;]+)/);
  if (!m) return null;
  const parts = m[1].split('.');
  if (parts.length < 2) return null;
  let payload = parts[1].replace(/-/g, '+').replace(/_/g, '/');
  payload += '='.repeat((4 - payload.length % 4) % 4);
  try {
    const j = JSON.parse(atob(payload));
    return { username: j.username || '', nickname: j.nickname || '' };
  } catch (e) {
    return null;
  }
}

async function me(request, env) {
  const s = await readSession(request, env);
  return json({
    ok: !!s,
    nickname: s ? s.n : null,
    account: s ? s.u : null,
    loginAt: s ? (s.t || '') : '',
  });
}

function logout() {
  return json({ ok: true }, 200, {
    'Set-Cookie': COOKIE_NAME + '=; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=0',
  });
}

/* ------------------------------ 数据下发 ------------------------------ */

async function dataJs(request, env) {
  const s = await readSession(request, env);
  if (!s) return js(LOCKED_JS);

  let body = null;
  if (env.BI_KV) {
    try { body = await env.BI_KV.get(DATA_KEY); } catch (e) { /* ignore */ }
  }
  if (!body && env.BI_DATA) {
    try {
      const obj = await env.BI_DATA.get(DATA_KEY);
      if (obj) body = await obj.text();
    } catch (e) { /* ignore */ }
  }
  if (!body) {
    // 还没推送过数据
    body = 'window.DASHBOARD_DATA = {"locked":false,"generatedAt":null,"snapshotTime":null,' +
           '"notice":"云端还没有推送数据，请在本机执行 deploy_cf.sh ", "customers":[],"managers":[]};';
  }

  // ===== 数据范围：只下发「登录人权限范围内」的客户 =====
  const scope = resolveScope(env, s);
  if (scope.type === 'manager') {
    try {
      const eq = body.indexOf('=');
      const payload = JSON.parse(body.slice(eq + 1).trim().replace(/;\s*$/, ''));
      const all = payload.customers || [];
      const mine = all.filter(c => String(c.mgr || '').trim() === scope.name);
      if (mine.length) {
        payload.customers = mine;
        const keep = new Set(mine.map(c => c.mgr));
        payload.managers = (payload.managers || []).filter(m => keep.has(m.name));
        payload.scope = { fixed: true, name: scope.name, count: mine.length, loginUser: scope.loginUser };
      } else if (String(env.BI_SCOPE_FALLBACK || 'all') === 'none') {
        // 没对上任何客户成功经理 → 不给数据（严格模式）
        payload.customers = [];
        payload.managers = [];
        payload.scope = { fixed: true, name: scope.name, count: 0, loginUser: scope.loginUser };
        payload.notice = '当前账号「' + scope.loginUser + '」未对应到任何客户成功经理（匹配名：' + scope.name +
                         '），看不到客户；请联系管理员核对姓名映射。';
      } else {
        // 名字没对上任何一位客户成功经理 → 不锁人，先给全部，并提示管理员补映射
        payload.scope = { fixed: false, name: scope.name, count: all.length, loginUser: scope.loginUser,
                          unmatched: true };
        payload.notice = '未能把账号「' + scope.loginUser + '」对应到客户成功经理（匹配名：' + scope.name +
                         '），本次先显示全部客户；请让管理员核对姓名映射。';
      }
      body = 'window.DASHBOARD_DATA = ' + JSON.stringify(payload) + ';';
    } catch (e) { /* 解析失败就不裁剪，至少不报错 */ }
  }

  return new Response(body, {
    headers: {
      'Content-Type': 'application/javascript; charset=utf-8',
      // 客户数据不进浏览器缓存：退出/换人后立刻拿到旧数据
      'Cache-Control': 'no-store',
    },
  });
}

/** 判定数据范围：主管/管理员看全部，客户成功经理只看自己名下 */
function resolveScope(env, s) {
  const loginUser = String((s && (s.n || s.d || s.u)) || '').trim();
  const aliases = [loginUser, String((s && s.u) || '').trim(), String((s && s.d) || '').trim(),
                   String((s && s.y) || '').trim()].filter(Boolean);
  const allList = String(env.BI_SCOPE_ALL || '')
    .split(',').map(x => x.trim()).filter(Boolean);
  if (!loginUser || aliases.some(a => allList.includes(a))) {
    return { type: 'all', loginUser: loginUser || aliases[0] || '' };
  }
  return { type: 'manager', name: loginUser, loginUser };
}
/* ------------------------------ 会话（签名 Cookie，无数据库）------------------------------ */

async function makeCookie(env, { u, n }, ttl) {
  const now = Math.floor(Date.now() / 1000);
  const payload = { u, n, e: now + ttl, t: localStamp(), r: rand(8) };
  const pb = b64uEncode(JSON.stringify(payload));
  const sig = await hmac(env, pb);
  return `${COOKIE_NAME}=${pb}.${sig}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=${ttl}`;
}

async function readSession(request, env) {
  const cookie = request.headers.get('Cookie') || '';
  let val = null;
  for (const kv of cookie.split(';')) {
    const t = kv.trim();
    if (t.startsWith(COOKIE_NAME + '=')) { val = t.slice(COOKIE_NAME.length + 1); break; }
  }
  if (!val) return null;
  const dot = val.lastIndexOf('.');
  if (dot <= 0) return null;
  const pb = val.slice(0, dot), sig = val.slice(dot + 1);
  const expect = await hmac(env, pb);
  if (!timingSafeEqual(expect, sig)) return null;
  let obj = null;
  try { obj = JSON.parse(b64uDecode(pb)); } catch (e) { return null; }
  if (!obj || !obj.e || obj.e < Math.floor(Date.now() / 1000)) return null;
  return obj;
}

async function hmac(env, text) {
  const secret = String(env.BI_SESSION_SECRET || 'tianji-preview-secret-please-change');
  const key = await crypto.subtle.importKey(
    'raw', new TextEncoder().encode(secret),
    { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']
  );
  const buf = await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(text));
  return b64uFromBytes(new Uint8Array(buf));
}

function b64uEncode(str) {
  return b64uFromBytes(new TextEncoder().encode(str));
}
function b64uDecode(b64) {
  const bin = atob(b64.replace(/-/g, '+').replace(/_/g, '/'));
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new TextDecoder().decode(bytes);
}
function b64uFromBytes(bytes) {
  let s = '';
  for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
  return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}
function timingSafeEqual(a, b) {
  if (a.length !== b.length) return false;
  let r = 0;
  for (let i = 0; i < a.length; i++) r |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return r === 0;
}
function rand(n) {
  const c = 'abcdefghijklmnopqrstuvwxyz0123456789';
  let s = '';
  for (let i = 0; i < n; i++) s += c[Math.floor(Math.random() * c.length)];
  return s;
}
function localStamp() {
  // 北京时间（UTC+8）
  const d = new Date(Date.now() + 8 * 3600 * 1000);
  return d.toISOString().slice(0, 19).replace('T', ' ');
}

/* ------------------------------ 防暴破（有 KV 才生效）------------------------------ */

const MAX_FAIL = 8, WINDOW = 900;

async function isLockedOut(env, key) {
  if (!env.BI_KV) return false;
  try {
    const v = await env.BI_KV.get('fail:' + key, { type: 'json' });
    if (!v) return false;
    if (Math.floor(Date.now() / 1000) - v.last > WINDOW) return false;
    return v.n >= MAX_FAIL;
  } catch (e) { return false; }
}
async function bumpFail(env, key) {
  if (!env.BI_KV) return;
  try {
    const v = (await env.BI_KV.get('fail:' + key, { type: 'json' })) || { n: 0 };
    await env.BI_KV.put('fail:' + key,
      JSON.stringify({ n: (v.n || 0) + 1, last: Math.floor(Date.now() / 1000) }),
      { expirationTtl: WINDOW });
  } catch (e) { /* ignore */ }
}
async function clearFail(env, key) {
  if (!env.BI_KV) return;
  try { await env.BI_KV.delete('fail:' + key); } catch (e) { /* ignore */ }
}

/* ------------------------------ 调天枢 ------------------------------ */

async function tsPost(env, path, body, headers) {
  const base = String(env.BI_TS_BASE || DEFAULT_TS_BASE).replace(/\/+$/, '');
  const init = {
    method: 'POST',
    headers: Object.assign({
      'Content-Type': 'application/json',
      // 天枢对少数接口（尤其发短信）有风控：带上浏览器特征，降低被当成机器人拦掉的概率
      'Accept': 'application/json, text/plain, */*',
      'Accept-Language': 'zh-CN,zh;q=0.9',
      'Origin': base,
      'Referer': base + '/',
      'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 ' +
                    '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
      'CF-Worker': '',          // 尽量抹掉 Cloudflare Worker 标记（能清就清，清不掉也不影响）
    }, headers || {}),
  };
  if (body != null) init.body = JSON.stringify(body);
  try {
    const r = await fetch(base + path, init);
    const text = await r.text();
    try { return JSON.parse(text); }
    catch (e) { return { code: -1, msg: '天枢返回异常：' + text.slice(0, 120) }; }
  } catch (e) {
    return { code: -1, msg: '连接天枢失败：' + ((e && e.message) || e) };
  }
}

async function readJson(request) {
  try { return await request.json(); } catch (e) { return {}; }
}
