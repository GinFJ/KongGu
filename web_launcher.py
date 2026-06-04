"""Launch the Konggu local Web UI."""

from __future__ import annotations

import socket
import sys
import threading
import webbrowser


HOST = "127.0.0.1"
PORT = 8000
URL = f"http://{HOST}:{PORT}"


def _port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) != 0


def main() -> int:
    if not _port_available(HOST, PORT):
        print(f"端口 {PORT} 已被占用，无法启动 Konggu Web 版。")
        print(f"请关闭正在使用 {URL} 的程序后重试。")
        return 1

    try:
        import uvicorn
    except ModuleNotFoundError:
        print("缺少 uvicorn。请先运行：pip install -r requirements.txt")
        return 1

    threading.Timer(1.0, lambda: webbrowser.open(URL)).start()
    print(f"Konggu Web 正在启动：{URL}")
    uvicorn.run("app.web.main:app", host=HOST, port=PORT, reload=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())

