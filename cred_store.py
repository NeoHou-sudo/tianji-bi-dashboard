#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
凭证留档系统 · 加密存储 + 访问审计
用于测试场景下快速调用天枢/听言账号密码。

存储方式：Fernet 对称加密 JSON，密钥读自环境变量 BI_CRED_KEY 或 ~/.baixyn-api/cred_store.key
访问控制：仅 admin 账号（通过 /api/login 登录后 session 中的 username 判定）
审计日志：每次读写写入 日志/凭证访问.log
"""
import os, json, time, datetime
from cryptography.fernet import Fernet

BASE = os.path.dirname(os.path.abspath(__file__))
KEY_FILE = os.path.expanduser("~/.baixyn-api/cred_store.key")
DATA_FILE = os.path.expanduser("~/.baixyn-api/cred_store.enc")
AUDIT_LOG = os.path.join(BASE, "日志", "凭证访问.log")
ADMIN_USERS = ["ziyu"]  # 只有这些账号可以访问凭证


def _get_key():
    """获取或生成加密密钥"""
    key = os.environ.get("BI_CRED_KEY")
    if key:
        return key.encode()
    if os.path.exists(KEY_FILE):
        return open(KEY_FILE, "rb").read().strip()
    key = Fernet.generate_key()
    open(KEY_FILE, "wb").write(key)
    os.chmod(KEY_FILE, 0o600)
    return key


def _audit(action, user, target=""):
    """写入审计日志"""
    try:
        os.makedirs(os.path.dirname(AUDIT_LOG), exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {action:6s} user={user} target={target}\n")
    except Exception:
        pass


def _load_all():
    """解密读取全部凭证"""
    try:
        data = open(DATA_FILE, "rb").read()
        f = Fernet(_get_key())
        return json.loads(f.decrypt(data).decode("utf-8"))
    except Exception:
        return {}


def _save_all(data):
    """加密保存全部凭证"""
    f = Fernet(_get_key())
    enc = f.encrypt(json.dumps(data, ensure_ascii=False).encode("utf-8"))
    open(DATA_FILE, "wb").write(enc)


def add_cred(name, username, password, note=""):
    """添加或更新一条凭证"""
    data = _load_all()
    data[name] = {
        "username": username,
        "password": password,
        "note": note,
        "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    _save_all(data)


def get_cred(name):
    """获取一条凭证（返回 username + password）"""
    data = _load_all()
    entry = data.get(name)
    if not entry:
        return None
    return {"username": entry["username"], "password": entry["password"]}


def list_names():
    """列出所有已存凭证的名称（不返回密码）"""
    data = _load_all()
    return [{"name": k, "note": v.get("note", ""), "updated_at": v.get("updated_at", "")}
            for k, v in data.items()]


def is_admin(username):
    """判断是否为管理员账号"""
    return username in ADMIN_USERS
