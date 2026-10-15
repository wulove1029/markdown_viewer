"""Isolated MainWindow A/B switching with real Chromium and PDF painting.

Run the same tool with --root pointing at a source snapshot for comparison.
Frame callbacks/offscreen paints are proxies, not physical display latency.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path


def summary(values):
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    return {"n": len(values), "p50_ms": statistics.median(values),
            "p95_ms": ordered[math.ceil(len(values) * .95) - 1],
            "max_ms": max(values)}


def markdown_ready_script(generation, marker, *, require_full=False):
    """Same DOM contract on baseline/candidate, without candidate-only signals."""
    return """(() => {
      const m=document.querySelector('meta[name="markdown-viewer-render-generation"]');
      const g=%d;
      if(!m || Number(m.content)!==g || document.querySelector('.state-page') ||
         document.querySelector('meta[name="markdown-viewer-render-error"]') ||
         !document.body.innerText.includes(%s)) return false;
      if(%s && document.querySelector('.partial-preview-notice')) return false;
      const partial=!!document.querySelector('.partial-preview-notice');
      const token=String(g)+':'+String(partial);
      if(window.__mdvBenchmarkFrame===token) return true;
      requestAnimationFrame(()=>requestAnimationFrame(()=>window.__mdvBenchmarkFrame=token));
      return false;
    })()""" % (generation, json.dumps(marker), "true" if require_full else "false")


def process_tree_rss():
    try:
        import psutil
        process = psutil.Process()
        total = process.memory_info().rss
        for child in process.children(recursive=True):
            try:
                total += child.memory_info().rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return total
    except ImportError:
        return None


def matching_raster(paints, path, generation, page, started):
    return any(p == str(path) and g == generation and drawn_page == page and t >= started
               for p, g, drawn_page, t in paints)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=30)
    parser.add_argument("--size", type=int, default=100_000)
    parser.add_argument("--kind", choices=("markdown", "text", "pdf"), default="markdown")
    parser.add_argument("--timeout", type=float, default=45)
    parser.add_argument("--overall-timeout", type=float, default=300)
    parser.add_argument("--label", default="candidate")
    parser.add_argument("--pdf-page", type=int, default=10, help="Zero-based restored page")
    parser.add_argument("--category", choices=("long_prose", "code_heavy"), default="long_prose")
    parser.add_argument("--visible", action="store_true")
    args = parser.parse_args()
    if args.repeats < 1 or args.size < 1 or args.timeout <= 0:
        parser.error("repeats and size must be positive")
    args.output = args.output.resolve()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = {"root": str(args.root.resolve()), "label": args.label,
              "python": sys.version, "kind": args.kind, "size": args.size,
              "category": args.category, "status": "initializing", "samples": []}

    def save_report():
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")

    def deadline_expired():
        import faulthandler
        report["status"] = "overall_timeout"
        report["passed"] = False
        save_report()
        faulthandler.dump_traceback(all_threads=True)
        try:
            import psutil
            for child in psutil.Process().children(recursive=True):
                try:
                    child.kill()
                except psutil.Error:
                    pass
        finally:
            os._exit(124)

    watchdog = threading.Timer(args.overall_timeout, deadline_expired)
    watchdog.daemon = True
    watchdog.start()
    save_report()
    if not args.visible:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
    sys.path.insert(0, str(args.root.resolve()))
    # The source-only baseline has app/main but deliberately shares the same
    # benchmark generators/assets. Resolve app from --root, tools from here.
    sys.path.insert(1, str(Path(__file__).resolve().parents[1]))
    from PySide6 import __version__ as pyside_version
    from PySide6.QtCore import QSettings, QStandardPaths, QThreadPool, QTimer
    from PySide6.QtWebEngineCore import QWebEngineProfile
    from PySide6.QtWidgets import QApplication

    # Retain the isolated directory if Qt still holds a file handle on Windows.
    # No personal settings, library or recovery records are read or written.
    folder = Path(tempfile.mkdtemp(prefix="mdv-switch-benchmark-"))
    report["temp"] = str(folder)
    QStandardPaths.setTestModeEnabled(True)
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(folder))
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.SystemScope,
                      str(folder / "system"))
    qt_app = QApplication([])
    qt_app.setApplicationName("MDVSwitchBenchmark")
    qt_app.setOrganizationName("MDVBenchmark")
    profile = QWebEngineProfile.defaultProfile()
    profile.setPersistentStoragePath(str(folder / "web-storage"))
    profile.setCachePath(str(folder / "web-cache"))
    settings = QSettings("markdown-viewer", "MarkdownViewer")
    settings.setValue("update_check_enabled", False)
    settings.setFallbacksEnabled(False)
    settings.sync()
    from app import document_libraries, recovery, tag_colors, tag_index
    document_libraries._default_store_path = lambda: folder / "libraries.json"
    recovery._default_recovery_dir = lambda: folder / "recovery"
    tag_colors._default_colors_path = lambda: folder / "colors.json"
    tag_index._default_index_path = lambda: folder / "tags.json"
    from app import window as window_module

    report.update({"pyside": pyside_version, "mode": "visible" if args.visible else "offscreen",
              "settings": settings.fileName(),
              "limitations": ["DOM double-frame and raster paint proxies, not monitor first paint",
                              "Filesystem cache warm; excludes application startup",
                              "TXT proxy: buffer installed/event processing; "
                              "not native paint timing",
                              "No remote images or external network dependencies"]})
    paths = []
    for name in ("A", "B"):
        suffix = {"markdown": ".md", "text": ".txt", "pdf": ".pdf"}[args.kind]
        path = folder / (name + suffix)
        if args.kind == "pdf":
            import pymupdf
            doc = pymupdf.open()
            for number in range(args.size):
                page = doc.new_page()
                page.insert_text((60, 80), f"{name} PAGE {number + 1}")
            doc.save(path)
            doc.close()
        else:
            if args.kind == "markdown":
                from tools.benchmark_markdown_first_load import generate_doc
                body = generate_doc(args.category, args.size)
            else:
                block = "Readable document paragraph with plain text.\n"
                body = (block * (args.size // len(block) + 1))[:args.size]
            path.write_text(f"## {name} section\n\n" + body + "\n#benchmark\n", encoding="utf-8")
        paths.append(path)
    target_page = min(max(args.pdf_page, 0), args.size - 1) if args.kind == "pdf" else None
    if target_page is not None:
        settings.setValue("pdf_last_pages", json.dumps({str(path): target_page for path in paths}))
        settings.sync()
    report["pdf_target_page"] = target_page
    report["file_bytes"] = {path.name: path.stat().st_size for path in paths}
    report["status"] = "constructing_window"
    save_report()
    window = window_module.MainWindow()
    window.resize(1280, 800)
    window.show()
    report["dpr"] = window.devicePixelRatioF()
    report["screen_dpi"] = window.screen().logicalDotsPerInch()
    report["rss_start_bytes"] = process_tree_rss()
    report["status"] = "running"
    save_report()
    sections = []

    def trace_method(owner, name):
        original = getattr(owner, name, None)
        if original is None:
            return

        def traced(*arguments, **keywords):
            began = time.perf_counter()
            try:
                return original(*arguments, **keywords)
            finally:
                sections.append({"method": name, "ms": (time.perf_counter() - began) * 1000})

        setattr(owner, name, traced)

    for name in ("_activate_tab", "_save_active_tab_state", "_snapshot_editor_state",
                 "_load_document", "_update_front_tags", "_refresh_properties_panel",
                 "_activate_editor_state", "_enter_edit_mode", "_open_pdf"):
        trace_method(window, name)
    trace_method(window._renderer, "load_file")
    gaps = []
    last_tick = time.perf_counter()
    heartbeat = QTimer()
    heartbeat.setInterval(16)

    def tick():
        nonlocal last_tick
        now = time.perf_counter()
        gaps.append((now - last_tick) * 1000)
        last_tick = now

    heartbeat.timeout.connect(tick)
    heartbeat.start()
    pdf_paints = []
    if args.kind == "pdf":
        # Same observable seam in baseline and candidate: actual raster drawn,
        # followed by the end of paintEvent. A blank placeholder never counts.
        view = window._pdf_view
        original_raster = view._paint_page_raster

        def raster(*a, **kw):
            result = original_raster(*a, **kw)
            page = a[1] if len(a) > 1 else kw.get("page")
            if result and page == target_page:
                pdf_paints.append((str(view._path), view._load_generation,
                                   page, time.perf_counter()))
            return result

        view._paint_page_raster = raster

    def wait_ready(path, started, *, require_full=False):
        expected_generation = getattr(window._renderer, "_render_generation", -1)
        pdf_generation = getattr(window._pdf_view, "_load_generation", -1)
        deadline = started + args.timeout
        state = {"done": False, "pending": False}
        while time.perf_counter() < deadline:
            qt_app.processEvents()
            if args.kind == "text":
                if (window._current_file == path and window._edit_mode
                        and window._editor.document().firstBlock().text().startswith(
                            "## " + path.stem)
                        and window._editor.document().characterCount() >= args.size):
                    return time.perf_counter()
            elif args.kind == "pdf":
                if matching_raster(pdf_paints, path, pdf_generation, target_page, started):
                    # processEvents returned, so the raster's paintEvent also
                    # returned; this is a native paint proxy for the target page.
                    return time.perf_counter()
            elif not state["pending"]:
                state["pending"] = True
                js = markdown_ready_script(expected_generation, path.stem + " section",
                                           require_full=require_full)

                def checked(value):
                    state["pending"] = False
                    state["done"] = value is True

                window._renderer.page().runJavaScript(js, checked)
            if state["done"]:
                return time.perf_counter()
            time.sleep(.002)
        return None

    try:
        # Cold-within-session opens are retained separately, then A/B returns.
        for index in range(2 + 2 * args.repeats):
            path = paths[index % 2]
            gaps.clear()
            sections.clear()
            from app import md_converter
            cache_hit = md_converter.has_cached_body(path) if args.kind == "markdown" else None
            last_tick = time.perf_counter()
            started = last_tick
            window.open_path(str(path))
            returned = time.perf_counter()
            ready = wait_ready(path, started)
            readable_gaps = list(gaps)
            full_ready = (wait_ready(path, started, require_full=True)
                          if ready is not None and args.kind == "markdown" else ready)
            item = {"path": path.name, "warm": index >= 2,
                    "body_cache_hit": cache_hit,
                    "sync_ms": (returned - started) * 1000,
                    "ready_ms": None if ready is None else (ready - started) * 1000,
                    "full_ready_ms": None if full_ready is None else (full_ready - started) * 1000,
                    "max_ui_gap_ms": max(readable_gaps, default=(returned - started) * 1000),
                    "full_max_ui_gap_ms": max(gaps, default=(returned - started) * 1000),
                    "rss_tree_bytes": process_tree_rss(),
                    "sync_sections": list(sections),
                    "success": ready is not None and full_ready is not None}
            report["samples"].append(item)
            save_report()
            if not item["success"]:
                break
        warm = [r for r in report["samples"] if r["warm"] and r["success"]]
        report["summary"] = {key: summary([r[key] for r in warm])
                             for key in ("sync_ms", "ready_ms", "full_ready_ms", "max_ui_gap_ms")}
        report["top_sync_sections"] = sorted(
            [dict(section, path=sample["path"], warm=sample["warm"])
             for sample in report["samples"] for section in sample["sync_sections"]],
            key=lambda section: section["ms"], reverse=True)[:10]
        report["passed"] = len(report["samples"]) == 2 + 2 * args.repeats and all(
            r["success"] for r in report["samples"])
        window.grab().save(str(args.output.with_suffix(".png")))
        report["status"] = "completed" if report["passed"] else "readiness_timeout"
    finally:
        heartbeat.stop()
        from app import render_service
        render_cancel = getattr(window._renderer, "_render_cancel", None)
        if render_cancel is not None:
            render_cancel.set()
        QThreadPool.globalInstance().waitForDone(10000)
        render_service.shutdown()
        window.close()
        qt_app.processEvents()
        save_report()
    watchdog.cancel()
    print(json.dumps(report.get("summary", {}), indent=2))
    sys.stdout.flush()
    # Native WebEngine teardown can hang in headless Windows after validation.
    os._exit(0 if report.get("passed") else 1)


if __name__ == "__main__":
    main()
