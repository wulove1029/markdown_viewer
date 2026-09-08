"""Child process that renders Markdown for :mod:`app.render_service`.

Runs as ``python -m app.render_worker <port> <token-hex>`` from source and as
``MarkdownViewer.exe --render-worker <port> <token-hex>`` when frozen (see
``main.py``).  Imports no Qt: the whole point is a small, killable process
holding only ``markdown-it`` and Pygments.
"""

from __future__ import annotations

import os
import socket
import sys
from pathlib import Path


def _serve_connection(sock: socket.socket) -> None:
    from .md_converter import RenderCancelled, read_text, render_body
    from .render_service import recv_frame, send_frame

    while True:
        sock.settimeout(None)
        try:
            request = recv_frame(sock, None, float("inf"))
        except Exception:
            return  # parent went away or killed us mid-request
        if not isinstance(request, dict):
            return
        op = request.get("op")
        if op == "shutdown":
            return
        try:
            if op == "render_file":
                path = request.get("path")
                try:
                    result = read_text(Path(path))
                except OSError:
                    # The parent stats the file before asking, so this means it
                    # vanished or turned unreadable in between: a normal error
                    # page, not a worker crash.
                    send_frame(sock, {"ok": False,
                                      "error": f"無法讀取檔案：{Path(path).name}"})
                    continue
                if result is None:
                    send_frame(sock, {"ok": False, "error":
                                      f"無法讀取檔案編碼，請使用 UTF-8、Big5 或 GBK：{Path(path).name}"})
                    continue
                body = render_body(result[0])
            elif op == "render_text":
                body = render_body(request.get("text") or "")
            elif op == "ping":
                send_frame(sock, {"ok": True, "body": None})
                continue
            else:
                send_frame(sock, {"ok": False, "error": f"unknown op: {op!r}"})
                continue
        except RenderCancelled:
            send_frame(sock, {"ok": False, "cancelled": True})
            continue
        except MemoryError as exc:
            send_frame(sock, {"ok": False, "crash": True, "error": f"MemoryError: {exc}"})
            return
        except Exception as exc:  # keep the parent responsive on any parse bug
            send_frame(sock, {"ok": False, "crash": True,
                              "error": f"{type(exc).__name__}: {exc}"})
            continue
        send_frame(sock, {"ok": True, "body": body})


def serve(port: int, token_hex: str) -> int:
    token = bytes.fromhex(token_hex)
    sock = socket.create_connection(("127.0.0.1", int(port)), timeout=30)
    try:
        sock.sendall(token)
        _serve_connection(sock)
    finally:
        try:
            sock.close()
        except OSError:
            pass
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--render-worker":
        argv = argv[1:]
    if len(argv) < 2:
        print("usage: app.render_worker <port> <token-hex>", file=sys.stderr)
        return 2
    # Belt and braces: a worker must never spawn its own worker.
    os.environ["MDV_DISABLE_RENDER_SUBPROCESS"] = "1"
    return serve(int(argv[0]), argv[1])


if __name__ == "__main__":  # pragma: no cover - process entry point
    if __package__ in (None, ""):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        __package__ = "app"
    sys.exit(main())
