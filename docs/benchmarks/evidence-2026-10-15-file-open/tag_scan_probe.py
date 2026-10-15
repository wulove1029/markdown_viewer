"""Measure the synchronous tag extraction used before preview submission.

Isolated component probe only: no MainWindow, painting, index persistence,
or panel construction. Uses synthetic documents in a temporary directory.
"""
from __future__ import annotations

import importlib.metadata
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from app.md_converter import body_hashtags, front_matter_tags, parse_front_matter, read_text
from tools.benchmark_markdown_first_load import generate_doc


def main():
    report = {
        "scope": "Synchronous tag extraction component, excludes UI and tag index",
        "python": sys.version,
        "packages": {name: importlib.metadata.version(name) for name in
                     ("PySide6", "markdown-it-py", "Pygments")},
        "limitations": ["Warm filesystem; five samples per case; no first paint",
                        "Synthetic prose with a final hashtag; no slow filesystem"],
        "cases": {},
    }
    with tempfile.TemporaryDirectory(prefix="mdv-tag-open-plan-") as folder:
        for size in (100_000, 1_000_000, 5_000_000):
            path = Path(folder) / f"prose-{size}.md"
            path.write_text(generate_doc("long_prose", size) + "\n\n#probetag\n",
                            encoding="utf-8")
            samples = []
            for _ in range(5):
                start = time.perf_counter()
                result = read_text(path)
                decoded = time.perf_counter()
                assert result is not None
                front, body = parse_front_matter(result[0])
                front_tags = front_matter_tags(front)
                body_tags = body_hashtags(body)
                ended = time.perf_counter()
                assert "probetag" in body_tags
                samples.append({"read_ms": (decoded - start) * 1000,
                                "tag_parse_ms": (ended - decoded) * 1000,
                                "total_ms": (ended - start) * 1000})
            report["cases"][str(size)] = {
                "bytes": path.stat().st_size,
                "n": len(samples),
                "p50_total_ms": statistics.median(s["total_ms"] for s in samples),
                "max_total_ms": max(s["total_ms"] for s in samples),
                "samples": samples,
            }
    target = Path(__file__).with_name("tag-scan.json")
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
