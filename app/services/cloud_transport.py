"""Transport to the cloud service explicitly selected on the account screen."""
from __future__ import annotations

import ipaddress
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import build_opener, HTTPRedirectHandler, Request


MAX_BODY = 65 * 1024 * 1024


def normalize_endpoint(value: str) -> str:
    hint = "请输入可信服务的 HTTPS 地址。本机测试可使用 http://127.0.0.1 加端口。"
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError(hint)
    address = value.strip().rstrip("/")
    try:
        parts = urlsplit(address)
        host, port = parts.hostname, parts.port
        if (not host or parts.username is not None or parts.password is not None or
                parts.path or parts.query or parts.fragment or any(c.isspace() or ord(c) < 32 for c in address)
                or "\\" in address or "%" in host):
            raise ValueError(hint)
        try:
            local = ipaddress.ip_address(host).is_loopback
        except ValueError:
            local = host == "localhost"
        if parts.scheme != "https" and not (local and parts.scheme == "http"):
            raise ValueError(hint)
        name = f"[{host}]" if ":" in host else host.encode("idna").decode("ascii")
        suffix = f":{port}" if port and (parts.scheme, port) not in {("https", 443), ("http", 80)} else ""
        if port == 0:
            raise ValueError(hint)
        return f"{parts.scheme}://{name}{suffix}"
    except (ValueError, UnicodeError):
        raise ValueError(hint) from None


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class CloudError(ValueError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def call(endpoint, method, route, *, payload=None, token=""):
    body = None if payload is None else json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    if body and len(body) > MAX_BODY:
        raise ValueError("这批课表超过云存储大小限制。")
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        request = Request(endpoint + "/v1/" + route, data=body, headers=headers, method=method)
        with build_opener(_NoRedirect()).open(request, timeout=30) as response:
            data = response.read(MAX_BODY + 1)
        if len(data) > MAX_BODY:
            raise ValueError("云服务返回的数据过大。")
        try:
            result = json.loads(data)
        except (ValueError, UnicodeError, RecursionError):
            result = None
        if not isinstance(result, dict):
            raise ValueError("云服务返回的数据格式有误，请确认服务地址。")
        return result
    except HTTPError as exc:
        with exc:
            code = exc.code
            messages = {401: "登录已过期，请重新登录同一账号。", 403: "当前账号无权执行此操作。",
                        404: "未找到该云批次，请刷新列表。", 409: "云端已有更新，请先打开最新版本。",
                        413: "这批课表超过云存储大小限制。", 429: "尝试次数过多，请稍后重试。"}
            message = messages.get(code, "云服务拒绝了请求，请核对地址或稍后重试。")
            if route in {"register", "login"} and code in {400, 401, 403, 409}:
                message = ("用户名或密码不正确。" if route == "login" else
                           "注册未成功，请更换用户名，密码至少 10 位并避开常见密码。")
            raise CloudError(code, message) from None
    except (OSError, URLError) as exc:
        raise ValueError("暂时连不上云服务，请检查服务地址和网络后重试。本机课表仍可继续制作。") from exc
