#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
客户续费 BI 看板 · 本地服务
- 提供看板静态文件
- POST /api/fix        : 生成该客户的修正成品并发送给对应客户成功经理(飞书)
- POST /api/fix_only   : 只生成文件（不发）
用法: python3 bi_server.py [port]  (默认 8899)
"""
import http.server, socketserver, json, os, sys, subprocess, threading, traceback, datetime
from urllib.parse import urlparse

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.dirname(BASE)
LARK = os.environ.get("LARK_CLI", "/Applications/Loomy.app/Contents/Resources/lark-cli/lark-cli")
MAP_FILE = os.environ.get("CSM_MAP_FILE", os.path.expanduser("~/.baixyn-api/csm_feishu_map.json"))
DRIVE_FOLDER = os.environ.get("DRIVE_FOLDER", "MgWffMXFzlJWn4dDtVIcEdSpn2b")
FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
FEISHU_SHARE_HOST = os.environ.get("FEISHU_SHARE_HOST", "iihcw7mp26x.feishu.cn")
# 监听地址/端口：兼容 Render/Heroku 等云平台的 PORT 约定
HOST = os.environ.get("BI_HOST") or ("0.0.0.0" if os.environ.get("PORT") else "127.0.0.1")
PORT = int(os.environ.get("BI_PORT") or os.environ.get("PORT") or (sys.argv[1] if len(sys.argv) > 1 else 8899))
TS_BASE = "https://ts.baixyn.com"
CORS_ORIGIN = os.environ.get("BI_ALLOWED_ORIGIN", "")   # 留空=同源部署，不放行跨域
COOKIE_SECURE = os.environ.get("BI_COOKIE_SECURE", "0") == "1"
SESSION_DB = os.environ.get("BI_SESSION_DB", os.path.join(BASE, "sessions.db"))
ARCHIVE_FILE = os.path.join(BASE, "归档记录.json")
KIND_LABEL = {"kw25": "2.5配词", "kw30": "3.0配词", "email": "邮件"}

# ===== 会话 & 登录防暴破（SQLite 持久化，重启不掉线）=====
import sqlite3, time, uuid


def _db():
    c = sqlite3.connect(SESSION_DB, timeout=10)
    c.execute("CREATE TABLE IF NOT EXISTS sessions(sid TEXT PRIMARY KEY, username TEXT, nickname TEXT, token TEXT, expires REAL, login_at TEXT)")
    try:
        c.execute("ALTER TABLE sessions ADD COLUMN login_at TEXT")
    except Exception:
        pass
    c.execute("CREATE TABLE IF NOT EXISTS login_fail(k TEXT PRIMARY KEY, n INTEGER, last REAL)")
    return c


def sess_create(info, ttl=28800):
    import datetime as _dt
    sid = uuid.uuid4().hex
    c = _db()
    c.execute("INSERT OR REPLACE INTO sessions(sid,username,nickname,token,expires,login_at) VALUES(?,?,?,?,?,?)",
              (sid, info.get("username"), info.get("nickname"), info.get("token"), time.time() + ttl,
               _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    c.commit(); c.close()
    return sid


def sess_get(sid):
    if not sid:
        return None
    c = _db()
    r = c.execute("SELECT sid,username,nickname,token,expires,login_at FROM sessions WHERE sid=?", (sid,)).fetchone()
    if r and r[4] > time.time():
        c.close()
        return {"sid": r[0], "username": r[1], "nickname": r[2], "token": r[3], "loginAt": r[5] or ""}
    if r:
        c.execute("DELETE FROM sessions WHERE sid=?", (sid,)); c.commit()
    c.close()
    return None


def sess_del(sid):
    c = _db(); c.execute("DELETE FROM sessions WHERE sid=?", (sid,)); c.commit(); c.close()


def sess_cookie(sid, ttl=28800):
    a = f"bi_sid={sid}; Path=/; HttpOnly; SameSite=Lax; Max-Age={ttl}"
    if COOKIE_SECURE:
        a += "; Secure"
    return a


LOGIN_MAX_FAIL = int(os.environ.get("BI_LOGIN_MAX_FAIL", "8"))
LOGIN_WINDOW = int(os.environ.get("BI_LOGIN_WINDOW", "900"))


def login_blocked(key):
    c = _db()
    r = c.execute("SELECT n,last FROM login_fail WHERE k=?", (key,)).fetchone()
    c.close()
    if not r:
        return False
    n, last = r
    if time.time() - last > LOGIN_WINDOW:
        return False
    return n >= LOGIN_MAX_FAIL


def login_fail(key):
    c = _db()
    r = c.execute("SELECT n FROM login_fail WHERE k=?", (key,)).fetchone()
    n = (r[0] if r else 0) + 1
    c.execute("INSERT OR REPLACE INTO login_fail(k,n,last) VALUES(?,?,?)", (key, n, time.time()))
    c.commit(); c.close()


def login_ok(key):
    c = _db(); c.execute("DELETE FROM login_fail WHERE k=?", (key,)); c.commit(); c.close()


def _load_archive():
    if os.path.exists(ARCHIVE_FILE):
        try:
            return json.load(open(ARCHIVE_FILE, encoding="utf-8"))
        except Exception:
            return []
    return []


def _save_archive(recs):
    try:
        json.dump(recs[:500], open(ARCHIVE_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception:
        pass


def archive_add(rec):
    recs = _load_archive()
    recs.insert(0, rec)
    _save_archive(recs)


def scan_archive():
    """扫描磁盘上已有的产物，补建历史记录（让之前生成过的包也能被翻出来）"""
    import zipfile, re, datetime as _dt
    recs = []
    zdir = os.path.join(BASE, "批量打包")
    if os.path.isdir(zdir):
        for fn in sorted(os.listdir(zdir), reverse=True):
            if not fn.lower().endswith(".zip"):
                continue
            fp = os.path.join(zdir, fn)
            try:
                st = os.stat(fp)
            except Exception:
                continue
            m = re.match(r"^(.*?)_客户优化方案_(\d{8}_\d{6})\.zip$", fn)
            mgr = m.group(1) if m else "全部客户"
            if m:
                ts = m.group(2)
                tstr = "%s-%s-%s %s:%s:%s" % (ts[0:4], ts[4:6], ts[6:8], ts[9:11], ts[11:13], ts[13:15])
            else:
                tstr = _dt.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            try:
                with zipfile.ZipFile(fp) as zf:
                    names = zf.namelist()
            except Exception:
                names = []
            recs.append({
                "id": "file:" + fn, "time": tstr, "kind": "batch", "source": "scan",
                "count": None, "customers": [],
                "packages": [{"manager": mgr, "zip": fn, "link": None, "count": None,
                              "customers": [], "details": [], "files": names}],
            })
    return recs


def _http(url, data=None, headers=None, method=None):
    import urllib.request
    h = {"Content-Type": "application/json"}
    if headers: h.update(headers)
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"code": -1, "msg": str(e)}


def ts_send_code(username):
    """调天枢发短信验证码"""
    return _http(f"{TS_BASE}/api/admin-api/system/auth/send-login-sms-code", {"username": username})


def ts_login(username, password, sms_code):
    """调天枢登录，返回 (ok, msg, user_info)"""
    r = _http(f"{TS_BASE}/api/admin-api/system/auth/login",
              {"username": username, "password": password, "smsCode": sms_code})
    if r.get("code") != 0 or not r.get("data"):
        return False, r.get("msg", "登录失败"), None
    token = r["data"].get("accessToken") or r["data"].get("access_token")
    if not token:
        return False, "未获取到登录凭证", None
    info = _http(f"{TS_BASE}/api/admin-api/system/auth/get-permission-info",
                 headers={"Authorization": "Bearer " + token})
    user = (info.get("data") or {}).get("user") or {}
    return True, "ok", {"username": username, "nickname": user.get("nickname") or user.get("username") or username,
                        "token": token}


# ===== 手动「快照」：后台跑抓取流水线（不占用 AI 额度，纯接口+本地计算）=====
REFRESH = {"running": False, "started": None, "finished": None, "ok": None,
           "changed": None, "snapshotTime": None, "msg": "", "hint": ""}


def _read_snaptime():
    try:
        js = json.loads(open(os.path.join(BASE, "assets", "data.js"), encoding="utf-8")
                        .read().split("=", 1)[1].rstrip().rstrip(";"))
        return js.get("snapshotTime") or js.get("generatedAt")
    except Exception:
        return None


def _run_refresh():
    import datetime as _dt
    try:
        env = dict(os.environ); env["SKIP_NOTIFY"] = "1"
        # 用当前登录用户的「天枢 token」覆盖本地凭证（刚通过短信验证码登录，最新有效）
        if REFRESH.get("_ts_token"):
            env["TS_TOKEN_OVERRIDE"] = REFRESH["_ts_token"]
        r = subprocess.run(["python3", os.path.join(BASE, "daily_update.py")],
                           capture_output=True, text=True, timeout=3600, cwd=BASE, env=env)
        tail = ((r.stdout or "") + (r.stderr or "")).strip()
        after = _read_snaptime()
        REFRESH["ok"] = (r.returncode == 0)
        REFRESH["snapshotTime"] = after
        REFRESH["changed"] = bool(after and after != REFRESH.get("_before"))
        REFRESH["msg"] = ("已更新" if REFRESH["changed"] else "未产生新数据") + \
            (("：" + tail[-200:]) if (not REFRESH["ok"] and tail) else "")
        # 给出可操作的处置提示（前端会用常驻消息栏展示）
        REFRESH["hint"] = ""
        if not REFRESH["changed"]:
            if ("售后看板拉取为空" in tail) or ("token 可能失效" in tail):
                REFRESH["hint"] = ("天枢登录凭证已失效。请点右上角「退出」后，用短信验证码重新登录天玑，"
                                   "再点「⟳ 快照」即可（登录时会自动续期天枢/听言两边的凭证）。")
            elif "听言" in tail and ("失败" in tail or "非 JSON" in tail or "未成功" in tail):
                REFRESH["hint"] = "听言凭证可能已过期。重新登录天玑会自动刷新听言凭证，然后再点「⟳ 快照」。"
            else:
                REFRESH["hint"] = "本次抓取完成但数据无变化，可稍后重试；若持续如此请查看服务端日志。"
    except subprocess.TimeoutExpired:
        REFRESH["ok"] = False; REFRESH["msg"] = "抓取超时"
    except Exception as e:
        REFRESH["ok"] = False; REFRESH["msg"] = "异常：" + str(e)
    finally:
        REFRESH["running"] = False
        REFRESH["finished"] = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def start_refresh(ts_token=""):
    if REFRESH["running"]:
        return False
    import datetime as _dt
    REFRESH.update({"running": True, "started": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "finished": None, "ok": None, "changed": None, "hint": "",
                    "snapshotTime": None, "msg": "抓取中…", "_before": _read_snaptime(),
                    "_ts_token": ts_token or "", "_t0": time.time()})
    threading.Thread(target=_run_refresh, daemon=True).start()
    return True


TY_LOGIN_URL = os.environ.get("TY_LOGIN_URL", "https://collocation-alert.baixyn.com/api/login")
PROGRESS_FILE = os.path.join(BASE, "_progress.json")


DOC_HTML = ""  # 口径文档已关闭，不再暴露计算逻辑

RATE = {}                      # ip -> [窗口开始, 计数]
RATE_LIMIT = int(os.environ.get("BI_RATE_LIMIT", "300"))   # 每 IP 每分钟
MAX_BODY = int(os.environ.get("BI_MAX_BODY", str(1024 * 1024)))  # 请求体上限 1MB


def rate_ok(ip):
    now = time.time()
    w = RATE.get(ip)
    if not w or now - w[0] > 60:
        RATE[ip] = [now, 1]
        return True
    w[1] += 1
    if len(RATE) > 5000:                      # 防内存膨胀
        RATE.clear()
    return w[1] <= RATE_LIMIT


def _read_progress():
    try:
        return json.load(open(PROGRESS_FILE, encoding="utf-8"))
    except Exception:
        return None


def ty_login(username, password):
    """用听言账号密码换 diag_token（登录天玑时顺手刷新听言凭证）"""
    import urllib.request, re as _re
    body = json.dumps({"username": username, "password": password}).encode("utf-8")
    req = urllib.request.Request(TY_LOGIN_URL, data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        text = r.read().decode("utf-8", "replace")
        cookies = r.headers.get_all("Set-Cookie") or []
    try:
        d = json.loads(text)
    except Exception:
        return False, "听言返回非 JSON", None
    if not d.get("success"):
        return False, d.get("message") or d.get("msg") or "听言登录失败", None
    tok = ""
    for c in cookies:
        m = _re.search(r"diag_token=([^;]+)", c)
        if m:
            tok = m.group(1); break
    tok = tok or d.get("token") or ""
    return bool(tok), ("ok" if tok else "未取到 token"), tok


def _save_ty_token(tok):
    """把新 token 写回本地凭证文件，供抓取脚本使用"""
    try:
        import datetime as _dt
        pth = os.path.expanduser("~/.baixyn-api/credentials.json")
        creds = json.load(open(pth, encoding="utf-8"))
        creds.setdefault("collocation_alert", {})["diag_token"] = tok
        creds["collocation_alert"]["expires_at_note"] = "由登录天玑时自动刷新 " + _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        json.dump(creds, open(pth, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print("[ty-login] 写回凭证失败:", e, file=sys.stderr)
        return False


# 允许登录本看板的账号白名单（空=不限制）；多个用英文逗号分隔
ALLOWED_ACCOUNTS = [x.strip() for x in os.environ.get("BI_ALLOWED_ACCOUNTS", "").split(",") if x.strip()]


def audit(event, account, ip, extra=""):
    """访问审计：谁、什么时候、从哪来、做了什么"""
    try:
        import datetime as _dt
        os.makedirs(os.path.join(BASE, "日志"), exist_ok=True)
        with open(os.path.join(BASE, "日志", "登录审计.log"), "a", encoding="utf-8") as f:
            f.write("[%s] %-8s account=%s ip=%s %s\n" % (
                _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), event, account, ip, extra))
    except Exception:
        pass


def valid_customer_names():
    try:
        s = open(os.path.join(BASE, "assets", "data.js"), encoding="utf-8").read()
        js = json.loads(s[s.find("=") + 1:].rstrip().rstrip(";"))
        return {c["name"] for c in (js.get("customers") or [])}
    except Exception:
        return set()


def upload_to_drive(local_path):
    """上传文件到飞书云空间专用文件夹，设组织内可读，返回 (file_token, url)。
    优先走飞书 OpenAPI 直连（跨平台、无需 lark-cli）；未配置应用凭证时自动回退 lark-cli。"""
    # ---- 首选：OpenAPI 直连 ----
    if FEISHU_APP_ID and FEISHU_APP_SECRET:
        try:
            import feishu_upload
            tok, url = feishu_upload.upload_to_folder(local_path, DRIVE_FOLDER)
            if tok:
                print("[drive] OpenAPI 上传成功 " + os.path.basename(local_path), file=sys.stderr)
                return tok, url
        except Exception as e:
            print("[drive] OpenAPI 上传失败，回退 lark-cli：" + str(e), file=sys.stderr)
    # ---- 回退：lark-cli（仅 macOS / 已安装环境可用）----
    try:
        # lark-cli 的 --file 只接受相对路径，故切到 BASE 用相对路径
        rel = os.path.relpath(local_path, BASE)
        r = subprocess.run([LARK, "drive", "+upload", "--file", rel,
                            "--folder-token", DRIVE_FOLDER, "--as", "user"],
                           capture_output=True, text=True, timeout=180, cwd=BASE)
        print(f"[drive] rc={r.returncode} out={r.stdout[:150]!r} err={r.stderr[:150]!r}", file=sys.stderr)
        import re
        m = re.search(r'\{[\s\S]*\}', r.stdout)
        if not m:
            return None, None
        d = json.loads(m.group(0))
        token = (d.get("data") or {}).get("file_token")
        if not token:
            return None, None
        # 设置组织内可读
        subprocess.run([LARK, "api", "PATCH", f"/open-apis/drive/v1/permissions/{token}/public",
                        "--params", '{"type":"file"}',
                        "--data", '{"link_share_entity":"tenant_readable","external_access_entity":"open","comment_entity":"anyone_can_view","share_entity":"anyone"}',
                        "--as", "user"], capture_output=True, text=True, timeout=60)
        return token, f"https://{FEISHU_SHARE_HOST}/file/{token}"
    except Exception as e:
        print("[drive] upload failed:", e, file=sys.stderr)
        return None, None

sys.path.insert(0, BASE)
import gen_fix  # 生成引擎
import cred_store  # 凭证留档系统
import local_accounts  # 本地账号系统（超管/主管）


def load_customer(name):
    s = open(os.path.join(BASE, "assets", "data.js"), encoding="utf-8").read()
    custs = json.loads(s[s.find("=") + 1:].rstrip(";"))["customers"]
    return next((c for c in custs if c["name"] == name), None)


def decide_outputs(cust):
    """按诊断+版本决定要生成哪些成品"""
    tags = cust.get("diagTags") or [cust.get("diag", "")]
    is3 = "OntoZ" in (cust.get("ver") or "")
    outs = []
    if any(("客户池不足" in t) or ("准确率低" in t) for t in tags):
        outs.append("kw30" if is3 else "kw25")
    if any("邮件" in t for t in tags):
        outs.append("email")
    if not outs:
        outs.append("kw30" if is3 else "kw25")   # 默认给配词
    return outs


def build_batch_zips(names, group_by_manager=True):
    """批量生成（串行+限速+缓存），按经理分组打包 zip，逐个上传云盘。
    返回 [{manager, zip, link, count, detail}]"""
    import zipfile, datetime
    from collections import defaultdict
    os.makedirs(os.path.join(BASE, "批量打包"), exist_ok=True)
    groups = defaultdict(list)
    for nm in names:
        cust = load_customer(nm)
        if not cust:
            continue
        key = (cust.get("mgr") or "未分配") if group_by_manager else "全部客户"
        groups[key].append(cust)
    results = []
    for mgr, custs in groups.items():
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_mgr = mgr.replace("/", "").replace("\\", "")
        zpath = os.path.join(BASE, "批量打包", f"{safe_mgr}_客户优化方案_{ts}.zip")
        detail = []
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
            for cust in custs:
                produced = []
                kinds = decide_outputs(cust)
                for k in kinds:
                    try:
                        if k == "email":
                            out, _d, _l = gen_fix.gen_email(cust)
                        else:
                            out, _d, _l = gen_fix.gen_keywords(cust, "2.5" if k == "kw25" else "3.0")
                        for _f in ([out] if isinstance(out, str) else (out or [])):
                            if _f and os.path.exists(_f):
                                zf.write(_f, os.path.basename(_f))
                                produced.append(os.path.basename(_f))
                    except Exception as e:
                        detail.append({"customer": cust["name"], "ok": False, "error": f"{k}: {e}"})
                if produced:
                    detail.append({"customer": cust["name"], "ok": True, "files": produced, "kinds": kinds})
        _tok, url = upload_to_drive(zpath)
        results.append({"manager": mgr, "zip": os.path.basename(zpath), "link": url,
                        "count": len([d for d in detail if d.get("ok")]), "detail": detail})
    return results


def send_to_csm(cust, files):
    """把文件发给该客户的客户成功经理（bot 身份）"""
    mgr = cust.get("mgr") or ""
    mp = json.load(open(MAP_FILE, encoding="utf-8")) if os.path.exists(MAP_FILE) else {}
    info = mp.get(mgr)
    if not info or not info.get("open_id"):
        return False, f"未找到「{mgr}」的飞书账号"
    open_id = info["open_id"]
    results = []
    # 先发一条说明
    head = (f"【客户服务优化 · {cust['name']}】\n"
            f"系统版本：{cust.get('ver','—')}　剩余{ cust.get('daysLeft','—') }天\n"
            f"问题诊断：{cust.get('diag','')}\n"
            f"建议：{cust.get('advice','')[:80]}")
    subprocess.run([LARK, "im", "+messages-send", "--user-id", open_id, "--text", head, "--as", "bot"],
                   capture_output=True, text=True, timeout=60)
    for f in files:
        if not f or not os.path.exists(f): continue
        r = subprocess.run([LARK, "im", "+messages-send", "--user-id", open_id,
                            "--file", f, "--as", "bot"], capture_output=True, text=True, timeout=120)
        results.append(os.path.basename(f) + ("✅" if '"ok": true' in r.stdout else "❌"))
    return True, f"已发给 {mgr}：" + "、".join(results)


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=BASE, **kw)

    def log_message(self, fmt, *args):
        pass

    def _cors(self):
        if CORS_ORIGIN:
            self.send_header("Access-Control-Allow-Origin", CORS_ORIGIN)
            self.send_header("Vary", "Origin")

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.end_headers()

    def _session(self):
        cookie = self.headers.get("Cookie") or ""
        for kv in cookie.split(";"):
            kv = kv.strip()
            if kv.startswith("bi_sid="):
                return sess_get(kv[7:])
        return None

    def end_headers(self):
        try:
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
        except Exception:
            pass
        super().end_headers()

    def do_GET(self):
        ip = self.client_address[0] if self.client_address else "?"
        if not rate_ok(ip):
            self._json({"ok": False, "error": "请求过于频繁，请稍后再试"}, 429); return
        path = urlparse(self.path).path
        # ---- 访问日志（排查前端加载了什么）----
        try:
            _q = urlparse(self.path).query
            os.makedirs(os.path.join(BASE, "日志"), exist_ok=True)
            with open(os.path.join(BASE, "日志", "访问日志.log"), "a", encoding="utf-8") as _f:
                _f.write("[%s] %s %s%s ua=%s\n" % (datetime.datetime.now().strftime("%H:%M:%S"),
                          ip, path, ("?" + _q[:60]) if _q else "",
                          str(self.headers.get("User-Agent") or "")[:90]))
        except Exception:
            pass
        # ---- 口径文档已关闭（v1.5.0 安全加固）----
        if path in ("/doc", "/doc.html"):
            self._json({"ok": False, "error": "页面不存在"}, 404); return
        # ---- 页面：自动给 data.js 带上「数据文件修改时间」版本号（根治缓存，且不依赖前端技巧）----
        if path.endswith(".html") and "/" not in path.strip("/"):
            _fp = os.path.join(BASE, path.lstrip("/"))
            if os.path.exists(_fp):
                _body = open(_fp, encoding="utf-8").read()
                try:
                    _v = int(os.path.getmtime(os.path.join(BASE, "assets", "data.js")))
                except Exception:
                    _v = 0
                _body = _body.replace('<script src="assets/data.js"></script>',
                                      '<script src="assets/data.js?v=%d"></script>' % _v)
                _d = _body.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(_d)))
                self.end_headers(); self.wfile.write(_d); return
        # ---- 客户数据必须登录；未登录只给空壳（防直连爬取）----
        if path == "/assets/data.js":
            if not self._session():
                body = ('window.DASHBOARD_DATA = {"locked":true,"generatedAt":null,'
                        '"snapshotTime":null,"customers":[],"managers":[]};').encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/javascript; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers(); self.wfile.write(body); return
            try:
                _sz = os.path.getsize(os.path.join(BASE, "assets", "data.js"))
                with open(os.path.join(BASE, "日志", "访问日志.log"), "a", encoding="utf-8") as _f:
                    _f.write("[%s] %s data.js 已登录 → 返回真实数据 %d 字节\n" % (
                        datetime.datetime.now().strftime("%H:%M:%S"), ip, _sz))
            except Exception:
                pass
            return super().do_GET()
        if path == "/api/file":
            # 下载生成结果文件，带正确文件名（UTF-8）
            from urllib.parse import parse_qs, quote
            qs = parse_qs(urlparse(self.path).query)
            name = (qs.get("name") or [""])[0]
            fpath = os.path.join(BASE, "生成结果", os.path.basename(name))
            if not os.path.exists(fpath):
                self._json({"ok": False, "error": "文件不存在"}, 404); return
            data = open(fpath, "rb").read()
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Disposition",
                             "attachment; filename*=UTF-8''" + quote(os.path.basename(fpath)))
            self._cors()
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "/api/refresh/status":
            _d = {k: v for k, v in REFRESH.items() if not k.startswith("_")}
            _d["progress"] = _read_progress()
            self._json({"ok": True, **_d}); return
        if path == "/api/history":
            from urllib.parse import parse_qs
            recs = _load_archive()
            known = {p.get("zip") for r in recs for p in (r.get("packages") or []) if p.get("zip")}
            extra = [r for r in scan_archive()
                     if not any(p.get("zip") in known for p in r.get("packages", []) if p.get("zip"))]
            if extra:
                recs = sorted(recs + extra, key=lambda r: r.get("time", ""), reverse=True)
                _save_archive(recs)
            self._json({"ok": True, "records": recs[:300]}); return
        if path == "/api/download":
            from urllib.parse import parse_qs, quote
            qs = parse_qs(urlparse(self.path).query)
            rel = (qs.get("f") or [""])[0]
            norm = os.path.normpath(rel)
            parts = norm.split(os.sep)
            if len(parts) != 2 or parts[0] not in ("生成结果", "批量打包") or ".." in rel:
                self._json({"ok": False, "error": "非法路径"}, 403); return
            fpath = os.path.join(BASE, norm)
            if not os.path.exists(fpath):
                self._json({"ok": False, "error": "文件不存在"}, 404); return
            data = open(fpath, "rb").read()
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(os.path.basename(fpath)))
            self._cors()
            self.end_headers(); self.wfile.write(data); return
        # ---- 静态资源白名单：只放行页面与 assets 下的非 json 资源 ----
        if path in ("", "/", "/tianji.html", "/index.html", "/kanban.html", "/favicon.ico"):
            return super().do_GET()
        if path.startswith("/assets/") and not path.lower().endswith(".json"):
            return super().do_GET()
        self._json({"ok": False, "error": "not found"}, 404); return

    def do_POST(self):
        ip = self.client_address[0] if self.client_address else "?"
        if not rate_ok(ip):
            self._json({"ok": False, "error": "请求过于频繁，请稍后再试"}, 429); return
        path = urlparse(self.path).path
        if path not in ("/api/fix", "/api/fix_only", "/api/batch", "/api/status", "/api/send-code", "/api/login", "/api/me", "/api/logout", "/api/history/upload", "/api/history/delete", "/api/refresh", "/api/cred/add", "/api/cred/get", "/api/cred/list"):
            self._json({"ok": False, "error": "unknown endpoint"}, 404); return
        try:
            ln = int(self.headers.get("Content-Length", 0))
            if ln and int(ln) > MAX_BODY:
                self._json({"ok": False, "error": "请求体过大"}, 413); return
            req = json.loads(self.rfile.read(ln) or b"{}")
            # ---- 登录相关 ----
            if path == "/api/send-code":
                r = ts_send_code((req.get("username") or "").strip())
                self._json({"ok": r.get("code") == 0, "msg": r.get("msg", "")}); return
            if path == "/api/login":
                uname = (req.get("username") or "").strip()
                pwd = req.get("password") or ""
                _ip = self.client_address[0] if self.client_address else "?"
                rlkey = uname + "|" + _ip
                if ALLOWED_ACCOUNTS and uname not in ALLOWED_ACCOUNTS:
                    audit("DENIED", uname, _ip, "未在白名单")
                    self._json({"ok": False, "error": "该账号未开通本看板访问权限"}); return
                if login_blocked(rlkey):
                    self._json({"ok": False, "error": "尝试次数过多，请 15 分钟后再试"}); return
                # 先尝试本地账号（超管/主管，不经过天枢）
                local_info = local_accounts.verify_local(uname, pwd)
                if local_info:
                    login_ok(rlkey)
                    audit("LOGIN-OK-LOCAL", uname, _ip, "role=" + str(local_info.get("role")))
                    sid = sess_create(local_info)
                    body = json.dumps({"ok": True, "nickname": local_info["nickname"], "local": True}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Set-Cookie", sess_cookie(sid))
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers(); self.wfile.write(body); return
                # 本地账号验证失败，走天枢认证
                ok, msg, info = ts_login(uname, pwd, (req.get("smsCode") or "").strip())
                if not ok:
                    login_fail(rlkey)
                    audit("LOGIN-FAIL", uname, _ip, str(msg)[:60])
                    self._json({"ok": False, "error": msg}); return
                login_ok(rlkey)
                # 顺手用同一套账号密码刷新听言凭证（失败不影响登录）
                try:
                    _ok2, _msg2, _tytok = ty_login(uname, req.get("password") or "")
                    if _ok2 and _tytok:
                        _save_ty_token(_tytok)
                        print("[ty-login] 听言凭证已刷新", file=sys.stderr)
                    else:
                        print("[ty-login] 听言登录未成功：" + str(_msg2), file=sys.stderr)
                except Exception as _e:
                    print("[ty-login] 听言登录异常：" + str(_e), file=sys.stderr)
                audit("LOGIN-OK", uname, _ip, "nickname=" + str(info.get("nickname")))
                sid = sess_create(info)
                body = json.dumps({"ok": True, "nickname": info["nickname"]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Set-Cookie", sess_cookie(sid))
                self.send_header("Content-Length", str(len(body)))
                self.end_headers(); self.wfile.write(body); return
            if path == "/api/me":
                s = self._session()
                self._json({"ok": bool(s), "nickname": (s or {}).get("nickname"),
                            "account": (s or {}).get("username"),
                            "loginAt": (s or {}).get("loginAt")}); return
            if path == "/api/logout":
                s = self._session()
                if s: sess_del(s["sid"])
                self._json({"ok": True}); return
            # ---- 凭证留档系统（仅 admin）----
            if path == "/api/cred/add":
                s = self._session()
                if not s or not cred_store.is_admin(s.get("username", "")):
                    self._json({"ok": False, "error": "仅管理员可操作"}); return
                name = (req.get("name") or "").strip()
                username = (req.get("username") or "").strip()
                password = req.get("password") or ""
                if not name or not username or not password:
                    self._json({"ok": False, "error": "name/username/password 均必填"}); return
                cred_store.add_cred(name, username, password, req.get("note", ""))
                cred_store._audit("ADD", s["username"], name)
                self._json({"ok": True, "name": name}); return
            if path == "/api/cred/get":
                s = self._session()
                if not s or not cred_store.is_admin(s.get("username", "")):
                    self._json({"ok": False, "error": "仅管理员可操作"}); return
                name = (req.get("name") or "").strip()
                cred = cred_store.get_cred(name)
                if not cred:
                    self._json({"ok": False, "error": "未找到该凭证"}); return
                cred_store._audit("GET", s["username"], name)
                self._json({"ok": True, **cred}); return
            if path == "/api/cred/list":
                s = self._session()
                if not s or not cred_store.is_admin(s.get("username", "")):
                    self._json({"ok": False, "error": "仅管理员可操作"}); return
                cred_store._audit("LIST", s["username"])
                self._json({"ok": True, "records": cred_store.list_names()}); return
            # ---- 状态查询 ----
            if path == "/api/status":
                self._json({"ok": True, "used_today": gen_fix.daily_used(),
                            "daily_limit": gen_fix.DAILY_LIMIT,
                            "rpm_limit": gen_fix.RPM_LIMIT,
                            "min_interval": gen_fix.MIN_INTERVAL}); return
            # 批量生成 + 打包 zip
            if path == "/api/batch":
                names = req.get("customers") or []
                if not names:
                    self._json({"ok": False, "error": "未选择客户"}); return
                if [n for n in names if n not in valid_customer_names()]:
                    self._json({"ok": False, "error": "包含无效客户名（已拒绝）"}); return
                if gen_fix.daily_used() >= gen_fix.DAILY_LIMIT:
                    self._json({"ok": False, "error": f"已达今日生成上限（{gen_fix.DAILY_LIMIT}次），请明天再试"}); return
                grouped = req.get("group_by_manager", True)
                results = build_batch_zips(names, group_by_manager=grouped)
                try:
                    import datetime as _dt
                    _pkgs, _all = [], []
                    for b in results:
                        _cs = [d for d in (b.get("detail") or []) if d.get("ok")]
                        _pkgs.append({
                            "manager": b.get("manager"), "zip": b.get("zip"),
                            "link": b.get("link"), "count": b.get("count"),
                            "customers": [d["customer"] for d in _cs],
                            "details": [{"customer": d["customer"],
                                         "kinds": [KIND_LABEL.get(k, k) for k in (d.get("kinds") or [])]} for d in _cs],
                        })
                        _all += [d["customer"] for d in _cs]
                    archive_add({
                        "id": _dt.datetime.now().strftime("%Y%m%d%H%M%S") + "-" + str(len(_load_archive())),
                        "time": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "kind": "batch", "grouped": grouped,
                        "count": len(_all), "customers": _all, "packages": _pkgs,
                    })
                except Exception:
                    pass
                self._json({"ok": True, "grouped": grouped, "batches": results,
                            "used_today": gen_fix.daily_used(),
                            "daily_limit": gen_fix.DAILY_LIMIT}); return
            # ---- 手动快照：立即抓取（需登录）----
            if path == "/api/refresh":
                sess = self._session()
                if not sess:
                    self._json({"ok": False, "error": "未登录"}); return
                audit("REFRESH", sess.get("username"),
                      self.client_address[0] if self.client_address else "?", "手动触发抓取")
                self._json({"ok": True, "started": start_refresh(sess.get("token", ""))}); return
            # ---- 归档：重新上传云盘 ----
            if path == "/api/history/upload":
                rid = req.get("id"); idx = req.get("index")
                recs = _load_archive()
                rec = next((r for r in recs if r.get("id") == rid), None)
                if not rec:
                    self._json({"ok": False, "error": "记录不存在"}); return
                pkgs = rec.get("packages") or []
                if idx is None or idx < 0 or idx >= len(pkgs):
                    self._json({"ok": False, "error": "包索引无效"}); return
                zp = os.path.join(BASE, "批量打包", pkgs[idx].get("zip") or "")
                if not os.path.exists(zp):
                    self._json({"ok": False, "error": "本地包已不存在"}); return
                _tok, url = upload_to_drive(zp)
                pkgs[idx]["link"] = url
                _save_archive(recs)
                self._json({"ok": True, "link": url}); return
            # ---- 归档：删除记录 ----
            if path == "/api/history/delete":
                rid = req.get("id")
                _save_archive([r for r in _load_archive() if r.get("id") != rid])
                self._json({"ok": True}); return
            name = req.get("customer")
            if name not in valid_customer_names():
                self._json({"ok": False, "error": "无效客户名（已拒绝）"}); return
            cust = load_customer(name)
            if not cust:
                self._json({"ok": False, "error": f"未找到客户 {name}"}); return
            kinds = decide_outputs(cust)
            files = []
            links = []
            for k in kinds:
                if k == "email":
                    out, _data, log = gen_fix.gen_email(cust)
                else:
                    out, _data, log = gen_fix.gen_keywords(cust, "2.5" if k == "kw25" else "3.0")
                outs = [out] if isinstance(out, str) else (out or [])
                for _f in outs:
                    if not _f:
                        continue
                    files.append(_f)
                    if os.path.exists(_f):
                        _tok, url = upload_to_drive(_f)
                        if url:
                            links.append({"name": os.path.basename(_f), "url": url})
            sent = None
            if path == "/api/fix":
                ok, msg = send_to_csm(cust, files)
                sent = msg
            try:
                import datetime as _dt
                _um = {l.get("name"): l.get("url") for l in links}
                archive_add({
                    "id": _dt.datetime.now().strftime("%Y%m%d%H%M%S") + "-s" + str(len(_load_archive())),
                    "time": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "kind": "single", "customer": name, "manager": cust.get("mgr"),
                    "files": [{"name": os.path.basename(f), "link": _um.get(os.path.basename(f))}
                              for f in files if f],
                })
            except Exception:
                pass
            self._json({"ok": True, "customer": name, "manager": cust.get("mgr"),
                        "files": [os.path.basename(f) for f in files if f],
                        "links": links,
                        "folder": "https://iihcw7mp26x.feishu.cn/drive/folder/" + DRIVE_FOLDER,
                        "sent": sent})
        except Exception as e:
            print("[error] " + traceback.format_exc(), file=sys.stderr)
            self._json({"ok": False, "error": str(e)}, 500)


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


if __name__ == "__main__":
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"BI 看板服务已启动: http://localhost:{PORT}/index.html")
    print("接口: POST /api/fix (生成+发送给客户经理)  |  POST /api/fix_only (只生成)")
    srv.serve_forever()
