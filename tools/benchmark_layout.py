"""Compare HEAD and working-tree PDF loads and tree application, using synthetic data."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import types
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication
import shiboken6
from app.document_libraries import DocumentLibrary, DocumentLibraryStore
from tools.benchmark_upgrade import measure


def baseline(name):
    source = subprocess.check_output(["git", "show", "HEAD:app/" + name + ".py"])
    module_name = "app._benchmark_baseline_" + name
    module = types.ModuleType(module_name)
    module.__file__ = str(Path("app") / (name + ".py"))
    sys.modules[module_name] = module
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    return module


def main():
    from app import file_browser, pdf_view
    import pymupdf
    app = QApplication([])
    report = {"scope": "synthetic component load/application; excludes first paint"}
    with tempfile.TemporaryDirectory(prefix="mdv-layout-") as folder:
        root = Path(folder)
        for label, browser, pdf in (("before", baseline("file_browser"), baseline("pdf_view")),
                                    ("after", file_browser, pdf_view)):
            store = DocumentLibraryStore(root / "libraries.json")
            store.save([])
            browser.DocumentLibraryStore = lambda: store
            view = browser.FileBrowserView(lambda _: None, background_scan=True)
            library = DocumentLibrary("bench", "Benchmark", str(root))
            for count in (100, 1000, 10000):
                nodes = [browser._ScanNode(f"{i}.md", str(root / f"{i}.md"), False) for i in range(count)]
                request = browser._ScanRequest([library], "", None, [], set(), lambda _: [], False, "")
                scans = [browser._LibraryScan(library, True, nodes, count)]
                slices = []
                def build():
                    start = time.perf_counter()
                    view._apply_scan(view._scan_generation, request, scans)
                    slices.append((time.perf_counter() - start)*1000)
                    while getattr(view, "_build_iterator", None) is not None:
                        start = time.perf_counter()
                        view._advance_build()
                        slices.append((time.perf_counter() - start)*1000)
                report[f"{label}_tree_{count}"] = measure(build, 5)
                report[f"{label}_tree_{count}"]["max_apply_slice_ms"] = max(slices)
            shiboken6.delete(view)
            for count in (10, 100, 750, 3000):
                path = root / f"{count}.pdf"
                if not path.exists():
                    document = pymupdf.open()
                    for i in range(count):
                        document.new_page(width=595 if i % 2 else 842, height=842)
                    document.save(path)
                    document.close()
                viewer = pdf.PdfView()
                report[f"{label}_pdf_{count}_load"] = measure(lambda: viewer.load(path), 30)
                shiboken6.delete(viewer)
    output = Path("docs/benchmarks/layout-comparison.json")
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: {x:y for x,y in v.items() if x != "samples_ms"}
                      if isinstance(v,dict) else v for k,v in report.items()}, indent=2))
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
