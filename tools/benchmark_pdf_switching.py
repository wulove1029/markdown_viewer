"""Repeatable real-raster PdfView switching probe (offscreen, not desktop FPS).

Run with Python 3.13. --root can point at an archived baseline checkout.
The same paint wrapper observes baseline/new implementations; load returns and
white placeholders never count as readable content. No user files are opened.
"""
from __future__ import annotations

import argparse
import inspect
import json
import os
import statistics
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round((len(ordered) - 1) * fraction))]


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--pages", default="100,1000,3000")
    parser.add_argument("--reps", type=int, default=30)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=Path, default=Path("tmp/pdf-switch-samples"))
    args = parser.parse_args()
    sys.path.insert(0, str(args.root.resolve()))
    import pymupdf
    import PySide6
    import shiboken6
    from PySide6.QtCore import QThreadPool, QTimer
    from PySide6.QtWidgets import QApplication

    from app import pdf_metadata_cache
    from app.pdf_view import PdfView

    qapp = QApplication.instance() or QApplication([])
    args.samples.mkdir(parents=True, exist_ok=True)
    report = {"python": sys.version, "pyside": PySide6.__version__, "root": str(args.root),
              "scope": "offscreen PdfView real target raster; excludes MainWindow/desktop",
              "rounds": args.rounds, "reps_per_round": args.reps, "results": []}
    for count in map(int, args.pages.split(",")):
        paths = [args.samples / f"mixed-{count}-{name}.pdf" for name in ("a", "b")]
        for index, path in enumerate(paths):
            if path.exists():
                continue
            with pymupdf.open() as document:
                for page_number in range(count):
                    page = document.new_page(width=600 if page_number % 2 else 420, height=800)
                    page.insert_text((50, 60), f"Document {index}, page {page_number}")
                    page.draw_rect((50, 100, 350, 500), color=(0.2, 0.4, 0.8))
                document.save(path)
        view = PdfView()
        view.resize(900, 700)
        view.show()
        qapp.processEvents()
        target = count // 2
        ready = False
        painted = False
        target_path = None
        load_ms = 0
        gaps = []
        last_tick = time.perf_counter()
        timer = QTimer()
        timer.setInterval(5)

        def heartbeat():
            nonlocal last_tick
            now = time.perf_counter()
            gaps.append((now - last_tick) * 1000)
            last_tick = now

        timer.timeout.connect(heartbeat)
        timer.start()
        original_raster = view._paint_page_raster
        original_paint = view.paintEvent

        def raster(painter, page, *parameters):
            nonlocal painted
            result = original_raster(painter, page, *parameters)
            if page == target and view._path == target_path and result:
                painted = True
            return result

        def paint(event):
            nonlocal ready
            original_paint(event)
            ready = ready or painted

        view._paint_page_raster = raster
        view.paintEvent = paint
        modern = "restore_page" in inspect.signature(view.load).parameters

        def switch(path):
            nonlocal ready, painted, target_path, load_ms, last_tick
            ready = painted = False
            target_path = path
            gaps.clear()
            last_tick = start = time.perf_counter()
            if modern:
                assert view.load(path, restore_page=target)
            else:
                assert view.load(path)
                view.restore_page(target)
            load_ms = (time.perf_counter() - start) * 1000
            while not ready:
                qapp.processEvents()
                if time.perf_counter() - start > 15:
                    raise RuntimeError(f"Timed out waiting for raster: {path}")
                time.sleep(0.001)
            elapsed = (time.perf_counter() - start) * 1000
            heartbeat()
            previews = getattr(view, "_document_previews", None)
            return {"readable_ms": elapsed, "load_ms": load_ms, "max_gap_ms": max(gaps),
                    "reused": target in getattr(view, "_reused_preview_pages", set()),
                    "cache_bytes": view._cache.bytes_used + (
                        previews.bytes_used if previews else 0)}

        pdf_metadata_cache._SIZES.clear()
        cold = switch(paths[0])
        switch(paths[1])
        switch(paths[0])
        measurements = []
        for round_number in range(args.rounds):
            for repetition in range(args.reps):
                switch(paths[1])
                row = switch(paths[0])
                row.update(round=round_number, repetition=repetition)
                measurements.append(row)
        row = {"pages": count, "target_page": target, "file_bytes": paths[0].stat().st_size,
               "dpr": view.viewport().devicePixelRatioF(), "cold": cold,
               "warm": {"p50_ms": statistics.median(m["readable_ms"] for m in measurements),
                        "p95_ms": percentile([m["readable_ms"] for m in measurements], .95),
                        "max_gap_p95_ms": percentile([m["max_gap_ms"] for m in measurements], .95),
                        "reused_count": sum(m["reused"] for m in measurements)},
               "measurements": measurements}
        report["results"].append(row)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({"pages": count, "cold": cold, "warm": row["warm"]}), flush=True)
        timer.stop()
        view._outline_submit_timer.stop()
        view._embedded_annotations_submit_timer.stop()
        if hasattr(view, "_geometry_timer"):
            view._geometry_timer.stop()
        view._reset_render_pipeline(clear_cache=True)
        QThreadPool.globalInstance().waitForDone(15000)
        qapp.processEvents()
        view._doc.close()
        for session in view._render_scheduler._sessions_by_generation.values():
            session.document.close()
        view.close()
        shiboken6.delete(view)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
