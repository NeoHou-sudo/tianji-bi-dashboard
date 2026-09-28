/**
 * 本地联调用的假服务器：
 *   - 直接跑 cf-worker/src/index.js（和线上同一份代码）
 *   - 静态文件从 ../public 读
 *   - KV 用内存 Map，预置真实的 assets/data.js
 *   - "天枢"接口用桩替代：账号 test001 / 密码 goodpass / 验证码 1234 可登录成功
 *
 * 用法：node cf-worker/test/dev_server.mjs   →  http://127.0.0.1:8913
 */
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import worker from '../src/index.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PUB = path.join(__dirname, '..', 'public');
const PORT = Number(process.env.PORT || 8913);

// ---------- 内存 KV ----------
const KV = new Map();
try {
  KV.set('data.js', fs.readFileSync(path.join(__dirname, '..', '..', 'assets', 'data.js'), 'utf8'));
  console.log('KV 已预置真实 data.js');
} catch (e) {
  console.log('未找到 assets/data.js，KV 为空');
}
const kv = {
  async get(k, opts) {
    const v = KV.get(k);
    if (v == null) return null;
    if (opts && opts.type === 'json') { try { return JSON.parse(v); } catch (e) { return null; } }
    return v;
  },
  async put(k, v) { KV.set(k, v); },
  async delete(k) { KV.delete(k); },
};

// ---------- 桩：天枢 ----------
const realFetch = globalThis.fetch;
globalThis.fetch = async (url, init) => {
  const u = String(url);
  if (u.includes('ts.baixyn.com')) {
    const p = new URL(u).pathname;
    let body = {};
    try { body = init && init.body ? JSON.parse(init.body) : {}; } catch (e) {}
    if (p.endsWith('/auth/send-login-sms-code')) return jr({ code: 0, msg: 'OK' });
    if (p.endsWith('/auth/login')) {
      if (body.password === 'goodpass' && body.smsCode === '1234') return jr({ code: 0, data: { accessToken: 'TOKEN-ABC' } });
      return jr({ code: 1, msg: '账号或密码不正确' });
    }
    if (p.endsWith('/get-permission-info')) return jr({ code: 0, data: { user: { nickname: '侯子昱' } } });
    return jr({ code: -1, msg: 'no route: ' + p });
  }
  return realFetch(url, init);
};
function jr(o) { return new Response(JSON.stringify(o), { headers: { 'Content-Type': 'application/json' } }); }

// ---------- 静态资源 ----------
function serveStatic(pathname) {
  let p = decodeURIComponent(pathname);
  if (p === '/' || p === '') p = '/index.html';
  const fp = path.join(PUB, p);
  if (!fp.startsWith(PUB) || !fs.existsSync(fp) || fs.statSync(fp).isDirectory()) {
    return new Response('Not found', { status: 404 });
  }
  const ext = path.extname(fp);
  const type = ext === '.html' ? 'text/html; charset=utf-8'
    : ext === '.js' ? 'application/javascript; charset=utf-8'
    : ext === '.css' ? 'text/css; charset=utf-8' : 'application/octet-stream';
  return new Response(fs.readFileSync(fp), { headers: { 'Content-Type': type } });
}

const env = {
  ASSETS: { fetch: (req) => Promise.resolve(serveStatic(new URL(req.url).pathname)) },
  BI_KV: kv,
  BI_SESSION_SECRET: 'dev-secret',
  BI_ALLOWED_ACCOUNTS: process.env.ALLOW || '',
  BI_TS_BASE: 'https://ts.baixyn.com',
};

http.createServer(async (req, res) => {
  const chunks = [];
  for await (const c of req) chunks.push(c);
  const body = Buffer.concat(chunks);
  const headers = new Headers();
  for (const [k, v] of Object.entries(req.headers)) {
    try { headers.set(k, Array.isArray(v) ? v.join(',') : v); } catch (e) {}
  }
  const request = new Request('http://127.0.0.1:' + PORT + req.url, {
    method: req.method,
    headers,
    body: (req.method === 'GET' || req.method === 'HEAD') ? undefined : body,
  });
  let resp;
  try {
    resp = await worker.fetch(request, env);
  } catch (e) {
    resp = new Response('server error: ' + e.message, { status: 500 });
  }
  const out = {};
  resp.headers.forEach((v, k) => { out[k] = v; });
  res.writeHead(resp.status, out);
  res.end(Buffer.from(await resp.arrayBuffer()));
}).listen(PORT, '127.0.0.1', () => console.log('dev server → http://127.0.0.1:' + PORT));
