"""Launch only the explicit, isolated diagnostic mode of a packaged build."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=10)
    parser.add_argument("--working-set", type=int, default=10)
    parser.add_argument("--size", type=int, default=100_000)
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()
    if not args.exe.is_file():
        parser.error("The packaged executable does not exist")
    args.output = args.output.resolve()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # A separate child report makes an interrupted run distinguishable from an
    # older successful parent report. Neither location contains personal data.
    child_output = args.output.with_name(args.output.stem + "-child.json")
    command = [
        str(args.exe.resolve()), "--packaged-benchmark", "--output", str(child_output),
        "--repeats", str(args.repeats), "--working-set", str(args.working_set),
        "--size", str(args.size), "--overall-timeout", str(args.timeout - 10),
        "--label", "packaged-isolated",
    ]
    environment = dict(os.environ)
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-gpu"
    launched = time.time()
    completed = subprocess.run(command, env=environment, timeout=args.timeout, check=False)
    report = json.loads(child_output.read_text("utf-8"))
    if report.get("window_ready_wall_time", 0) < launched:
        raise SystemExit("The executable did not produce a fresh readiness report")
    report["executable"] = str(args.exe.resolve())
    report["exit_code"] = completed.returncode
    report["launch_to_window_ready_ms"] = (
        report["window_ready_wall_time"] - launched
    ) * 1000
    report["launch_scope"] = (
        "Process launch through isolated MainWindow construction/show; "
        "includes diagnostic fixture generation, excludes first document paint. "
        "Warm OS cache; not an installed first-run or cold-boot measurement."
    )
    warm = [sample for sample in report["samples"] if sample["warm"]]
    if warm and all(sample.get("rss_tree_bytes") is not None for sample in warm):
        start, end = warm[0]["rss_tree_bytes"], warm[-1]["rss_tree_bytes"]
        report["rss_warm_start_bytes"] = start
        report["rss_warm_end_bytes"] = end
        report["rss_warm_growth_percent"] = (end - start) / start * 100 if start else None
        sizes = [sample["rss_tree_bytes"] for sample in warm]
        early = statistics.median(sizes[:args.working_set])
        late = statistics.median(sizes[-args.working_set:])
        report["rss_warm_first_set_median_bytes"] = early
        report["rss_warm_last_set_median_bytes"] = late
        report["rss_warm_set_median_growth_percent"] = (late - early) / early * 100
        report["rss_warm_peak_bytes"] = max(sizes)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({
        "exit_code": completed.returncode, "passed": report.get("passed"),
        "launch_to_window_ready_ms": report["launch_to_window_ready_ms"],
        "summary": report.get("summary"),
        "rss_warm_growth_percent": report.get("rss_warm_growth_percent"),
    }, indent=2))
    raise SystemExit(0 if completed.returncode == 0 and report.get("passed") else 1)


if __name__ == "__main__":
    main()
