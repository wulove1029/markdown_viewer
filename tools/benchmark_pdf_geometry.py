"""Isolate mixed-size PDF layout and visible-page lookup costs without PDF I/O."""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import time
from pathlib import Path


def summary(values):
    ordered = sorted(values)
    return {
        "n": len(values), "p50_ms": statistics.median(values),
        "p95_ms": ordered[math.ceil(len(values) * .95) - 1], "max_ms": max(values),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--pages", type=int, default=3000)
    parser.add_argument("--repeats", type=int, default=100)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QSizeF
    from PySide6.QtWidgets import QApplication

    from app.pdf_view import PdfView

    app = QApplication.instance() or QApplication([])
    view = PdfView()
    view.resize(900, 700)
    view.show()
    app.processEvents()
    view._page_sizes = [QSizeF(420 + (i % 3) * 90, 600 + (i % 5) * 50)
                        for i in range(args.pages)]
    view._geometry_scale_width = 420
    view._relayout()
    view.jump_to_page(args.pages // 2)
    app.processEvents()
    geometry = []
    lookup = []
    incremental = getattr(view, "_apply_geometry_updates", None)
    for iteration in range(args.repeats):
        changed = range((iteration * 31) % (args.pages - 32),
                        (iteration * 31) % (args.pages - 32) + 32)
        for index in changed:
            previous = view._page_sizes[index]
            view._page_sizes[index] = QSizeF(previous.width(), previous.height() + 1)
        started = time.perf_counter()
        if incremental is not None:
            view._geometry_dirty.update(changed)
            incremental()
        else:
            view._relayout()
        geometry.append((time.perf_counter() - started) * 1000)
        started = time.perf_counter()
        for _ in range(100):
            view.current_page()
            view._visible_pages()
        lookup.append((time.perf_counter() - started) * 1000)
    result = {
        "scope": "Synthetic mixed paper sizes; no PDFium I/O or raster; 32 changed pages per batch",
        "pages": args.pages, "incremental": incremental is not None,
        "geometry_batch": summary(geometry), "100_navigation_queries": summary(lookup),
        "raw_geometry_ms": geometry, "raw_navigation_ms": lookup,
    }
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if not key.startswith("raw_")}))
    view.close()


if __name__ == "__main__":
    main()
