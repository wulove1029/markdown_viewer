"""Exercise the packaged second-instance path without opening a user session."""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QCoreApplication, QProcess, QTimer
from PySide6.QtNetwork import QLocalServer

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("exe", type=Path)
parser.add_argument("--output", type=Path, default=Path("tmp/packaged-smoke.json"))
args = parser.parse_args()
args.output.parent.mkdir(parents=True, exist_ok=True)

app = QCoreApplication([])
# Windows named pipes permit multiple listening instances of one name. A
# successful listen alone does NOT prove that the user's application is absent.
if sys.platform == "win32":
    running = subprocess.check_output(
        ["tasklist", "/FI", "IMAGENAME eq MarkdownViewer.exe", "/FO", "CSV", "/NH"])
    if b"markdownviewer.exe" in running.lower():
        result = {"status": "skipped", "reason": "existing MarkdownViewer process; left running",
                  "scope": "packaged forwarding not verified"}
        args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result))
        sys.exit(2)
server = QLocalServer()
name = "MarkdownViewer-single-instance"
if not server.listen(name):
    print("SKIPPED: an existing application owns the IPC endpoint; left untouched")
    sys.exit(2)
received = bytearray()
connections = []
def accept():
    while server.hasPendingConnections():
        socket = server.nextPendingConnection()
        connections.append(socket)
        def read(socket=socket):
            received.extend(bytes(socket.readAll()))
        socket.readyRead.connect(read)
        socket.disconnected.connect(read)
        read()
server.newConnection.connect(accept)
with tempfile.TemporaryDirectory(prefix="mdv-exe-smoke-") as folder:
    path = Path(folder) / "sample.md"
    path.write_text("# Packaged forwarding smoke", encoding="utf-8")
    process = QProcess()
    result = {}
    start = time.perf_counter()
    def finished(code, status):
        result.update(exit_code=code, status=str(status),
                      elapsed_ms=(time.perf_counter()-start)*1000)
        QTimer.singleShot(30, app.quit)
    process.finished.connect(finished)
    timer = QTimer()
    timer.setSingleShot(True)
    def timeout():
        result["timeout"] = True
        process.kill()
        app.quit()
    timer.timeout.connect(timeout)
    timer.start(15000)
    process.start(str(args.exe.resolve()), [str(path)])
    app.exec()
    timer.stop()
    server.close()
    result["path_matches"] = received.decode("utf-8") == str(path.resolve())
    result["received"] = received.decode("utf-8")
    result["expected"] = str(path.resolve())
    result["scope"] = "packaged second-instance forwarding; no MainWindow or cold first paint"
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    assert result.get("exit_code") == 0 and result["path_matches"] and not result.get("timeout")
