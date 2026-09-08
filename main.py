"""Entry point for Markdown Viewer."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtCore import Qt, QStandardPaths, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from app.version import VERSION

log = logging.getLogger("markdown_viewer")

# IPC server name — shared between first and second instances.
_IPC_SERVER_NAME = "MarkdownViewer-single-instance"


def _setup_logging() -> None:
    """Write a rotating log + capture unhandled exceptions for field triage."""
    base = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppDataLocation
    )
    log_dir = Path(base or ".") / "logs"
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


def _try_send_to_running_instance(path_to_send: str) -> bool:
    """Try to connect to an already-running instance and send *path_to_send*.

    Returns True if the message was delivered (caller should exit);
    False if no running instance was found (caller should become primary).
    """
    socket = QLocalSocket()
    socket.connectToServer(_IPC_SERVER_NAME)
    if not socket.waitForConnected(1000):
        return False
    # Send the absolute path (or empty string for "just raise window").
    socket.write(path_to_send.encode("utf-8"))
    if not socket.waitForBytesWritten(1000):
        # A connected server owns the session; never start another primary.
        log.warning("IPC write did not complete")
    socket.disconnectFromServer()
    return True


def _setup_ipc_server(window: MainWindow) -> QLocalServer:
    """Start a QLocalServer that listens for file paths from new instances."""

    def _on_new_connection():
        while server.hasPendingConnections():
            conn = server.nextPendingConnection()
            if conn is None:
                continue
            _receive_connection(conn)

    def _receive_connection(conn):
        # EOF frames one UTF-8 path, including an empty activation request.
        # Compatible with older clients; fragmented writes never open half a path.
        payload = bytearray()
        finished = False
        timer = QTimer(conn)
        timer.setSingleShot(True)

        def read():
            payload.extend(bytes(conn.readAll()))
            if len(payload) > 64 * 1024:
                finish(False)

        def finish(deliver=True):
            nonlocal finished
            if finished:
                return
            finished = True
            timer.stop()
            payload.extend(bytes(conn.readAll()))
            if deliver and len(payload) <= 64 * 1024:
                try:
                    path = payload.decode("utf-8").strip()
                except UnicodeDecodeError:
                    path = ""
                if path:
                    window.open_path(path)
                window.setWindowState(window.windowState() & ~Qt.WindowState.WindowMinimized)
                window.raise_()
                window.activateWindow()
            conn.close()
            conn.deleteLater()

        conn.readyRead.connect(read)
        conn.disconnected.connect(finish)
        timer.timeout.connect(lambda: finish(False))
        timer.start(5000)
        read()
        if conn.state() == QLocalSocket.LocalSocketState.UnconnectedState:
            finish()

    server = QLocalServer(window)
    # Remove any stale server left by a previous crash.
    QLocalServer.removeServer(_IPC_SERVER_NAME)
    if not server.listen(_IPC_SERVER_NAME):
        log.warning("IPC server failed to listen: %s", server.errorString())
    else:
        log.info("IPC server listening as %r", _IPC_SERVER_NAME)
    server.newConnection.connect(_on_new_connection)
    return server


def main():
    from app.url_schemes import register_document_schemes
    register_document_schemes()
    # Crisper rendering on Windows fractional display scaling (125%, 150%).
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName("Markdown Viewer")
    app.setApplicationVersion(VERSION)
    app.setOrganizationName("markdown-viewer")
    _setup_logging()
    log.info("Markdown Viewer %s starting", VERSION)

    # --- Single-instance gate -------------------------------------------
    # Resolve the file argument (if any) to an absolute path early so the
    # running instance receives a usable path regardless of CWD differences.
    file_arg = ""
    if len(sys.argv) > 1:
        file_arg = str(Path(sys.argv[1]).resolve())

    if _try_send_to_running_instance(file_arg):
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

    # A CLI file takes priority, while drafts outside the old session remain
    # discoverable through the same non-modal recovery inbox.
    session_state.restore_startup(window, file_arg)

    sys.exit(app.exec())


if __name__ == "__main__":
    # Killable Markdown render worker (see app/render_service.py). Must be
    # handled before any Qt/window work: the frozen build re-launches this same
    # executable with this flag instead of "python -m app.render_worker".
    if len(sys.argv) > 1 and sys.argv[1] == "--render-worker":
        from app.render_worker import main as _render_worker_main

        sys.exit(_render_worker_main(sys.argv[1:]))
    main()
