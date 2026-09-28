#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
天玑本地账号系统 · 超管/主管账号管理
用于在天枢之外创建独立的看板内部账号，不经过天枢认证。
"""
import hashlib, json, os, time, uuid

BASE = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE, "local_accounts.json")

# 超管账号（拥有凭证留档权限）
SUPER_ADMINS = {"neo"}

# 主管账号（看全部数据，无凭证权限）
SUPERVISORS = {"ziyu", "lily", "李莉"}

def _load():
    if not os.path.exists(DB_FILE):
        return {}
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save(data):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def _hash(password):
    return hashlib.sha256(password.encode("utf-8")).hexdigest()

def init_defaults():
    """初始化默认账号：超管 neo"""
    data = _load()
    if "neo" not in data:
        data["neo"] = {
            "password": _hash("hou59215921"),
            "role": "super_admin",
            "nickname": "neo",
            "createdAt": time.strftime("%Y-%m-%d %H:%M:%S"),
            "local": True
        }
        _save(data)
    return data

def verify_local(username, password):
    """验证本地账号，返回 user_info 或 None"""
    data = _load()
    user = data.get(username)
    if not user:
        return None
    if _hash(password) != user.get("password"):
        return None
    return {
        "username": username,
        "nickname": user.get("nickname") or username,
        "token": "local-" + uuid.uuid4().hex,
        "local": True,
        "role": user.get("role", "supervisor"),
    }

def is_super_admin(username):
    data = _load()
    user = data.get(username)
    return user and user.get("role") == "super_admin"

def is_supervisor(username):
    data = _load()
    user = data.get(username)
    return user and user.get("role") in ("super_admin", "supervisor")

def add_account(username, password, role="supervisor", nickname=None):
    """新增本地账号"""
    data = _load()
    data[username] = {
        "password": _hash(password),
        "role": role,
        "nickname": nickname or username,
        "createdAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "local": True
    }
    _save(data)
    return True

def change_password(username, new_password):
    data = _load()
    user = data.get(username)
    if not user:
        return False
    user["password"] = _hash(new_password)
    _save(data)
    return True

def change_role(username, new_role):
    data = _load()
    user = data.get(username)
    if not user:
        return False
    user["role"] = new_role
    _save(data)
    return True

def list_accounts():
    data = _load()
    result = []
    for name, info in data.items():
        result.append({
            "username": name,
            "nickname": info.get("nickname", name),
            "role": info.get("role", "supervisor"),
            "local": True,
            "createdAt": info.get("createdAt", "")
        })
    return result

# 初始化默认账号
init_defaults()
