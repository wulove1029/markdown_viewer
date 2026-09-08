"""Repeatable baseline for "large Markdown first load" latency.

Covers docs/CLAUDE-CODE-NEXT-OPTIMIZATIONS.md section 3.A/3.B measurement
requirements: fixed-seed synthetic documents at 100 KB / 1 MB / 5 MB / 10 MB
across five content shapes, measured as (a) new-process cold open, (b)
same-process re-open, (c) same-process large-to-small switch, plus a direct
test of whether the parser lock in app/md_converter.py blocks a second,
unrelated conversion while one is already running.

Does not modify app/*. Where a timestamp the spec asks for is not exposed by
the current code (WebEngine "first visible paint", per-stage parser/anchor
split), this file adds the missing granularity by monkeypatching the already
public module attributes (``md_converter.read_text``, ``md_converter._wrap``,
``md_converter._PARSER.render``) from the outside, for the lifetime of this
process only. Search this file for "HOOK NEEDED" for what should become a
real hook in app/ for the next batch.

Usage:
    py -3 -X utf8 tools/benchmark_markdown_first_load.py --help
    py -3 -X utf8 tools/benchmark_markdown_first_load.py --quick
    py -3 -X utf8 tools/benchmark_markdown_first_load.py --output docs/benchmarks/first-load-baseline-2026-09-08.json
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import random
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SEED = 20260908
SIZES = {"100kb": 100_000, "1mb": 1_000_000, "5mb": 5_000_000, "10mb": 10_000_000}
ALL_CATEGORIES = ["long_prose", "code_heavy", "big_table", "math_mermaid", "long_paragraph"]
# Categories after long_prose are "syntax stress" cases (per spec 3.A): never
# averaged together with long_prose, always reported under their own key.
STRESS_CATEGORIES = {"code_heavy", "big_table", "math_mermaid", "long_paragraph"}
BASELINE_SMALL = ("long_prose", "100kb")  # the fixed "small file" used in switch/contention tests


# --------------------------------------------------------------------------
# Deterministic document generation
# --------------------------------------------------------------------------

_WORDS = (
    "system design markdown render cache parser thread lock queue worker "
    "signal event loop cancel generation preview document outline table "
    "diagram footnote anchor heading paragraph link image codec buffer "
    "stream window widget engine viewport theme session token block scope"
).split()


def _lorem(rng: random.Random, n_words: int) -> str:
    return " ".join(rng.choice(_WORDS) for _ in range(n_words))


def _gen_long_prose(rng: random.Random) -> str:
    parts = []
    for i in range(200):
        parts.append(f"## Section {i}\n\n")
        for _ in range(3):
            sentence = _lorem(rng, rng.randint(12, 24)).capitalize() + "."
            parts.append(sentence + " ")
        parts.append("\n\n")
        if i % 5 == 0:
            parts.append(f"> Note: {_lorem(rng, 10)}.\n\n")
        if i % 7 == 0:
            parts.append(f"- {_lorem(rng, 6)}\n- {_lorem(rng, 6)}\n- {_lorem(rng, 6)}\n\n")
    return "".join(parts)


_CODE_SNIPPETS = {
    "python": "def handler(event):\n    state = load_state(event.id)\n"
              "    for item in state.items:\n        if item.dirty:\n"
              "            item.flush()\n    return state\n",
    "javascript": "function onLoad(doc) {\n  const nodes = doc.querySelectorAll('.x');\n"
                  "  nodes.forEach((n) => { n.dataset.ready = '1'; });\n  return nodes.length;\n}\n",
    "bash": "#!/bin/bash\nset -euo pipefail\nfor f in *.md; do\n"
            "  echo \"processing $f\"\n  wc -l \"$f\"\ndone\n",
    "json": '{\n  "name": "sample",\n  "values": [1, 2, 3, 4, 5],\n  "nested": {"ok": true}\n}\n',
}


def _gen_code_heavy(rng: random.Random) -> str:
    # Snippets are repeated in-line (not once per markdown block) so a large
    # target size is reached with a bounded number of fenced blocks -- one
    # fence per ~2KB of code, not one fence per ~40 bytes. The latter (an
    # earlier version of this generator) produced >100k separate fences for a
    # 10 MB document and made Pygments per-block call overhead dominate,
    # which stress-tests call overhead rather than "a document with a lot of
    # code in it". Kept as a documented trade-off, not a silent fix.
    parts = []
    langs = list(_CODE_SNIPPETS)
    for i in range(150):
        lang = langs[i % len(langs)]
        parts.append(f"### Snippet {i} ({lang})\n\n")
        body = _CODE_SNIPPETS[lang] * 12 + f"# {_lorem(rng, 4)}\n"
        parts.append(f"```{lang}\n{body}```\n\n")
        parts.append(_lorem(rng, 15).capitalize() + ".\n\n")
    return "".join(parts)


def _gen_big_table(rng: random.Random) -> str:
    parts = ["# Large tables\n\n"]
    cols = 10
    for t in range(40):
        parts.append(f"## Table {t}\n\n")
        parts.append("| " + " | ".join(f"col{c}" for c in range(cols)) + " |\n")
        parts.append("|" + "---|" * cols + "\n")
        for r in range(80):
            parts.append(
                "| " + " | ".join(_lorem(rng, 2) for _ in range(cols)) + " |\n"
            )
        parts.append("\n")
    return "".join(parts)


def _gen_math_mermaid(rng: random.Random) -> str:
    parts = ["# Math and diagrams\n\n"]
    for i in range(120):
        parts.append(f"## Block {i}\n\n")
        parts.append(f"Inline math $a_{i} + b_{i} = c_{{{i}}}$ and text {_lorem(rng, 8)}.\n\n")
        parts.append(f"$$\\sum_{{k=0}}^{{{i}}} k^2 = \\frac{{n(n+1)(2n+1)}}{{6}}$$\n\n")
        parts.append(
            "```mermaid\ngraph TD\n"
            f"  A{i}[Start] --> B{i}{{Check}}\n"
            f"  B{i} -->|yes| C{i}[Continue]\n"
            f"  B{i} -->|no| D{i}[Stop]\n```\n\n"
        )
    return "".join(parts)


def _gen_long_paragraph(rng: random.Random) -> str:
    # A stress case for the "single huge block" edge case called out in
    # section 3.C: one heading, then one paragraph with no blank lines.
    words = []
    for _ in range(400):
        words.append(_lorem(rng, 40))
    return "# Single huge paragraph\n\n" + " ".join(words) + "\n"


_GENERATORS = {
    "long_prose": _gen_long_prose,
    "code_heavy": _gen_code_heavy,
    "big_table": _gen_big_table,
    "math_mermaid": _gen_math_mermaid,
    "long_paragraph": _gen_long_paragraph,
}


def generate_doc(category: str, size_bytes: int) -> str:
    """Deterministic text for (category, size_bytes), trimmed to exactly
    <= size_bytes UTF-8 bytes (a partial trailing multi-byte char, if any, is
    dropped, so the final size can be up to 3 bytes under the target)."""
    rng = random.Random(f"{SEED}:{category}:{size_bytes}")
    gen = _GENERATORS[category]
    text = gen(rng)
    encoded = text.encode("utf-8")
    while len(encoded) < size_bytes:
        text += gen(rng)
        encoded = text.encode("utf-8")
    encoded = encoded[:size_bytes]
    return encoded.decode("utf-8", errors="ignore")


def write_fixture(cache_dir: Path, category: str, size_key: str) -> Path:
    path = cache_dir / f"{category}-{size_key}.md"
    if not path.exists() or path.stat().st_size < SIZES[size_key] * 0.9:
        text = generate_doc(category, SIZES[size_key])
        path.write_bytes(text.encode("utf-8"))
    return path


# --------------------------------------------------------------------------
# Stats helper
# --------------------------------------------------------------------------

def summarize(samples_ms: list[float], note: str = "") -> dict:
    if not samples_ms:
        return {"n": 0, "note": note or "no samples"}
    ordered = sorted(samples_ms)
    n = len(ordered)
    p50 = statistics.median(ordered)
    # Nearest-rank p95: with n < 20 this is the same sample as max, so treat it
    # as a preliminary indicator only (see "p95_note").
    p95 = ordered[min(n - 1, int(round(n * 0.95)) if n > 1 else 0)]
    out = {
        "n": n,
        "p50_ms": p50,
        "p95_ms": p95,
        "p95_note": "nearest-rank; equals max when n < 20" if n < 20 else "nearest-rank",
        "max_ms": ordered[-1],
        "min_ms": ordered[0],
        "samples_ms": samples_ms,
    }
    if note:
        out["note"] = note
    return out


# --------------------------------------------------------------------------
# External instrumentation of app/md_converter (no edits to app/ itself)
# --------------------------------------------------------------------------

class ConverterInstrumentation:
    """Monkeypatches md_converter to record per-stage timings of the *next*
    convert() call. HOOK NEEDED: app/md_converter.py has no built-in timing
    hooks; render_body() conflates "parser end" and "HTML fragment produced"
    into one step (Pygments highlighting happens inline during md.render()),
    so body_render_ms below covers both -- a real implementation would split
    them so the D-section "loading feedback within 200ms" checkpoint can be
    measured precisely.
    """

    def __init__(self, md_converter_module):
        self.mc = md_converter_module
        self.last: dict = {}
        self._orig_read_text = md_converter_module.read_text
        self._orig_parser_render = md_converter_module._PARSER.render
        self._orig_render_body = md_converter_module.render_body
        self._orig_wrap = md_converter_module._wrap
        self._installed = False

    def install(self):
        if self._installed:
            return
        mc = self.mc

        def read_text(path):
            t0 = time.perf_counter()
            result = self._orig_read_text(path)
            self.last["decode_ms"] = (time.perf_counter() - t0) * 1000
            return result

        def parser_render(src, env=None):
            t0 = time.perf_counter()
            result = self._orig_parser_render(src, env)
            self.last["parser_ms"] = self.last.get("parser_ms", 0.0) + (
                time.perf_counter() - t0
            ) * 1000
            return result

        def render_body(text, *, cancel=None):
            t0 = time.perf_counter()
            result = self._orig_render_body(text, cancel=cancel)
            self.last["body_render_ms"] = (time.perf_counter() - t0) * 1000
            return result

        def wrap(*a, **kw):
            t0 = time.perf_counter()
            result = self._orig_wrap(*a, **kw)
            self.last["wrap_ms"] = (time.perf_counter() - t0) * 1000
            return result

        mc.read_text = read_text
        mc._PARSER.render = parser_render
        mc.render_body = render_body
        mc._wrap = wrap
        self._installed = True

    def uninstall(self):
        if not self._installed:
            return
        mc = self.mc
        mc.read_text = self._orig_read_text
        mc._PARSER.render = self._orig_parser_render
        mc.render_body = self._orig_render_body
        mc._wrap = self._orig_wrap
        self._installed = False

    def reset(self):
        self.last = {}


# --------------------------------------------------------------------------
# (a) cold subprocess open
# --------------------------------------------------------------------------

_SUBPROCESS_SCRIPT = r"""
import json, sys, time
from pathlib import Path
sys.path.insert(0, {root!r})
from app import md_converter

# Same lightweight stage-timing monkeypatch as ConverterInstrumentation,
# duplicated here so a single convert() call is timed exactly once -- calling
# read_text/render_body directly *and then* convert() would parse the file
# twice and misreport a doubled cost as "convert_total_ms".
timings = {{}}
_orig_read_text = md_converter.read_text
_orig_render_body = md_converter.render_body


def _read_text(path):
    t0 = time.perf_counter()
    result = _orig_read_text(path)
    timings["decode_ms"] = (time.perf_counter() - t0) * 1000
    return result


def _render_body(text, *, cancel=None):
    t0 = time.perf_counter()
    result = _orig_render_body(text, cancel=cancel)
    timings["body_render_ms"] = (time.perf_counter() - t0) * 1000
    return result


md_converter.read_text = _read_text
md_converter.render_body = _render_body

t_start = time.perf_counter()
path = Path({path!r})
t0 = time.perf_counter()
html, headings = md_converter.convert(path)
t3 = time.perf_counter()
timings["convert_total_ms"] = (t3 - t0) * 1000
timings["process_wall_ms"] = (t3 - t_start) * 1000
print(json.dumps(timings))
"""


def run_cold_subprocess(path: Path, timeout_s: float) -> dict | None:
    script = _SUBPROCESS_SCRIPT.format(root=str(ROOT), path=str(path))
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", "-c", script],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return {"timed_out": True, "timeout_s": timeout_s}
    wall_ms = (time.perf_counter() - t0) * 1000
    if proc.returncode != 0:
        return {"error": proc.stderr[-2000:], "returncode": proc.returncode}
    try:
        data = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"error": "unparseable stdout", "stdout": proc.stdout[-2000:]}
    data["subprocess_wall_ms"] = wall_ms
    return data


# --------------------------------------------------------------------------
# (b) same-process re-open, (c) large-to-small sequential switch
# --------------------------------------------------------------------------

def warm_reopen_once(md_converter, instr: ConverterInstrumentation, path: Path) -> dict:
    md_converter._CONVERT_CACHE.clear()
    instr.reset()
    t0 = time.perf_counter()
    md_converter.convert(path)
    total_ms = (time.perf_counter() - t0) * 1000
    out = dict(instr.last)
    out["convert_total_ms"] = total_ms
    return out


def sequential_switch_once(md_converter, instr: ConverterInstrumentation,
                            big_path: Path, small_path: Path) -> dict:
    md_converter._CONVERT_CACHE.clear()
    md_converter.convert(big_path)  # simulate "big file already open"
    instr.reset()
    t0 = time.perf_counter()
    md_converter.convert(small_path)
    total_ms = (time.perf_counter() - t0) * 1000
    out = dict(instr.last)
    out["switch_total_ms"] = total_ms
    return out


# --------------------------------------------------------------------------
# Section 3.B / item 5: does a running parser call block a new, unrelated one?
# --------------------------------------------------------------------------

def parser_lock_contention_once(md_converter, big_path: Path, small_path: Path) -> dict:
    """Start a big-file convert() on a background thread, then, once it is
    almost certainly holding the parser lock, request an unrelated small file
    from the *main* thread. Compares the small file's latency against a solo
    baseline to show whether the shared lock in md_converter._CONVERT_LOCK
    serializes unrelated work."""
    md_converter._CONVERT_CACHE.clear()

    big_started = threading.Event()
    big_done = threading.Event()
    big_elapsed = {}

    def run_big():
        big_started.set()
        t0 = time.perf_counter()
        md_converter.convert(big_path)
        big_elapsed["ms"] = (time.perf_counter() - t0) * 1000
        big_done.set()

    thread = threading.Thread(target=run_big, daemon=True)
    t_launch = time.perf_counter()
    thread.start()
    big_started.wait(2.0)
    time.sleep(0.01)  # let the big convert() acquire _CONVERT_LOCK first

    t_small_request = time.perf_counter()
    md_converter.convert(small_path)
    small_elapsed_ms = (time.perf_counter() - t_small_request) * 1000
    # Sampled here, *before* the join below: reading big_done after the join
    # would always report False and make the derived
    # "small_blocked_until_big_finished_fraction" a constant 1.0 regardless of
    # what the code under test does (fixed 2026-09-08, batch 3 review).
    big_running_at_small_return = not big_done.is_set()
    # Bounded, not the parser's own worst case: if the big convert() hasn't
    # finished in 60s something is wrong (all fixtures finish in low tens of
    # seconds), and we'd rather report "still running" honestly than hang.
    thread.join(timeout=60)

    return {
        "small_request_latency_ms": small_elapsed_ms,
        "big_convert_ms": big_elapsed.get("ms"),
        "big_started_before_small_request_ms": (t_small_request - t_launch) * 1000,
        "big_still_running_when_small_finished": big_running_at_small_return,
    }


# --------------------------------------------------------------------------
# GUI heartbeat while a background render runs
# --------------------------------------------------------------------------

def measure_gui_heartbeat(app, work_fn, tick_ms: int = 50) -> dict:
    from PySide6.QtCore import QTimer

    gaps = []
    last = [time.perf_counter()]
    timer = QTimer()
    timer.setInterval(tick_ms)

    def on_tick():
        now = time.perf_counter()
        gaps.append((now - last[0]) * 1000)
        last[0] = now

    timer.timeout.connect(on_tick)
    timer.start()

    done = threading.Event()

    def worker():
        work_fn()
        done.set()

    t0 = time.perf_counter()
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    while not done.is_set():
        app.processEvents()
        time.sleep(0.005)
    thread.join(timeout=60)
    timer.stop()
    total_ms = (time.perf_counter() - t0) * 1000
    return {
        "max_gap_ms": max(gaps) if gaps else None,
        "p95_gap_ms": sorted(gaps)[int(len(gaps) * 0.95)] if gaps else None,
        "tick_count": len(gaps),
        "total_ms": total_ms,
        "tick_interval_ms": tick_ms,
    }


# --------------------------------------------------------------------------
# WebEngine navigation + DOM-visible timing (best-effort; offscreen platform)
# --------------------------------------------------------------------------

def measure_webengine_load(app, html: str, reps: int) -> dict:
    try:
        from PySide6.QtWebEngineWidgets import QWebEngineView
        from PySide6.QtCore import QEventLoop, QTimer
    except Exception as exc:  # pragma: no cover - environment dependent
        return {"measured": False, "reason": f"QWebEngineView unavailable: {exc}"}

    samples_load = []
    samples_visible = []
    for _ in range(reps):
        view = QWebEngineView()
        loop = QEventLoop()
        state: dict = {}

        def on_load(ok, view=view, state=state):
            state["load_ms"] = (time.perf_counter() - state["t0"]) * 1000
            state["ok"] = ok

            def on_js(value, state=state):
                state["dom_text_len"] = value
                state["visible_ms"] = (time.perf_counter() - state["t0"]) * 1000
                loop.quit()

            view.page().runJavaScript("document.body ? document.body.innerText.length : -1", on_js)

        view.page().loadFinished.connect(on_load)

        def start(view=view, state=state):
            state["t0"] = time.perf_counter()
            view.setHtml(html)

        QTimer.singleShot(0, start)
        QTimer.singleShot(30000, loop.quit)  # guard against a hang
        loop.exec()
        if "load_ms" in state:
            samples_load.append(state["load_ms"])
        if "visible_ms" in state:
            samples_visible.append(state["visible_ms"])
        view.deleteLater()
        app.processEvents()

    return {
        "measured": True,
        "load_finished_ms": summarize(samples_load),
        "dom_visible_ms": summarize(samples_visible),
        "note": "offscreen platform; local setHtml() navigation, not a real "
                "window paint -- see docs for the limitation this leaves open.",
    }


# --------------------------------------------------------------------------
# Peak memory over repeated open/close
# --------------------------------------------------------------------------

def measure_memory_growth(md_converter, path: Path, iterations: int) -> dict:
    import psutil

    proc = psutil.Process()
    rss_samples = []
    for _ in range(iterations):
        md_converter._CONVERT_CACHE.clear()
        md_converter.convert(path)
        rss_samples.append(proc.memory_info().rss / (1024 * 1024))
    first_half = rss_samples[: len(rss_samples) // 2] or rss_samples
    second_half = rss_samples[len(rss_samples) // 2 :] or rss_samples
    return {
        "measure": "psutil RSS (MB), whole-process",
        "iterations": iterations,
        "rss_mb_samples": rss_samples,
        "rss_mb_first_half_avg": statistics.mean(first_half),
        "rss_mb_second_half_avg": statistics.mean(second_half),
        "rss_mb_max": max(rss_samples),
        "rss_mb_growth_mb": rss_samples[-1] - rss_samples[0],
    }


def measure_tracemalloc_peak(md_converter, path: Path) -> dict:
    import tracemalloc

    md_converter._CONVERT_CACHE.clear()
    tracemalloc.start()
    md_converter.convert(path)
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {
        "measure": "tracemalloc peak (Python-level allocations only; "
                   "excludes native/C-extension heaps)",
        "peak_mb": peak / (1024 * 1024),
    }


# --------------------------------------------------------------------------
# Environment info
# --------------------------------------------------------------------------

def environment_info() -> dict:
    info = {
        "python": sys.version,
        "platform": sys.platform,
        "platform_detail": platform.platform(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
        "qt_qpa_platform": os.environ.get("QT_QPA_PLATFORM"),
        "packaged": False,  # this tool always runs from source, never PyInstaller
    }
    try:
        import PySide6
        info["pyside6"] = PySide6.__version__
        from PySide6.QtCore import qVersion
        info["qt"] = qVersion()
    except Exception as exc:  # pragma: no cover
        info["pyside6_error"] = str(exc)
    return info


# --------------------------------------------------------------------------
# CLI / orchestration
# --------------------------------------------------------------------------

# Default to a timestamped file so a bare run never overwrites a committed baseline.
DEFAULT_OUTPUT = ROOT / "docs" / "benchmarks" / (
    "first-load-" + time.strftime("%Y%m%d-%H%M%S") + ".json"
)


# --------------------------------------------------------------------------
# Added for batch 3 (2026-09-08 "large markdown render"): these measure the
# NEW code paths (fast text prefix, killable render worker). Nothing above is
# modified, so before/after numbers for the original keys stay comparable.
# --------------------------------------------------------------------------

def first_readable_once(md_converter, path: Path) -> dict:
    """Time what the reader actually sees first.

    Mirrors app/renderer.py::_MarkdownRenderWorker: at or above
    FAST_PREVIEW_MIN_BYTES the worker renders a block-boundary prefix and shows
    it labelled as partial; below it the first thing shown is the full render.
    """
    md_converter._CONVERT_CACHE.clear()
    size = path.stat().st_size
    mode = "full_render"
    covered = 1.0
    t0 = time.perf_counter()
    if size >= md_converter.FAST_PREVIEW_MIN_BYTES:
        result = md_converter.read_text(path)
        partial = None
        if result is not None:
            partial = md_converter.render_partial_body(result[0], size)
        if partial is not None:
            md_converter.wrap_body(partial[0], path.stem, "light")
            mode = "fast_text_prefix"
            covered = partial[1] / size
        else:
            md_converter.convert(path)
            mode = "full_render_no_safe_prefix"
    else:
        md_converter.convert(path)
    return {"first_readable_ms": (time.perf_counter() - t0) * 1000,
            "mode": mode, "covered_fraction": covered}


def cancel_switch_once(md_converter, big_path: Path, small_path: Path,
                       delay_s: float = 0.3) -> dict:
    """Cancel a big render mid-flight and open a small file immediately.

    This is the spec 3.B / 3.D case: the stale document must stop consuming the
    parser, and the newly requested small file must not queue behind it.
    """
    md_converter._CONVERT_CACHE.clear()
    try:
        from app import render_service
        killed_before = render_service.service().killed
    except Exception:
        render_service = None
        killed_before = None
    cancel = threading.Event()
    started = threading.Event()
    out: dict = {}

    def run_big():
        started.set()
        t0 = time.perf_counter()
        try:
            md_converter.convert(big_path, cancel=cancel)
            out["outcome"] = "completed"
        except md_converter.RenderCancelled:
            out["outcome"] = "cancelled"
        except Exception as exc:
            out["outcome"] = f"error: {exc}"
        out["returned_at"] = time.perf_counter()

    thread = threading.Thread(target=run_big, daemon=True)
    thread.start()
    started.wait(2.0)
    time.sleep(delay_s)
    t_cancel = time.perf_counter()
    cancel.set()
    t_small = time.perf_counter()
    md_converter.convert(small_path)
    small_ms = (time.perf_counter() - t_small) * 1000
    thread.join(timeout=60)
    returned = out.get("returned_at")
    return {
        "small_request_latency_ms": small_ms,
        "big_outcome": out.get("outcome"),
        "big_stop_after_cancel_ms": (returned - t_cancel) * 1000 if returned else None,
        "big_still_running_when_small_finished": returned is None,
        "render_worker_processes_killed": (
            None if killed_before is None
            else render_service.service().killed - killed_before
        ),
    }


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    p.add_argument("--tmp-dir", type=Path, default=ROOT / "tmp" / "bench")
    p.add_argument("--sizes", default="100kb,1mb,5mb,10mb",
                    help="comma list from: " + ",".join(SIZES))
    p.add_argument("--categories", default=",".join(ALL_CATEGORIES),
                    help="comma list from: " + ",".join(ALL_CATEGORIES))
    p.add_argument("--warm-reps", type=int, default=10)
    p.add_argument("--switch-reps", type=int, default=5)
    p.add_argument("--contention-reps", type=int, default=5)
    p.add_argument("--gui-reps", type=int, default=3)
    p.add_argument("--webengine-reps", type=int, default=3)
    p.add_argument("--memory-iterations", type=int, default=20)
    p.add_argument("--first-readable-reps", type=int, default=5,
                    help="reps for the new fast-text-reading first-screen metric")
    p.add_argument("--cancel-switch-reps", type=int, default=3,
                    help="reps for cancelling a big render and opening a small file")
    p.add_argument("--cold-timeout-s", type=float, default=180.0)
    p.add_argument("--skip-cold", action="store_true")
    p.add_argument("--skip-webengine", action="store_true")
    p.add_argument("--skip-gui", action="store_true")
    p.add_argument("--skip-memory", action="store_true")
    p.add_argument(
        "--quick", action="store_true",
        help="Small, fast run (100kb+1mb, 2 categories, few reps); "
             "designed to finish within ~5 minutes.",
    )
    return p


def apply_quick_profile(args):
    if not args.quick:
        return
    args.sizes = "100kb,1mb"
    args.categories = "long_prose,big_table"
    args.warm_reps = 3
    args.switch_reps = 2
    args.contention_reps = 2
    args.gui_reps = 1
    args.webengine_reps = 1
    args.memory_iterations = 5
    args.cold_timeout_s = 60.0
    args.first_readable_reps = 2
    args.cancel_switch_reps = 1


def main():
    args = build_arg_parser().parse_args()
    apply_quick_profile(args)

    sizes = [s.strip() for s in args.sizes.split(",") if s.strip()]
    categories = [c.strip() for c in args.categories.split(",") if c.strip()]
    for s in sizes:
        if s not in SIZES:
            raise SystemExit(f"unknown size key: {s}")
    for c in categories:
        if c not in _GENERATORS:
            raise SystemExit(f"unknown category: {c}")

    args.tmp_dir.mkdir(parents=True, exist_ok=True)

    print(f"[1/6] Generating fixtures into {args.tmp_dir} ...", file=sys.stderr)
    fixtures: dict[tuple[str, str], Path] = {}
    for category in categories:
        for size_key in sizes:
            fixtures[(category, size_key)] = write_fixture(args.tmp_dir, category, size_key)
    # Always ensure the fixed small baseline exists, even if not in --categories/--sizes.
    baseline_cat, baseline_size = BASELINE_SMALL
    if (baseline_cat, baseline_size) not in fixtures:
        fixtures[BASELINE_SMALL] = write_fixture(args.tmp_dir, baseline_cat, baseline_size)

    report: dict = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "environment": environment_info(),
        "config": vars(args) | {"output": str(args.output), "tmp_dir": str(args.tmp_dir)},
        "seed": SEED,
        "sizes_bytes": SIZES,
        "stress_categories": sorted(STRESS_CATEGORIES),
        "baseline_small_case": list(BASELINE_SMALL),
        "cold_subprocess": {},
        "warm_reopen": {},
        "sequential_switch": {},
        "parser_lock_contention": {},
        "first_readable": {},
        "cancel_switch": {},
        "gui_heartbeat": {},
        "webengine": {},
        "memory": {},
        "limitations": [
            "Cold subprocess timings may hit the OS file-system cache from "
            "this same benchmark run's earlier fixture writes/reads; not a "
            "true cold-disk measurement.",
            "QT_QPA_PLATFORM=offscreen is used throughout; WebEngine numbers "
            "reflect local setHtml() navigation on a headless surface, not a "
            "real on-screen first paint -- needs a manual Windows check.",
            "body_render_ms conflates markdown-it parsing and Pygments/anchor "
            "HTML generation because app/md_converter.py has no internal stage "
            "boundary between them (see ConverterInstrumentation docstring).",
            "This tool never imports app.renderer's QThreadPool worker "
            "directly to avoid depending on GUI wiring under change; GUI "
            "heartbeat/contention tests reproduce the same shared "
            "_CONVERT_LOCK usage pattern instead.",
        ],
    }

    from app import md_converter

    instr = ConverterInstrumentation(md_converter)
    instr.install()

    # ---- (a) cold subprocess ----
    if not args.skip_cold:
        print("[2/6] Cold subprocess opens (one run each; OS cache may be warm)...", file=sys.stderr)
        for (category, size_key), path in fixtures.items():
            if category not in categories or size_key not in sizes:
                continue
            key = f"{category}/{size_key}"
            print(f"  cold: {key}", file=sys.stderr)
            report["cold_subprocess"][key] = run_cold_subprocess(path, args.cold_timeout_s)

    # ---- (b) warm re-open ----
    print("[3/6] Warm same-process re-open...", file=sys.stderr)
    for (category, size_key), path in fixtures.items():
        if category not in categories or size_key not in sizes:
            continue
        reps = args.warm_reps
        if size_key == "10mb":
            reps = min(reps, 3)
        elif size_key == "5mb":
            reps = min(reps, 5)
        key = f"{category}/{size_key}"
        print(f"  warm: {key} x{reps}", file=sys.stderr)
        samples = []
        decode_samples = []
        body_samples = []
        # Per-case wall-clock budget so one pathological case (e.g. a code-heavy
        # document with thousands of Pygments calls) cannot silently blow the
        # whole run past the operator's patience; a case that hits this returns
        # fewer than the requested reps, and n in the summary says so honestly.
        deadline = time.time() + max(60.0, reps * 120.0)
        for _ in range(reps):
            if time.time() > deadline:
                break
            result = warm_reopen_once(md_converter, instr, path)
            samples.append(result["convert_total_ms"])
            if "decode_ms" in result:
                decode_samples.append(result["decode_ms"])
            if "body_render_ms" in result:
                body_samples.append(result["body_render_ms"])
        report["warm_reopen"][key] = {
            "convert_total_ms": summarize(samples),
            "decode_ms": summarize(decode_samples),
            "body_render_ms": summarize(body_samples),
        }

    # ---- (c) sequential large -> small switch ----
    print("[4/6] Sequential large-to-small switch...", file=sys.stderr)
    small_path = fixtures[BASELINE_SMALL]
    for (category, size_key), path in fixtures.items():
        if size_key == baseline_size and category == baseline_cat:
            continue
        if category not in categories or size_key not in sizes:
            continue
        reps = args.switch_reps
        if size_key in ("5mb", "10mb"):
            reps = min(reps, 3)
        key = f"{category}/{size_key}_to_{'/'.join(BASELINE_SMALL)}"
        print(f"  switch: {key} x{reps}", file=sys.stderr)
        samples = []
        deadline = time.time() + max(60.0, reps * 90.0)
        for _ in range(reps):
            if time.time() > deadline:
                break
            result = sequential_switch_once(md_converter, instr, path, small_path)
            samples.append(result["switch_total_ms"])
        report["sequential_switch"][key] = summarize(samples)
    # Baseline: opening the small file alone, no prior large file in-process.
    solo_samples = []
    for _ in range(args.switch_reps):
        md_converter._CONVERT_CACHE.clear()
        t0 = time.perf_counter()
        md_converter.convert(small_path)
        solo_samples.append((time.perf_counter() - t0) * 1000)
    report["sequential_switch"]["solo_baseline_" + "/".join(BASELINE_SMALL)] = summarize(solo_samples)

    instr.uninstall()

    # ---- parser lock contention ----
    print("[5/6] Parser lock contention (does a running parse block a new one?)...", file=sys.stderr)
    contention_targets = [
        (c, s) for (c, s) in fixtures if s in ("5mb", "10mb") and c == "long_prose"
        and c in categories and s in sizes
    ]
    for category, size_key in contention_targets:
        big_path = fixtures[(category, size_key)]
        key = f"{category}/{size_key}_blocks_{'/'.join(BASELINE_SMALL)}"
        samples = []
        for rep in range(args.contention_reps):
            print(f"  contention: {key} rep {rep + 1}/{args.contention_reps}", file=sys.stderr)
            samples.append(parser_lock_contention_once(md_converter, big_path, small_path))
        latencies = [s["small_request_latency_ms"] for s in samples]
        still_running = [s["big_still_running_when_small_finished"] for s in samples]
        report["parser_lock_contention"][key] = {
            "small_request_latency_ms": summarize(latencies),
            "small_blocked_until_big_finished_fraction": (
                sum(1 for x in still_running if not x) / len(still_running) if still_running else None
            ),
            "raw_samples": samples,
        }
    # Baseline solo small-file latency for comparison (no contention).
    solo2 = []
    for _ in range(args.contention_reps):
        md_converter._CONVERT_CACHE.clear()
        t0 = time.perf_counter()
        md_converter.convert(small_path)
        solo2.append((time.perf_counter() - t0) * 1000)
    report["parser_lock_contention"]["solo_baseline_" + "/".join(BASELINE_SMALL)] = summarize(solo2)

    # ---- first readable content + cancel/switch (batch 3 additions) ----
    print("[5b/6] First readable content + cancel-and-switch...", file=sys.stderr)
    for category, size_key in sorted(fixtures):
        if category not in categories or size_key not in sizes:
            continue
        path = fixtures[(category, size_key)]
        key = f"{category}/{size_key}"
        samples, meta = [], {}
        for _ in range(max(1, args.first_readable_reps)):
            result = first_readable_once(md_converter, path)
            samples.append(result["first_readable_ms"])
            meta = result
        report["first_readable"][key] = summarize(samples) | {
            "mode": meta.get("mode"),
            "covered_fraction": meta.get("covered_fraction"),
        }
    cancel_targets = [
        (c, s) for (c, s) in fixtures if s in ("5mb", "10mb") and c == "long_prose"
        and c in categories and s in sizes
    ]
    for category, size_key in cancel_targets:
        big_path = fixtures[(category, size_key)]
        key = f"{category}/{size_key}_cancelled_then_{'/'.join(BASELINE_SMALL)}"
        runs = []
        for rep in range(max(1, args.cancel_switch_reps)):
            print(f"  cancel-switch: {key} rep {rep + 1}", file=sys.stderr)
            runs.append(cancel_switch_once(md_converter, big_path, small_path))
        report["cancel_switch"][key] = {
            "small_request_latency_ms": summarize(
                [r["small_request_latency_ms"] for r in runs]),
            "big_stop_after_cancel_ms": summarize(
                [r["big_stop_after_cancel_ms"] for r in runs
                 if r["big_stop_after_cancel_ms"] is not None]),
            "raw_samples": runs,
        }

    # ---- GUI heartbeat + WebEngine (needs QApplication) ----
    if not args.skip_gui or not args.skip_webengine:
        print("[6/6] GUI heartbeat / WebEngine...", file=sys.stderr)
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])

        if not args.skip_gui:
            gui_targets = [k for k in fixtures if k[1] in ("5mb", "10mb") and k[0] == "long_prose"
                           and k[0] in categories and k[1] in sizes]
            for category, size_key in gui_targets:
                path = fixtures[(category, size_key)]
                key = f"{category}/{size_key}"
                runs = []
                for _ in range(args.gui_reps):
                    md_converter._CONVERT_CACHE.clear()
                    runs.append(measure_gui_heartbeat(app, lambda p=path: md_converter.convert(p)))
                gaps = [r["max_gap_ms"] for r in runs if r["max_gap_ms"] is not None]
                report["gui_heartbeat"][key] = {
                    "max_gap_ms": summarize(gaps),
                    "tick_interval_ms": 50,
                    "raw_runs": runs,
                }

        if not args.skip_webengine:
            we_targets = [k for k in fixtures if k[0] == "long_prose" and k[0] in categories
                          and k[1] in sizes]
            for category, size_key in we_targets:
                path = fixtures[(category, size_key)]
                key = f"{category}/{size_key}"
                html, _headings = md_converter.convert(path)
                report["webengine"][key] = measure_webengine_load(app, html, args.webengine_reps)

    # ---- memory ----
    if not args.skip_memory:
        print("[memory] Peak RSS + tracemalloc on repeated open/close...", file=sys.stderr)
        mem_target = fixtures.get(("long_prose", "5mb")) or fixtures.get(("long_prose", sizes[-1]))
        if mem_target is not None:
            report["memory"]["repeated_open_close"] = measure_memory_growth(
                md_converter, mem_target, args.memory_iterations
            )
            report["memory"]["tracemalloc_single_convert"] = measure_tracemalloc_peak(
                md_converter, mem_target
            )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"Wrote {args.output}", file=sys.stderr)

    def _brief(v):
        if isinstance(v, dict):
            return {k: _brief(x) for k, x in v.items() if k not in ("samples_ms", "raw_samples", "raw_runs")}
        return v

    print(json.dumps(_brief(report), indent=2, default=str))
    sys.stdout.flush()
    sys.stderr.flush()
    # QWebEngineView's render-process teardown segfaults on interpreter exit
    # in this offscreen/Windows combination (observed after --webengine runs,
    # after all output was already written); skip Python/Qt exit-time
    # cleanup entirely, same workaround as tools/benchmark_layout.py.
    os._exit(0)


if __name__ == "__main__":
    main()
