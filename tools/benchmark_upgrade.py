"""Repeatable, isolated component benchmarks; not an end-to-end paint metric."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def measure(callback, count):
    values = []
    for _ in range(count):
        start = time.perf_counter()
        callback()
        values.append((time.perf_counter() - start) * 1000)
    ordered = sorted(values)
    return {"p50_ms": statistics.median(values),
            "p95_ms": ordered[min(len(values)-1, int(len(values)*.95))],
            "samples_ms": values}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=30)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("repeats must be positive")
    from PySide6.QtWidgets import QApplication
    from app import md_converter
    from app.annotations import DocumentAnnotations
    from app.tag_index import TagIndex
    app = QApplication.instance() or QApplication([])
    report = {"python": sys.version, "platform": sys.platform,
              "scope": "component timings; excludes first paint and packaged startup"}
    report["entry_import"] = measure(lambda: subprocess.run(
        [sys.executable, "-X", "utf8", "-c", "import main"], cwd=ROOT,
        check=True, stdout=subprocess.DEVNULL), 10)
    with tempfile.TemporaryDirectory(prefix="mdv-benchmark-") as folder:
        base = Path(folder)
        for size in (10_000, 100_000, 1_000_000, 5_000_000):
            path = base / f"sample-{size}.md"
            block = "## Heading\n\nA paragraph with **bold** and a [link](#heading).\n\n"
            path.write_text((block * (size // len(block) + 1))[:size], encoding="utf-8")
            def cold():
                md_converter._CONVERT_CACHE.clear()
                md_converter.convert(path)
            report[f"markdown_{size}_cold_convert"] = measure(cold, 3)
            report[f"markdown_{size}_warm_convert"] = measure(
                lambda: md_converter.convert(path), args.repeats)
        index = TagIndex(base / "index.json")
        doc = DocumentAnnotations()
        doc.doc_tags = ["benchmark"]
        index.update(base / "sample.md", doc)
        report["unchanged_tag_update"] = measure(
            lambda: index.update(base / "sample.md", doc), args.repeats)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: {x: y for x, y in v.items() if x != "samples_ms"}
                      if isinstance(v, dict) else v for k, v in report.items()}, indent=2))


if __name__ == "__main__":
    main()

