"""D1a: IPC JSON protocol v1, anchors, [[page#heading]], .bak rule, tab key cache."""
# ruff: noqa: F811

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from PySide6.QtNetwork import QLocalServer
from PySide6.QtWidgets import QWidget

import main as main_mod
from app import window as window_mod
from app.links import LinkIndex, split_wikilink_target
from app.url_protocol import parse_open_target, parse_open_url
from tests.test_editor_workspace_integration import (  # noqa: F401
    _isolated_workspace_dependencies,
    make_workspace_window,
)


# ---------------- helpers ----------------
class _Recorder(QWidget):
    def __init__(self):
        super().__init__()
        self.calls = []

    def open_external_path(self, path, anchor=None):
        self.calls.append((path, anchor))


@pytest.fixture
def ipc(qapp, monkeypatch):
    name = f"mv-test-{os.getpid()}-v1-{time.monotonic_ns()}"
    monkeypatch.setattr(main_mod, "_IPC_SERVER_NAME", name)
    owner = _Recorder()
    server = main_mod._setup_ipc_server(owner)
    assert server is not None and server.isListening()
    yield owner
    server.close()
    owner.deleteLater()


_CLIENT = """
import json, sys, time
sys.path.insert(0, sys.argv[5])
import main as m
m._IPC_SERVER_NAME = sys.argv[1]
mode, payload = sys.argv[2], bytes.fromhex(sys.stdin.read().strip())
t0 = time.monotonic()
if mode == "send":
    args = json.loads(payload.decode("utf-8"))
    out = m._try_send_to_running_instance(args["path"], args.get("anchor"))
else:
    from PySide6.QtNetwork import QLocalSocket
    sock = QLocalSocket()
    sock.connectToServer(m._IPC_SERVER_NAME)
    assert sock.waitForConnected(1000)
    sock.write(payload)
    sock.waitForBytesWritten(1000)
    out = m._read_ipc_reply(sock, 2000) if mode == "raw" else True
    sock.disconnectFromServer()
print(json.dumps({"out": out, "elapsed": time.monotonic() - t0}))
"""


def _client(qapp, mode, payload: bytes, timeout=15.0):
    """Run the client in a separate process while this process serves IPC."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    proc = subprocess.Popen(
        [sys.executable, "-X", "utf8", "-c", _CLIENT, main_mod._IPC_SERVER_NAME,
         mode, "-", "-", root],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=root,
    )
    proc.stdin.write(payload.hex().encode("ascii"))
    proc.stdin.close()
    deadline = time.monotonic() + timeout
    while proc.poll() is None and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.005)
    assert proc.poll() is not None, "client process hung"
    stdout, stderr = proc.communicate()
    assert proc.returncode == 0, stderr.decode("utf-8", "replace")
    return json.loads(stdout.decode("utf-8").strip().splitlines()[-1])


def _raw_request(qapp, payload: bytes):
    return _client(qapp, "raw", payload)["out"]


def _pump(qapp, predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.005)
    return predicate()


@pytest.fixture
def note(tmp_path):
    path = tmp_path / "n.md"
    path.write_text("# Title\n\n## Second\n", encoding="utf-8")
    return path


# ---------------- v1 over a real local socket ----------------
def test_v1_open_replies_ok_and_opens(qapp, ipc, note):
    request = {"v": 1, "cmd": "open", "path": str(note), "anchor": {"heading": "Second"}}
    reply = _raw_request(qapp, json.dumps(request).encode("utf-8"))
    assert reply == {"ok": True}
    assert ipc.calls == [(str(note), {"heading": "Second"})]


def test_v1_open_without_anchor_and_line_anchor(qapp, ipc, note):
    assert _raw_request(
        qapp, json.dumps({"v": 1, "cmd": "open", "path": str(note)}).encode()
    ) == {"ok": True}
    assert _raw_request(
        qapp,
        json.dumps({"v": 1, "cmd": "open", "path": str(note), "anchor": {"line": 3}}).encode(),
    ) == {"ok": True}
    assert ipc.calls == [(str(note), None), (str(note), {"line": 3})]


def test_v1_ping_returns_version(qapp, ipc):
    from app.version import VERSION

    reply = _raw_request(qapp, b'{"v":1,"cmd":"ping"}')
    assert reply == {"ok": True, "version": VERSION}
    assert ipc.calls == []


@pytest.mark.parametrize(
    "payload",
    [
        b'{"v":1,"cmd":"open","path":',  # truncated, then EOF is not required: see below
        b"{not json}",
        b'{"v":1,"cmd":"explode"}',
        b'{"v":2,"cmd":"ping"}',
        b'{"cmd":"ping"}',
        b'{"v":1,"cmd":"open","path":"relative.md"}',
        b'{"v":1,"cmd":"open","path":123}',
        b'{"v":1,"cmd":"open","path":"C:\\\\nope\\\\missing.md"}',
        b'{"v":1,"cmd":"open","path":"C:\\\\x\\\\a.md","anchor":{"line":0}}',
        b'{"v":1,"cmd":"open","path":"C:\\\\x\\\\a.md","anchor":"top"}',
    ],
)
def test_bad_requests_reply_not_ok_and_execute_nothing(qapp, ipc, payload):
    if payload.endswith(b'"path":'):
        # Incomplete JSON then EOF: the server must not run it as a bare path.
        _client(qapp, "noreply", payload)
        _pump(qapp, lambda: False, timeout=0.3)
        assert ipc.calls == []
        return
    reply = _raw_request(qapp, payload)
    assert reply is not None and reply["ok"] is False and reply["error"]
    assert ipc.calls == []


def test_unsupported_file_type_is_refused(qapp, ipc, tmp_path):
    exe = tmp_path / "x.exe"
    exe.write_text("x")
    request = json.dumps({"v": 1, "cmd": "open", "path": str(exe)}).encode()
    reply = _raw_request(qapp, request)
    assert reply == {"ok": False, "error": "unsupported_type"}
    assert ipc.calls == []


def test_v1_folder_is_opened(qapp, ipc, tmp_path):
    request = json.dumps({"v": 1, "cmd": "open", "path": str(tmp_path)}).encode()
    assert _raw_request(qapp, request) == {"ok": True}
    assert ipc.calls == [(str(tmp_path), None)]


def test_v1_oversize_payload_is_refused(qapp, ipc):
    big = b'{"v":1,"cmd":"open","path":"' + b"a" * (70 * 1024) + b'"}'
    reply = _raw_request(qapp, big)
    assert reply is not None and reply["ok"] is False
    assert ipc.calls == []


def test_legacy_bare_path_still_works(qapp, ipc, note):
    _client(qapp, "noreply", str(note).encode("utf-8"))  # old clients just close
    assert _pump(qapp, lambda: bool(ipc.calls))
    assert ipc.calls == [(str(note), None)]


def test_legacy_empty_payload_activates(qapp, ipc):
    _client(qapp, "noreply", b"")
    assert _pump(qapp, lambda: bool(ipc.calls))
    assert ipc.calls == [("", None)]


def test_second_instance_forwards_v1_and_gets_reply(qapp, ipc, note):
    args = json.dumps({"path": str(note), "anchor": {"line": 2}}).encode("utf-8")
    result = _client(qapp, "send", args)
    assert result["out"] is True
    assert result["elapsed"] < 2.5  # reply arrived, no timeout wait
    assert ipc.calls == [(str(note), {"line": 2})]


def test_second_instance_does_not_hang_on_silent_old_server(qapp, monkeypatch):
    """An old server reads until EOF and never answers: client gives up in 3 s."""
    name = f"mv-test-{os.getpid()}-silent-{time.monotonic_ns()}"
    monkeypatch.setattr(main_mod, "_IPC_SERVER_NAME", name)
    server = QLocalServer()
    kept = []
    server.newConnection.connect(lambda: kept.append(server.nextPendingConnection()))
    assert server.listen(name)
    try:
        args = {"path": "C:\\a.md", "anchor": {"line": 1}}
        result = _client(qapp, "send", json.dumps(args).encode("utf-8"))
    finally:
        server.close()
    assert result["out"] is True  # a server owns the session: never become a second primary
    assert 2.5 <= result["elapsed"] < 4.5


def test_no_server_returns_false(qapp, monkeypatch):
    monkeypatch.setattr(main_mod, "_IPC_SERVER_NAME", f"mv-test-{os.getpid()}-none")
    assert main_mod._try_send_to_running_instance("") is False


# ---------------- handle_ipc_request unit-level ----------------
def test_handle_ipc_request_rejects_bool_version_and_non_dict():
    window = _Recorder()
    assert main_mod.handle_ipc_request(window, [1])["ok"] is False
    assert main_mod.handle_ipc_request(window, {"v": True, "cmd": "ping"})["ok"] is False
    assert window.calls == []


def test_launch_request_carries_url_anchor(tmp_path):
    target = str(tmp_path / "n.md")
    url = "markdownviewer://open?path=" + quote(target, safe="") + "&heading=" + quote("二 節")
    path, anchor = main_mod.resolve_launch_request(url)
    assert path.endswith("n.md") and anchor == {"heading": "二 節"}
    assert main_mod.resolve_launch_request("markdownviewer://run?path=x") is None
    assert main_mod.resolve_launch_request("") == ("", None)


# ---------------- URL anchors ----------------
def _url(path, extra=""):
    return "markdownviewer://open?path=" + quote(path, safe="") + extra


def test_url_heading_and_line():
    path = "C:\\docs\\a.md"
    assert parse_open_target(_url(path, "&heading=" + quote("中文 標題"))) == (
        path, {"heading": "中文 標題"})
    assert parse_open_target(_url(path, "&line=42")) == (path, {"line": 42})
    assert parse_open_target(_url(path)) == (path, None)
    assert parse_open_url(_url(path, "&line=42")) == path


@pytest.mark.parametrize(
    "extra",
    ["&line=0", "&line=-3", "&line=abc", "&line=", "&heading=%00", "&heading="],
)
def test_url_bad_anchor_is_dropped_but_path_kept(extra):
    assert parse_open_target(_url("C:\\docs\\a.md", extra)) == ("C:\\docs\\a.md", None)


def test_url_anchor_does_not_relax_path_rules():
    assert parse_open_target(_url("C:\\x\\a.exe", "&line=1")) is None
    assert parse_open_target(_url("C:\\x\\..\\a.md", "&heading=h")) is None


# ---------------- window anchors ----------------
def _spy_window(make_workspace_window, headings):
    window = make_workspace_window()
    calls = {"scroll": [], "line": []}
    window._renderer.scroll_to = calls["scroll"].append
    window._renderer.reveal_source_line_after_load = calls["line"].append
    window._panel.toc = SimpleNamespace(entries=lambda: list(headings))
    return window, calls


def test_open_external_path_scrolls_to_heading(make_workspace_window, note):
    window, calls = _spy_window(
        make_workspace_window, [(1, "Title", "title"), (2, "Second", "second")]
    )
    window.open_external_path(str(note), {"heading": " second "})
    assert calls["scroll"] == ["second"]
    assert window._index_of_path(str(note)) >= 0


def test_open_external_path_scrolls_to_line(make_workspace_window, note):
    window, calls = _spy_window(make_workspace_window, [])
    window.open_external_path(str(note), {"line": 3})
    assert calls["line"] == [3]


def test_unknown_heading_still_opens_file(make_workspace_window, note):
    window, calls = _spy_window(make_workspace_window, [(1, "Title", "title")])
    window.open_external_path(str(note), {"heading": "Nope"})
    assert calls["scroll"] == []
    assert window._index_of_path(str(note)) >= 0


# ---------------- [[page#heading]] ----------------
def test_split_wikilink_target():
    assert split_wikilink_target("a#H") == ("a", "H")
    assert split_wikilink_target(" a b # 標題 ") == ("a b", "標題")
    assert split_wikilink_target("a") == ("a", "")
    assert split_wikilink_target("a#H1#H2") == ("a", "H2")
    assert split_wikilink_target("#H") == ("", "H")


def test_wikilink_with_heading_resolves_file_and_backlinks(tmp_path):
    a = tmp_path / "a.md"
    b = tmp_path / "Page B.md"
    other = tmp_path / "sub"
    other.mkdir()
    decoy = other / "Page B#Intro.md"  # a file literally named with '#' must not matter
    docs = [
        (a, "see [[Page B#Intro]] and [[Page B#Intro|alias]] and [[#Local]]"),
        (b, "# Intro"),
        (decoy, "x"),
    ]
    index = LinkIndex()
    index.build(docs)
    assert index.resolve("Page B#Intro", a) == b
    assert index.resolve("page b # Intro ", a) == b
    assert index.resolve("Page B.md#Intro", a) == b
    assert index.resolve("#Local", a) == a
    assert index.resolve("#Local") is None
    assert index.backlinks(b) == [str(a)]
    assert index.forward[str(a)] == {str(b)}


def test_wikilink_click_opens_file_and_scrolls_to_heading(make_workspace_window, tmp_path):
    a = tmp_path / "a.md"
    b = tmp_path / "b.md"
    a.write_text("[[b#Deep Dive]]", encoding="utf-8")
    b.write_text("# Top\n\n## Deep Dive\n", encoding="utf-8")
    window, calls = _spy_window(
        make_workspace_window, [(1, "Top", "top"), (2, "Deep Dive", "deep-dive")]
    )
    window._link_index.build([(a, a.read_text(encoding="utf-8")), (b, "# Top")])
    window.open_path(str(a))
    window._on_wikilink_clicked("b#Deep Dive")
    assert str(window._current_file) == str(b)
    assert calls["scroll"] == ["deep-dive"]


# ---------------- .bak hide rule ----------------
def test_hidden_sidecar_rule():
    from app.file_browser import _is_hidden_sidecar

    names = {"a.md", "a.md.bak", "doc.md", "doc.md.2026-11-08.bak",
             "orphan.md.bak", "a.md.notes.json"}
    assert _is_hidden_sidecar("a.md.bak", names)
    assert _is_hidden_sidecar("A.MD.BAK", names)
    assert _is_hidden_sidecar("a.md.notes.json", names)
    assert not _is_hidden_sidecar("Doc.md.2026-11-08.bak", names)
    assert not _is_hidden_sidecar("orphan.md.bak", names)


def test_scan_hides_only_own_bak(tmp_path, monkeypatch):
    from app import file_browser as fb

    (tmp_path / "a.md").write_text("x")
    (tmp_path / "a.md.bak").write_text("x")
    (tmp_path / "Doc.md").write_text("x")
    (tmp_path / "Doc.md.2026-11-08.bak").write_text("x")
    hidden = []
    monkeypatch.setattr(fb, "set_hidden", hidden.append)
    request = fb._ScanRequest(
        libraries=[], query="", allowed=None, excluded=[], transient=set(),
        tags_for=lambda _p: [], filtering=False, active_tag="",
    )
    fb._scan_folder(str(tmp_path), request, None)
    names = sorted(os.path.basename(p) for p in hidden)
    assert names == ["a.md.bak"]


# ---------------- tab key cache ----------------
def test_index_of_path_caches_normalized_keys(make_workspace_window, tmp_path, monkeypatch):
    paths = []
    for name in ("a.md", "b.md", "c.md"):
        p = tmp_path / name
        p.write_text("# x", encoding="utf-8")
        paths.append(p)
    window = make_workspace_window()
    for p in paths:
        window.open_path(str(p))
    calls = []
    real = window_mod.tab_path_key
    monkeypatch.setattr(window_mod, "tab_path_key", lambda v: calls.append(v) or real(v))
    window._tab_key_cache.clear() if hasattr(window, "_tab_key_cache") else None
    spelled = str(paths[2]).swapcase()
    assert window._index_of_path(spelled) == 2
    first = len(calls)
    assert first <= 4  # one per distinct spelling, not per call
    for _ in range(5):
        assert window._index_of_path(spelled) == 2
        assert window._index_of_path(str(paths[0])) == 0
    assert len(calls) <= first + 1  # only the one new spelling
    assert window._index_of_path(str(tmp_path / "zzz.md")) == -1


def test_payload_is_bare_path_without_anchor_and_json_with_anchor():
    assert main_mod._ipc_payload("C:\筆記\a.md", None) == "C:\筆記\a.md".encode("utf-8")
    assert main_mod._ipc_payload("", None) == b""
    sent = json.loads(main_mod._ipc_payload("C:\a.md", {"line": 4}).decode("utf-8"))
    assert sent == {"v": 1, "cmd": "open", "path": "C:\a.md", "anchor": {"line": 4}}


def test_second_instance_without_anchor_sends_bare_path_and_does_not_wait(qapp, ipc, note):
    result = _client(qapp, "send", json.dumps({"path": str(note)}).encode("utf-8"))
    assert result["out"] is True
    assert result["elapsed"] < 2.5
    assert _pump(qapp, lambda: bool(ipc.calls))
    assert ipc.calls == [(str(note), None)]
