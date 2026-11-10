"""Entry point for Markdown Viewer."""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING

if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "--agent-watchdog":
    # Run the bundled Agent Watchdog with this interpreter (a packaged build
    # has no python.exe). Standard library only: no Qt, no QApplication, no
    # single-instance IPC, no application settings.
    from app.agent_bundle import run_bundled as _run_bundled_watchdog

    raise SystemExit(_run_bundled_watchdog(sys.argv[2:]))
elif (
    __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "--agent-watchdog-stop"
):
    # Run by the installer before an upgrade / uninstall: ask the bundled
    # Agent Watchdog started by this application to exit. Headless as above.
    from app.agent_stop import main as _stop_bundled_watchdog

    raise SystemExit(_stop_bundled_watchdog())
elif __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "--packaged-benchmark":
    # Establish isolated settings before application modules capture QSettings.
    from tools.benchmark_file_switching import main as _benchmark_main

    del sys.argv[1]
    _benchmark_main()
    raise SystemExit(0)
elif (
    __name__ == "__main__"
    and len(sys.argv) > 1
    and sys.argv[1].startswith("--")
    and sys.argv[1] != "--render-worker"
):
    # Headless CLI (--export, --export-batch, --capabilities, --version) and
    # every unknown --option (exit 2). Handled before any Qt import and before
    # the single-instance IPC gate: never opens a window, never forwards.
    from app.cli_export import main as _cli_main

    raise SystemExit(_cli_main(sys.argv[1:]))
else:
    from PySide6.QtCore import QStandardPaths, Qt, QTimer
    from PySide6.QtGui import QIcon
    from PySide6.QtNetwork import QAbstractSocket, QLocalServer, QLocalSocket
    from PySide6.QtWidgets import QApplication

    from app.file_types import document_kind
    from app.settings_store import APP, DISPLAY_NAME, ORG, legacy_data_path
    from app.url_protocol import (
        MAX_HEADING_LENGTH,
        is_protocol_url,
        parse_open_target,
        parse_open_url,
    )
    from app.version import VERSION

if TYPE_CHECKING:
    from app.window import MainWindow

log = logging.getLogger("markdown_viewer")

# IPC server name — shared between first and second instances.
_IPC_SERVER_NAME = "MarkdownViewer-single-instance"
# JSON request/reply protocol version spoken on that socket (see handle_ipc_request).
IPC_PROTOCOL_VERSION = 1


def _setup_logging() -> None:
    """Write a rotating log + capture unhandled exceptions for field triage."""
    base = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppDataLocation
    )
    log_dir = legacy_data_path(base) / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            log_dir / "markdown-viewer.log",
            maxBytes=512 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
    except OSError:
        handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)

    def _excepthook(exc_type, exc_value, exc_tb):
        logging.getLogger("markdown_viewer").critical(
            "Unhandled exception", exc_info=(exc_type, exc_value, exc_tb)
        )
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _excepthook


# 圖示路徑：打包後從 _MEIPASS 找，開發時從專案目錄找
def _find_icon() -> QIcon:
    candidates = [
        Path(__file__).parent / "ICON" / "icon.ico",
    ]
    if hasattr(sys, "_MEIPASS"):
        candidates.insert(0, Path(sys._MEIPASS) / "ICON" / "icon.ico")
    for p in candidates:
        if p.exists():
            return QIcon(str(p))
    return QIcon()


_IPC_MAX_BYTES = 64 * 1024
_IPC_REPLY_TIMEOUT_MS = 3000


def _ipc_payload(path_to_send: str, anchor: dict | None) -> bytes:
    """Bare UTF-8 path (legacy, compatible with every version) unless anchored."""
    if not anchor:
        return path_to_send.encode("utf-8")  # "" = just raise the window
    request = {"v": IPC_PROTOCOL_VERSION, "cmd": "open", "path": path_to_send,
               "anchor": anchor}
    return json.dumps(request, ensure_ascii=False).encode("utf-8")


def _try_send_to_running_instance(path_to_send: str, anchor: dict | None = None) -> bool:
    """Try to connect to an already-running instance and send *path_to_send*.

    Without an anchor the legacy bare path is sent (no reply awaited). With one,
    IPC protocol v1 JSON is sent and we wait up to 3 s for ``{ok}``; an older
    server never answers, in which case we just give up waiting.

    Returns True if a server owns the session (caller should exit);
    False if no running instance was found (caller should become primary).
    """
    socket = QLocalSocket()
    socket.connectToServer(_IPC_SERVER_NAME)
    if not socket.waitForConnected(1000):
        return False
    socket.write(_ipc_payload(path_to_send, anchor))
    if not socket.waitForBytesWritten(1000):
        # A connected server owns the session; never start another primary.
        log.warning("IPC write did not complete")
    elif anchor:  # only v1 JSON gets a reply; a bare path is framed by EOF
        reply = _read_ipc_reply(socket, _IPC_REPLY_TIMEOUT_MS)
        if reply is None:
            log.info("IPC server sent no reply (older version or busy)")
        elif not reply.get("ok"):
            log.warning("IPC server refused request: %s", reply.get("error"))
    socket.disconnectFromServer()
    return True


def _read_ipc_reply(socket: QLocalSocket, timeout_ms: int) -> dict | None:
    """Wait (bounded) for one JSON reply object; None on timeout/EOF/garbage."""
    deadline = time.monotonic() + timeout_ms / 1000
    buffer = b""
    while True:
        buffer += bytes(socket.readAll())
        if len(buffer) > _IPC_MAX_BYTES:
            return None
        if buffer:
            try:
                value = json.loads(buffer.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                value = None
            if isinstance(value, dict):
                return value
        remaining = int((deadline - time.monotonic()) * 1000)
        if remaining <= 0:
            return None
        if not socket.waitForReadyRead(remaining):
            buffer += bytes(socket.readAll())
            try:
                value = json.loads(buffer.decode("utf-8")) if buffer else None
            except (ValueError, UnicodeDecodeError):
                value = None
            return value if isinstance(value, dict) else None


def resolve_launch_request(arg: str) -> tuple[str, dict | None] | None:
    """Like resolve_launch_arg but also returns the URL's anchor (or None)."""
    if arg and is_protocol_url(arg):
        target = parse_open_target(arg)
        path = _url_to_local_path(arg)
        if target is None or path is None:
            return None
        return path, target[1]
    resolved = resolve_launch_arg(arg)
    return None if resolved is None else (resolved, None)


def resolve_launch_arg(arg: str) -> str | None:
    """Turn a command-line argument into an absolute local path.

    Returns "" for no argument, None for an invalid markdownviewer:// URL
    (nothing must be opened), otherwise the resolved path.
    """
    if not arg:
        return ""
    if is_protocol_url(arg):
        return _url_to_local_path(arg)
    return str(Path(arg).resolve())


def _url_to_local_path(url: str) -> str | None:
    """Validated local file for a markdownviewer:// URL (never a folder)."""
    target = parse_open_url(url)
    if target is None:
        log.warning("Rejected invalid markdownviewer URL: %r", url[:200])
        return None
    resolved = Path(target).resolve()
    if resolved.is_dir():
        log.warning("Rejected markdownviewer URL pointing at a folder")
        return None
    return str(resolved)


def handle_forwarded_path(window: MainWindow, path: str) -> None:
    """Show/raise the (possibly hidden) main window, then open file or folder."""
    anchor = None
    if path and is_protocol_url(path):
        target = parse_open_target(path)
        anchor = target[1] if target else None
        path = _url_to_local_path(path) or ""
    if anchor:
        window.open_external_path(path, anchor)
    else:
        window.open_external_path(path)


def _valid_ipc_anchor(anchor) -> bool:
    if anchor is None:
        return True
    if not isinstance(anchor, dict):
        return False
    heading, line = anchor.get("heading"), anchor.get("line")
    if heading is not None:
        return (isinstance(heading, str) and 0 < len(heading.strip()) <= MAX_HEADING_LENGTH
                and not any(ord(c) < 32 or ord(c) == 127 for c in heading))
    if line is not None:
        return isinstance(line, int) and not isinstance(line, bool) and 1 <= line <= 10**9
    return True  # empty anchor object: nothing to scroll to


def handle_ipc_request(window: MainWindow, request) -> dict:
    """Execute one parsed v1 request; always returns the JSON reply dict."""
    if not isinstance(request, dict):
        return {"ok": False, "error": "invalid_request"}
    version = request.get("v")
    if isinstance(version, bool) or version != IPC_PROTOCOL_VERSION:
        return {"ok": False, "error": "unsupported_version"}
    cmd = request.get("cmd")
    if cmd == "ping":
        return {"ok": True, "version": VERSION}
    if cmd != "open":
        return {"ok": False, "error": "unknown_cmd"}
    path = request.get("path", "")
    if not isinstance(path, str) or "\x00" in path:
        return {"ok": False, "error": "invalid_path"}
    anchor = request.get("anchor")
    if not _valid_ipc_anchor(anchor):
        return {"ok": False, "error": "invalid_anchor"}
    try:
        if not path.strip():
            window.open_external_path("")
            return {"ok": True}
        path = path.strip()
        if not os.path.isabs(path) or is_protocol_url(path):
            return {"ok": False, "error": "path_not_absolute"}
        target = Path(path)
        if not target.exists():
            return {"ok": False, "error": "not_found"}
        if target.is_dir():
            window.open_external_path(str(target))  # anchor is meaningless here
            return {"ok": True}
        if not document_kind(target):
            return {"ok": False, "error": "unsupported_type"}
        window.open_external_path(str(target), anchor or None)
    except Exception:
        log.exception("IPC open failed")
        return {"ok": False, "error": "internal_error"}
    return {"ok": True}


def _ipc_server_is_alive() -> bool:
    probe = QLocalSocket()
    probe.connectToServer(_IPC_SERVER_NAME)
    alive = probe.waitForConnected(300)
    probe.abort()
    return alive


def _setup_ipc_server(window: MainWindow) -> QLocalServer | None:
    """Start a QLocalServer that listens for file paths from new instances.

    Returns None when another live instance already owns the name; the caller
    must then forward to it instead of becoming a second primary.
    """

    def _on_new_connection():
        while server.hasPendingConnections():
            conn = server.nextPendingConnection()
            if conn is None:
                continue
            _receive_connection(conn)

    def _receive_connection(conn):
        # v1 clients send one complete JSON object and wait for a JSON reply, so
        # a payload that parses as an object is executed at once (the client
        # cannot half-close). Anything else is a legacy bare path framed by EOF:
        # compatible with older clients; fragmented writes never open half a path.
        payload = bytearray()
        finished = False
        timer = QTimer(conn)
        timer.setSingleShot(True)

        def jsonish():
            try:
                return payload.decode("utf-8").lstrip().startswith("{")
            except UnicodeDecodeError:
                return False

        def reply_and_close(obj):
            nonlocal finished
            finished = True
            timer.stop()
            try:
                conn.write(json.dumps(obj, ensure_ascii=False).encode("utf-8"))
                conn.waitForBytesWritten(500)
            except RuntimeError:
                pass
            conn.disconnectFromServer()
            conn.deleteLater()

        def read():
            if finished:
                return
            payload.extend(bytes(conn.readAll()))
            if len(payload) > _IPC_MAX_BYTES:
                if jsonish():
                    reply_and_close({"ok": False, "error": "too_large"})
                else:
                    finish(False)
                return
            process()

        def process():
            if not jsonish():
                return  # legacy path: wait for EOF
            text = payload.decode("utf-8").strip()
            try:
                request = json.loads(text)
            except ValueError:
                if text.endswith("}"):  # complete but malformed
                    reply_and_close({"ok": False, "error": "invalid_json"})
                return  # probably still arriving
            reply_and_close(handle_ipc_request(window, request))

        def finish(deliver=True):
            nonlocal finished
            if finished:
                return
            payload.extend(bytes(conn.readAll()))
            if len(payload) <= _IPC_MAX_BYTES:
                process()
                if finished:
                    return
            if jsonish():
                # Unparsed JSON at EOF/timeout/overflow: never run it as a path.
                reply_and_close({"ok": False, "error": "invalid_json"})
                return
            finished = True
            timer.stop()
            if deliver and len(payload) <= _IPC_MAX_BYTES:
                try:
                    path = payload.decode("utf-8").strip()
                except UnicodeDecodeError:
                    path = ""
                handle_forwarded_path(window, path)
            conn.close()
            conn.deleteLater()

        conn.readyRead.connect(read)
        conn.disconnected.connect(finish)
        timer.timeout.connect(lambda: finish(False))
        timer.start(5000)
        read()
        if conn.state() == QLocalSocket.LocalSocketState.UnconnectedState:
            finish()

    # Never remove a live server's name (two instances started at once).
    if _ipc_server_is_alive():
        return None
    server = QLocalServer(window)
    # Nobody answered: any leftover name is stale from a crash.
    QLocalServer.removeServer(_IPC_SERVER_NAME)
    if not server.listen(_IPC_SERVER_NAME):
        if server.serverError() == QAbstractSocket.SocketError.AddressInUseError:
            return None  # lost a startup race to another instance
        log.warning("IPC server failed to listen: %s", server.errorString())
    else:
        log.info("IPC server listening as %r", _IPC_SERVER_NAME)
    server.newConnection.connect(_on_new_connection)
    return server


def _owns_ipc(server) -> bool:
    """True only when *server* really listens: the single-instance name is ours.

    A listen() that failed for a reason other than AddressInUse leaves a dead
    server object behind; that instance may open normally but must not run the
    watchdog poller (a second primary would double every toast).
    """
    return server is not None and server.isListening()


def main():
    from app.url_schemes import register_document_schemes
    register_document_schemes()
    # Crisper rendering on Windows fractional display scaling (125%, 150%).
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName(APP)
    app.setApplicationDisplayName(DISPLAY_NAME)
    app.setApplicationVersion(VERSION)
    app.setOrganizationName(ORG)
    _setup_logging()
    log.info("Markdown Viewer %s starting", VERSION)

    # --- Single-instance gate -------------------------------------------
    # Resolve the file argument (if any) to an absolute path early so the
    # running instance receives a usable path regardless of CWD differences.
    request = resolve_launch_request(sys.argv[1]) if len(sys.argv) > 1 else ("", None)
    if request is None:
        sys.exit(0)  # invalid markdownviewer:// URL: open nothing
    file_arg, launch_anchor = request

    if _try_send_to_running_instance(file_arg, launch_anchor):
        log.info("Forwarded to running instance, exiting.")
        sys.exit(0)
    # --------------------------------------------------------------------

    app.setWindowIcon(_find_icon())

    from app import session_state
    from app.window import MainWindow

    window = MainWindow()
    window.setWindowIcon(_find_icon())
    window.show()

    # Start the IPC server *after* the window exists so incoming paths can
    # be routed to it immediately.  Keep a reference so it is not GC'd.
    _ipc_server = _setup_ipc_server(window)  # noqa: F841
    if _ipc_server is None:
        # Another instance won the startup race: hand over and exit.
        if _try_send_to_running_instance(file_arg, launch_anchor):
            log.info("Lost IPC startup race; forwarded to running instance.")
            sys.exit(0)
        log.warning("IPC name busy but no instance answered; continuing")

    # Only the primary instance watches AI agents / owns the tray icon: it
    # must come after the IPC server owns the single-instance name.
    if _owns_ipc(_ipc_server):
        window.start_agent_watchdog()
    elif _ipc_server is not None:
        log.warning("IPC server is not listening; Agent watchdog not started")

    # Restore the workspace even on a file-manager launch; its requested file
    # takes focus without replacing the other tabs. Draft recovery stays lazy.
    session_state.restore_startup(window, file_arg)
    if file_arg and launch_anchor:
        window.open_external_path(file_arg, launch_anchor)  # cold start: scroll to anchor

    sys.exit(app.exec())


if __name__ == "__main__":
    # Killable Markdown render worker (see app/render_service.py). Must be
    # handled before any Qt/window work: the frozen build re-launches this same
    # executable with this flag instead of "python -m app.render_worker".
    if len(sys.argv) > 1 and sys.argv[1] == "--render-worker":
        from app.render_worker import main as _render_worker_main

        sys.exit(_render_worker_main(sys.argv[1:]))
    main()
