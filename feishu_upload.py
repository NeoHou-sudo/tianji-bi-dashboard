#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
飞书云空间上传（OpenAPI 直连，替代 lark-cli）
======================================================================
只用 Python 标准库，跨平台（Linux 服务器上也能跑）。

能力：
  - 获取 tenant_access_token（应用凭证换发，自动缓存 2 小时）
  - 上传文件到指定云空间文件夹（≤20MB 走 upload_all；大文件自动走分片）
  - 把文件设为「组织内可读」并返回分享链接

前置（在飞书开放平台给你的自建应用配置）：
  1) 开通权限：drive:drive（云空间读写）
  2) 把目标文件夹「客户优化方案」共享给该应用（添加应用为协作者，可编辑）
     否则上传会报 “no permission / 无权限”
环境变量：
  FEISHU_APP_ID      应用 App ID（cli_xxx）
  FEISHU_APP_SECRET  应用 App Secret
  FEISHU_SHARE_HOST  分享链接域名，默认 iihcw7mp26x.feishu.cn
  FEISHU_OPEN_BASE   OpenAPI 域名，默认 https://open.feishu.cn

命令行自测：
  python3 feishu_upload.py check                 # 只看凭证/取 token 是否正常
  python3 feishu_upload.py upload <文件> [文件夹token]
"""
import os, sys, json, uuid, time, mimetypes, binascii
import urllib.request, urllib.error

OPEN_BASE = os.environ.get("FEISHU_OPEN_BASE", "https://open.feishu.cn").rstrip("/")
SHARE_HOST = os.environ.get("FEISHU_SHARE_HOST", "iihcw7mp26x.feishu.cn")
APP_ID = os.environ.get("FEISHU_APP_ID", "")
APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
BLOCK_LIMIT = 20 * 1024 * 1024          # upload_all 单文件上限 20MB

_tok_cache = {"v": None, "exp": 0}


# ------------------------------------------------------------------ HTTP
def _request(url, data=None, headers=None, method=None, timeout=120):
    h = dict(headers or {})
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, str(e)


def _json(url, obj=None, headers=None, method=None, timeout=120):
    h = {"Content-Type": "application/json; charset=utf-8"}
    if headers:
        h.update(headers)
    body = json.dumps(obj).encode() if obj is not None else None
    code, text = _request(url, body, h, method, timeout)
    try:
        return code, json.loads(text)
    except Exception:
        return code, {"_raw": text}


# ------------------------------------------------------------- 鉴权
def tenant_token(force=False):
    """获取并缓存 tenant_access_token"""
    if not force and _tok_cache["v"] and _tok_cache["exp"] > time.time() + 60:
        return _tok_cache["v"]
    if not APP_ID or not APP_SECRET:
        raise RuntimeError("未配置 FEISHU_APP_ID / FEISHU_APP_SECRET")
    code, d = _json(OPEN_BASE + "/open-apis/auth/v3/tenant_access_token/internal",
                    {"app_id": APP_ID, "app_secret": APP_SECRET})
    if d.get("code") != 0 or not d.get("tenant_access_token"):
        raise RuntimeError(f"获取 tenant_access_token 失败：code={d.get('code')} msg={d.get('msg')}")
    _tok_cache["v"] = d["tenant_access_token"]
    _tok_cache["exp"] = time.time() + int(d.get("expire", 7200))
    return _tok_cache["v"]


# ------------------------------------------------------------- multipart
def _multipart(fields, file_field, filename, data, mime="application/octet-stream"):
    b = "----tianji" + uuid.uuid4().hex
    out = bytearray()
    for k, v in fields.items():
        out += f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    out += (f"--{b}\r\nContent-Disposition: form-data; name=\"{file_field}\"; "
            f"filename=\"{filename}\"\r\nContent-Type: {mime}\r\n\r\n").encode()
    out += data + b"\r\n"
    out += f"--{b}--\r\n".encode()
    return b, bytes(out)


def _auth(tok):
    return {"Authorization": "Bearer " + tok}


def _mime(path):
    return mimetypes.guess_type(path)[0] or "application/octet-stream"


# ------------------------------------------------------------- 上传
def upload_all(tok, path, folder_token):
    """小文件（≤20MB）直传，返回 file_token"""
    name = os.path.basename(path)
    data = open(path, "rb").read()
    boundary, body = _multipart(
        {"file_name": name, "parent_type": "explorer", "parent_node": folder_token,
         "size": str(len(data))},
        "file", name, data, _mime(path))
    headers = _auth(tok)
    headers["Content-Type"] = "multipart/form-data; boundary=" + boundary
    code, text = _request(OPEN_BASE + "/open-apis/drive/v1/files/upload_all",
                          body, headers, "POST", 300)
    try:
        d = json.loads(text)
    except Exception:
        raise RuntimeError(f"upload_all 失败：HTTP {code} {text[:200]}")
    if d.get("code") != 0:
        raise RuntimeError(f"upload_all 失败：code={d.get('code')} msg={d.get('msg')}")
    return d["data"]["file_token"]


def upload_chunked(tok, path, folder_token):
    """大文件分片上传（>20MB）"""
    name = os.path.basename(path)
    size = os.path.getsize(path)
    code, d = _json(OPEN_BASE + "/open-apis/drive/v1/files/upload_prepare",
                    {"file_name": name, "parent_type": "explorer",
                     "parent_node": folder_token, "size": size}, _auth(tok))
    if d.get("code") != 0:
        raise RuntimeError(f"upload_prepare 失败：{d}")
    up_id = d["data"]["upload_id"]
    block_size = d["data"]["block_size"]
    block_num = d["data"]["block_num"]
    with open(path, "rb") as f:
        for seq in range(block_num):
            chunk = f.read(block_size)
            boundary, body = _multipart(
                {"upload_id": up_id, "seq": str(seq), "size": str(len(chunk))},
                "file", name, chunk, "application/octet-stream")
            headers = _auth(tok)
            headers["Content-Type"] = "multipart/form-data; boundary=" + boundary
            code, text = _request(OPEN_BASE + "/open-apis/drive/v1/files/upload_part",
                                  body, headers, "POST", 300)
            try:
                pd = json.loads(text)
            except Exception:
                raise RuntimeError(f"upload_part[{seq}] 失败：HTTP {code} {text[:200]}")
            if pd.get("code") != 0:
                raise RuntimeError(f"upload_part[{seq}] 失败：{pd}")
    code, d = _json(OPEN_BASE + "/open-apis/drive/v1/files/upload_finish",
                    {"upload_id": up_id, "block_num": block_num}, _auth(tok))
    if d.get("code") != 0:
        raise RuntimeError(f"upload_finish 失败：{d}")
    return d["data"]["file_token"]


def set_tenant_readable(tok, file_token):
    """设为组织内可读（失败不致命，仅告警）"""
    code, d = _json(OPEN_BASE + f"/open-apis/drive/v1/permissions/{file_token}/public",
                    {"link_share_entity": "tenant_readable",
                     "external_access_entity": "open",
                     "comment_entity": "anyone_can_view",
                     "share_entity": "anyone"},
                    _auth(tok), "PATCH")
    if d.get("code") != 0:
        print(f"[feishu] 设置组织内可读失败（不影响上传）：{d.get('msg')}", file=sys.stderr)
        return False
    return True


# ------------------------------------------------------------- 对外主函数
def upload_to_folder(path, folder_token):
    """上传文件到云空间文件夹，返回 (file_token, url)"""
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    tok = tenant_token()
    size = os.path.getsize(path)
    file_token = upload_chunked(tok, path, folder_token) if size > BLOCK_LIMIT \
        else upload_all(tok, path, folder_token)
    try:
        set_tenant_readable(tok, file_token)
    except Exception as e:
        print(f"[feishu] 权限设置异常（不影响上传）：{e}", file=sys.stderr)
    return file_token, f"https://{SHARE_HOST}/file/{file_token}"


# ------------------------------------------------------------- CLI
def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "check":
        if not APP_ID or not APP_SECRET:
            print("❌ 未配置 FEISHU_APP_ID / FEISHU_APP_SECRET")
            return
        try:
            t = tenant_token()
            print(f"✅ 凭证可用，tenant_access_token 获取成功（长度 {len(t)}）")
        except Exception as e:
            print("❌ " + str(e))
    elif cmd == "upload":
        if len(sys.argv) < 3:
            print("用法：python3 feishu_upload.py upload <文件> [文件夹token]")
            return
        folder = sys.argv[3] if len(sys.argv) > 3 else os.environ.get("DRIVE_FOLDER", "")
        try:
            ft, url = upload_to_folder(sys.argv[2], folder)
            print(f"✅ 上传成功\nfile_token: {ft}\nurl: {url}")
        except Exception as e:
            print("❌ 上传失败：" + str(e))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
