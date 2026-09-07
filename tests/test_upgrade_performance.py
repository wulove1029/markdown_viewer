"""Regression checks for work avoided, cache bounds, and responsive IPC."""
import os
from pathlib import Path
import subprocess
import sys
import threading
from uuid import uuid4

from PySide6.QtCore import QSizeF
from PySide6.QtNetwork import QLocalSocket
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget


def test_entry_import_does_not_load_document_stack():
    subprocess.run([sys.executable, "-c",
        "import main,sys; assert 'app.window' not in sys.modules; "
        "assert 'PySide6.QtWebEngineWidgets' not in sys.modules"], check=True)


def test_ipc_buffers_fragmented_utf8_until_disconnect(qapp, monkeypatch):
    import main
    monkeypatch.setattr(main, "_IPC_SERVER_NAME", "mdv-test-" + uuid4().hex)
    class Window(QWidget):
        def __init__(self):
            super().__init__()
            self.paths = []
        def open_path(self, path):
            self.paths.append(path)
    window = Window()
    server = main._setup_ipc_server(window)
    socket = QLocalSocket()
    socket.connectToServer(main._IPC_SERVER_NAME)
    assert socket.waitForConnected(1000)
    payload = "C:/筆記/文件.md".encode("utf-8")
    socket.write(payload[:5])
    socket.flush()
    QTest.qWait(20)
    assert not window.paths
    socket.write(payload[5:])
    socket.flush()
    socket.disconnectFromServer()
    QTest.qWait(30)
    assert window.paths == [payload.decode("utf-8")]
    server.close()
    window.deleteLater()


def test_unchanged_tags_do_not_write_but_removal_does(tmp_path, monkeypatch):
    from app.annotations import DocumentAnnotations
    from app.tag_index import TagIndex
    index = TagIndex(tmp_path / "index.json")
    writes = []
    monkeypatch.setattr(index, "_save", lambda: writes.append(True))
    path = tmp_path / "a.md"
    doc = DocumentAnnotations(doc_tags=["one"])
    index.update(path, doc)
    index.update(path, doc)
    assert len(writes) == 1
    index.update(path, DocumentAnnotations())
    index.update(path, DocumentAnnotations())
    assert len(writes) == 2


def test_markdown_cache_checks_size_and_promotes_recent_entries(tmp_path, monkeypatch):
    from app import md_converter as converter
    converter._CONVERT_CACHE.clear()
    monkeypatch.setattr(converter, "_CONVERT_CACHE_MAX", 2)
    paths = [tmp_path / f"{i}.md" for i in range(3)]
    for i, path in enumerate(paths):
        path.write_text(f"# {i}", encoding="utf-8")
    first = converter.convert_body(paths[0])
    converter.convert_body(paths[1])
    assert converter.convert_body(paths[0]) is first
    converter.convert_body(paths[2])
    assert converter.convert_body(paths[0]) is first
    stat = paths[0].stat()
    paths[0].write_text("# Changed length", encoding="utf-8")
    os.utime(paths[0], ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert "Changed length" in converter.convert_body(paths[0]).body
    monkeypatch.setattr(converter, "_CONVERT_CACHE_BYTES", 1)
    converter._CONVERT_CACHE.clear()
    converter.convert_body(paths[0])
    assert not converter._CONVERT_CACHE


def test_cancelled_render_skips_conversion(qapp, monkeypatch):
    from app import renderer
    cancel = threading.Event()
    cancel.set()
    monkeypatch.setattr(renderer, "convert_text", lambda *_: (_ for _ in ()).throw(
        AssertionError("obsolete request was parsed")))
    worker = renderer._MarkdownRenderWorker(1, text="# obsolete", cancel=cancel)
    delivered = []
    worker.signals.ready.connect(lambda *args: delivered.append(args))
    worker.run()
    assert not delivered


def test_pdf_geometry_cache_is_bounded_and_invalidates_replacements(tmp_path, monkeypatch):
    from app import pdf_metadata_cache as cache
    cache._SIZES.clear()
    monkeypatch.setattr(cache, "_MAX_PAGES", 3)
    path = tmp_path / "a.pdf"
    path.write_bytes(b"original")
    key = cache.signature(path)
    cache.put_sizes(key, [QSizeF(100, 200)] * 2)
    assert cache.get_sizes(key, 2) == ((100, 200), (100, 200))
    cache.put_sizes(("b", 1), [QSizeF(200, 400)] * 2)
    assert cache.get_sizes(key, 2) is None
    path.write_bytes(b"replacement-longer")
    assert cache.signature(path) != key


def test_large_tree_build_yields_and_replays_selection(qapp, tmp_path, monkeypatch):
    from app import file_browser as fb
    from app.document_libraries import DocumentLibrary, DocumentLibraryStore
    store = DocumentLibraryStore(tmp_path / "libraries.json")
    store.save([])
    monkeypatch.setattr(fb, "DocumentLibraryStore", lambda: store)
    view = fb.FileBrowserView(lambda _: None, background_scan=True)
    library = DocumentLibrary("test", "Test", str(tmp_path))
    nodes = [fb._ScanNode(f"{i}.md", str(tmp_path / f"{i}.md"), False) for i in range(300)]
    request = fb._ScanRequest([library], "", None, [], set(), lambda _: [], False, "")
    # Deterministic work slices; no timing-dependent pass/fail threshold.
    counter = iter(range(10000))
    monkeypatch.setattr(fb, "perf_counter", lambda: next(counter) * .01)
    view._apply_scan(view._scan_generation, request, [fb._LibraryScan(library, True, nodes, len(nodes))])
    assert view.is_scanning()
    view.select_path(nodes[-1].path)
    for _ in range(305):
        view._advance_build()
    assert not view.is_scanning()
    assert view._tree.currentItem().data(0, fb._PATH_ROLE) == nodes[-1].path
    assert view._tree.topLevelItem(0).childCount() == 300
    view.deleteLater()

