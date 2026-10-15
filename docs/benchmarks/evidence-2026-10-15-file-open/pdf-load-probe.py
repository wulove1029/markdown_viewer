"""One-shot synthetic PdfView.load probe; excludes first paint and MainWindow."""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import pymupdf
import shiboken6
from PySide6.QtWidgets import QApplication

from app import pdf_metadata_cache
from app.pdf_view import PdfView

qapp = QApplication([])
report = {
    "scope": "synthetic PdfView.load only; excludes window open flow, event loop and first paint",
    "results": [],
}
with tempfile.TemporaryDirectory(prefix="mdv-pdf-open-audit-") as folder:
    for pages in (100, 1000, 3000):
        path = Path(folder) / f"{pages}.pdf"
        with pymupdf.open() as doc:
            for _ in range(pages):
                doc.new_page()
            doc.save(path)
        view = PdfView()
        view.resize(900, 700)
        pdf_metadata_cache._SIZES.clear()
        for kind in ("cold_geometry", "warm_geometry"):
            start = time.perf_counter()
            loaded = view.load(path)
            elapsed = (time.perf_counter() - start) * 1000
            view._render_dispatch_timer.stop()
            report["results"].append(
                {"pages": pages, "kind": kind, "loaded": loaded, "ms": round(elapsed, 3)}
            )
        view._doc.close()
        for session in view._render_scheduler._sessions_by_generation.values():
            session.document.close()
        shiboken6.delete(view)
print(json.dumps(report))
