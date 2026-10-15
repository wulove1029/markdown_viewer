"""Same-process tag scan comparison; excludes UI and first visible content."""

import ast
import json
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from app import document_metadata as dm
from app import md_converter as mc
from tools.benchmark_markdown_first_load import generate_doc


def main():
    baseline = subprocess.check_output(
        ["git", "show", "HEAD:app/md_converter.py"], cwd=ROOT, encoding="utf-8")
    function = next(node for node in ast.parse(baseline).body
                    if isinstance(node, ast.FunctionDef) and node.name == "body_hashtags")
    namespace = dict(mc.__dict__)
    exec(compile(ast.Module(body=[function], type_ignores=[]), "legacy-scan", "exec"),
         namespace)
    legacy = namespace["body_hashtags"]
    report = {"python": sys.version, "scope": "component only; no UI or first paint",
              "baseline": subprocess.check_output(["git", "rev-parse", "HEAD"],
                                                   cwd=ROOT, text=True).strip(),
              "cases": {}}
    with tempfile.TemporaryDirectory(prefix="mdv-metadata-implementation-") as folder:
        for size in (100_000, 1_000_000, 5_000_000):
            path = Path(folder) / f"{size}.md"
            path.write_text(generate_doc("long_prose", size) + "\n\n#probetag\n",
                            encoding="utf-8")
            case = {"bytes": path.stat().st_size}
            expected = None
            for name, scan in (("legacy", legacy), ("line_scan", mc.body_hashtags)):
                samples = []
                for _ in range(5):
                    start = time.perf_counter()
                    text, _ = mc.read_text(path)
                    front, body = mc.parse_front_matter(text)
                    tags = (mc.front_matter_tags(front), scan(body))
                    samples.append((time.perf_counter() - start) * 1000)
                    assert "probetag" in tags[1]
                    if expected is None:
                        expected = tags
                    assert tags == expected
                case[name] = {"n": len(samples), "p50_ms": statistics.median(samples),
                              "max_ms": max(samples), "samples_ms": samples}
            dm.read_metadata(path)
            samples = []
            for _ in range(30):
                start = time.perf_counter()
                assert dm.cached_metadata(path) is not None
                samples.append((time.perf_counter() - start) * 1000)
            case["cached_lookup"] = {"n": len(samples), "p50_ms": statistics.median(samples),
                                     "p95_ms": sorted(samples)[28], "max_ms": max(samples)}
            report["cases"][str(size)] = case
    Path(__file__).with_name("metadata-probe.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
