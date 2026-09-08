"""Child process that renders Markdown for :mod:`app.render_service`.

Runs as ``python -m app.render_worker <port>`` from source and as
``MarkdownViewer.exe --render-worker <port>`` when frozen (see
``main.py``).  The 32-byte handshake token is read from stdin, never from
argv, because any local process can read another process's command line.

Imports no Qt of its own -- the point is a small, killable process holding
only ``markdown-it`` and Pygments.  (In a frozen build the executable is
``main.py``, whose module-level imports do pull in Qt before this entry point
runs, so the frozen child is heavier than the source-mode one.)
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


def read_token(size: int = 32) -> bytes:
    """Read the shared secret from stdin (never from argv, which is public).

    Frozen windowed builds can leave ``sys.stdin`` as ``None`` even though the
    parent gave us a real pipe, so fall back to file descriptor 0.
    """
    stream = getattr(sys.stdin, "buffer", None)
    if stream is None:
        stream = os.fdopen(0, "rb", closefd=False)
    data = b""
    while len(data) < size:
        chunk = stream.read(size - len(data))
        if not chunk:
            break
        data += chunk
    return data


def serve(port: int, token: bytes | None = None) -> int:
    if token is None:
        token = read_token()
    if len(token) != 32:
        print("render worker: missing stdin token", file=sys.stderr)
        return 2
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
    if not argv:
        print("usage: app.render_worker <port>  (token arrives on stdin)",
              file=sys.stderr)
        return 2
    # Belt and braces: a worker must never spawn its own worker.
    os.environ["MDV_DISABLE_RENDER_SUBPROCESS"] = "1"
    return serve(int(argv[0]))


if __name__ == "__main__":  # pragma: no cover - process entry point
    if __package__ in (None, ""):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        __package__ = "app"
    sys.exit(main())
