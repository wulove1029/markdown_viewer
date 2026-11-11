"""Headless command-line export (F1b).

``MarkdownViewer.exe --export <docx|pptx|pdf|html> <in.md> <out>`` converts one
Markdown file without opening any window and never talks to a running Viewer
instance. main.py dispatches here before any Qt import and before the
single-instance IPC gate.

Exit codes: 0 ok, 2 usage error, 3 bad input/output path, 4 export failed or
timed out. The packaged build is console=False, so stdout/stderr may be None;
every print goes through :func:`_emit`.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_INPUT = 3
EXIT_FAILED = 4

FORMATS = ("docx", "pptx", "pdf", "html")
JOB_TIMEOUT_S = 120
SCHEMA = 1

CLI_FLAGS = ("--export", "--export-batch", "--capabilities", "--version")

_EXIT_CODES = {
    "0": "success",
    "2": "usage error (bad arguments, unsupported format, unknown option)",
    "3": "input missing / not .md or .markdown / output equals input",
    "4": "export failed or timed out",
}

_OPTS_WITH_VALUE = ("--log", "--result-json")


class CliError(Exception):
    def __init__(self, code: int, message: str, opts: dict[str, str] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.opts: dict[str, str] = opts or {}


def _emit(text: str) -> None:
    stream = sys.stdout
    if stream is None:
        return
    try:
        stream.write(text + "\n")
        stream.flush()
    except (OSError, ValueError, UnicodeError):
        pass


def _version() -> str:
    from app.version import VERSION

    return VERSION


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _write_json(path_str: str | None, payload: dict[str, Any]) -> None:
    """UTF-8 without BOM. A failing result file must never change the exit code."""
    if not path_str:
        return
    try:
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        _write_atomic(Path(path_str), data)
    except OSError:
        pass


class _Log:
    """Append-only UTF-8 (no BOM) progress log; a no-op without ``--log``."""

    def __init__(self, path_str: str | None):
        self._fh = None
        self._handler = None
        if not path_str:
            return
        try:
            p = Path(path_str)
            p.parent.mkdir(parents=True, exist_ok=True)
            self._fh = open(p, "w", encoding="utf-8", newline="\n")
        except OSError:
            self._fh = None
            return
        import logging

        self._handler = logging.StreamHandler(self._fh)
        self._handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        root = logging.getLogger()
        root.setLevel(logging.INFO)
        root.addHandler(self._handler)

    def write(self, message: str) -> None:
        if self._fh is None:
            return
        try:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            self._fh.write(f"{stamp} {message}\n")
            self._fh.flush()
        except (OSError, ValueError):
            pass

    def close(self) -> None:
        if self._handler is not None:
            import logging

            logging.getLogger().removeHandler(self._handler)
            self._handler = None
        if self._fh is not None:
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None


# ---------------------------------------------------------------------------
# Argument parsing / validation (Qt-free)


def parse_args(argv: list[str], positionals: int) -> tuple[list[str], dict[str, str]]:
    """Split *argv* into exactly *positionals* values and known ``--opt value`` pairs."""
    pos: list[str] = []
    opts: dict[str, str] = {}
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg.startswith("--"):
            name, eq, inline = arg.partition("=")
            if name not in _OPTS_WITH_VALUE:
                raise CliError(EXIT_USAGE, f"unknown option: {name}", opts)
            if name in opts:
                raise CliError(EXIT_USAGE, f"duplicate option: {name}", opts)
            if eq:
                value = inline
            else:
                i += 1
                if i >= len(argv):
                    raise CliError(EXIT_USAGE, f"option {name} needs a value", opts)
                value = argv[i]
            if not value:
                raise CliError(EXIT_USAGE, f"option {name} needs a value", opts)
            opts[name] = value
        else:
            pos.append(arg)
        i += 1
    if len(pos) != positionals:
        raise CliError(
            EXIT_USAGE, f"expected {positionals} positional argument(s), got {len(pos)}", opts
        )
    return pos, opts


def _norm(path: Path) -> str:
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path.absolute()
    return os.path.normcase(str(resolved))


def validate_job(fmt: str, input_str: str, output_str: str) -> tuple[Path, Path]:
    from app.file_types import is_markdown

    if fmt not in FORMATS:
        raise CliError(EXIT_USAGE, f"unsupported format: {fmt!r} (use {'|'.join(FORMATS)})")
    src = Path(input_str)
    dst = Path(output_str)
    if not src.is_file():
        raise CliError(EXIT_INPUT, f"input not found: {src}")
    if not is_markdown(src):
        raise CliError(EXIT_INPUT, f"input must be .md or .markdown: {src}")
    if _norm(src) == _norm(dst):
        raise CliError(EXIT_INPUT, "output path equals input path")
    if dst.is_dir():
        raise CliError(EXIT_INPUT, f"output is a directory: {dst}")
    return Path(os.path.abspath(src)), Path(os.path.abspath(dst))


# ---------------------------------------------------------------------------
# Qt side (imported lazily; only export modes reach this)

_profile_dir: str | None = None


def _ensure_app():
    """QApplication without any window, plus a private WebEngine profile."""
    global _profile_dir
    from app.url_schemes import register_document_schemes

    register_document_schemes()
    from PySide6.QtWebEngineCore import QWebEngineProfile
    from PySide6.QtWidgets import QApplication

    from app.settings_store import APP, DISPLAY_NAME, ORG

    app = QApplication.instance()
    if app is None:
        app = QApplication([sys.argv[0] if sys.argv else "MarkdownViewer"])
    app.setApplicationName(APP)
    app.setApplicationDisplayName(DISPLAY_NAME)
    app.setApplicationVersion(_version())
    app.setOrganizationName(ORG)
    app.setQuitOnLastWindowClosed(False)
    if _profile_dir is None:
        # Never share the default on-disk Chromium profile with a running GUI.
        _profile_dir = tempfile.mkdtemp(prefix="mdv_cli_")
        profile = QWebEngineProfile.defaultProfile()
        profile.setPersistentStoragePath(os.path.join(_profile_dir, "storage"))
        profile.setCachePath(os.path.join(_profile_dir, "cache"))
    return app


def _flush_deferred_deletes() -> None:
    """Carry out pending ``deleteLater()`` calls now.

    The CLI never runs a top-level event loop, and ``processEvents()`` does not
    deliver DeferredDelete events posted outside one. Without this the export's
    WebEngine view and pages outlive their job and are still alive when
    QApplication is destroyed at interpreter exit ("Release of profile requested
    but WebEnginePage still not deleted"); under QT_QPA_PLATFORM=offscreen that
    teardown crashes with an access violation after a successful export.
    """
    if "PySide6.QtCore" not in sys.modules:
        return
    from PySide6.QtCore import QCoreApplication, QEvent

    if QCoreApplication.instance() is None:
        return
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QCoreApplication.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _cleanup_profile() -> None:
    global _profile_dir
    # Chromium must let go of every page before its profile directory goes.
    _flush_deferred_deletes()
    if _profile_dir:
        shutil.rmtree(_profile_dir, ignore_errors=True)
        _profile_dir = None


def _run_snapshot(text: str, src: Path, tmp: Path, pdf: bool, timeout_s: float) -> bool:
    """Render *text* in a never-shown RendererView and print it to PDF or HTML."""
    from PySide6.QtCore import QEventLoop, QObject, QTimer, QUrl

    from . import export_actions
    from .renderer import RendererView

    class Job(QObject):
        def __init__(self) -> None:
            super().__init__()
            self.ok = False
            self.done = False
            self.checking = False
            self.loop = QEventLoop()
            self.view = RendererView()
            self.view.hide()
            self.view.resize(900, 700)
            self.base_url = QUrl.fromLocalFile(str(src.resolve().parent) + "/")
            self.poll = QTimer(self)
            self.poll.setInterval(50)
            self.poll.timeout.connect(self.poll_ready)
            self.limit = QTimer(self)
            self.limit.setSingleShot(True)
            self.limit.timeout.connect(lambda: self.finish(False))

        def start(self) -> None:
            self.view.render_markdown_text(
                text, theme="light", title=src.stem, base_url=self.base_url,
                defer_code=False,
            )
            self.limit.start(int(timeout_s * 1000))
            self.poll.start()

        def poll_ready(self) -> None:
            if self.done or self.checking:
                return
            self.checking = True
            generation = self.view._render_generation
            self.view.page().runJavaScript(
                export_actions.snapshot_ready_js(generation), self.on_ready
            )

        def on_ready(self, value: Any) -> None:
            self.checking = False
            if self.done:
                return
            if value == -1 or self.view._render_failed_generation is not None:
                self.finish(False)
                return
            if value != 1:
                return
            self.poll.stop()
            if pdf:
                layout = export_actions.pdf_layout("A4", "portrait")
                try:
                    self.view.export_pdf(
                        str(tmp), lambda _p, ok: self.finish(bool(ok)), layout
                    )
                except (RuntimeError, TypeError):
                    self.finish(False)
            else:
                self.view.page().toHtml(self.write_html)

        def write_html(self, value: Any) -> None:
            if self.done:
                return
            if not isinstance(value, str) or not value:
                self.finish(False)
                return
            value = export_actions.html_with_base(value, self.base_url)
            try:
                tmp.write_text(value, encoding="utf-8")
            except OSError:
                self.finish(False)
                return
            self.finish(True)

        def finish(self, ok: bool) -> None:
            if self.done:
                return
            self.done = True
            self.ok = ok
            self.poll.stop()
            self.limit.stop()
            self.loop.quit()

        def run(self) -> bool:
            self.start()
            self.loop.exec()
            ok = self.ok
            self.view.deactivate()
            self.view.deleteLater()
            self.deleteLater()
            # The view (and its page) must be gone before QApplication is.
            _flush_deferred_deletes()
            return ok

    return Job().run()


def _warn(warnings: list[str], log: _Log, message: str) -> None:
    """Record a degraded-output warning in the result, the log and stdout."""
    warnings.append(message)
    log.write(f"warning: {message}")
    _emit(f"warning: {message}")


def _run_office(
    fmt: str, text: str, src: Path, tmp: Path, warnings: list[str], log: _Log
) -> None:
    """docx/pptx through the same exporters (and fragment renderer) the GUI uses."""
    renderer = None
    inner: Callable[[str, str], Any] | None = None
    try:
        from .fragment_render import FragmentRenderer

        renderer = FragmentRenderer()
        inner = renderer.provide
    except Exception as exc:
        renderer = None
        _warn(warnings, log, f"fragment renderer unavailable: {exc}")

    # Always pass a provider, so a fragment that could not be rendered is
    # reported and labelled as such in the document, never silently dropped.
    def provider(kind: str, source: str) -> Any:
        result = inner(kind, source) if inner is not None else None
        if result is None:
            _warn(warnings, log, f"{kind} fragment not rendered; source text kept")
        return result

    try:
        if fmt == "docx":
            from .docx_export import export_markdown_to_docx

            export_markdown_to_docx(
                text, str(tmp), base_dir=src.parent, image_provider=provider
            )
        else:
            from .pptx_export import export_markdown_to_pptx

            export_markdown_to_pptx(
                text, str(tmp), base_dir=src.parent, image_provider=provider
            )
    finally:
        if renderer is not None:
            renderer.cleanup()
            _flush_deferred_deletes()


def export_one(
    fmt: str, src: Path, dst: Path, log: _Log, warnings: list[str]
) -> None:
    """Export *src* to *dst* (temp file + atomic rename). Raises CliError(4)."""
    from app.md_converter import read_text

    log.write(f"export {fmt}: {src} -> {dst}")
    _ensure_app()
    tmp = dst.with_name(f".{dst.name}.{os.getpid()}.tmp")
    try:
        read = read_text(src)
        if read is None:
            raise CliError(EXIT_FAILED, "could not decode input file")
        text = read[0]
        dst.parent.mkdir(parents=True, exist_ok=True)
        if fmt in ("docx", "pptx"):
            _run_office(fmt, text, src, tmp, warnings, log)
        else:
            ok = _run_snapshot(text, src, tmp, fmt == "pdf", JOB_TIMEOUT_S)
            if not ok:
                raise CliError(EXIT_FAILED, f"{fmt} rendering failed or timed out")
        if not tmp.is_file() or tmp.stat().st_size == 0:
            raise CliError(EXIT_FAILED, "export produced an empty file")
        os.replace(tmp, dst)
        log.write(f"done {fmt}: {dst} ({dst.stat().st_size} bytes)")
    except CliError:
        raise
    except Exception as exc:
        raise CliError(EXIT_FAILED, f"{type(exc).__name__}: {exc}") from exc
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


class _Watchdog:
    """Hard per-job deadline: synchronous exporters cannot be interrupted."""

    def __init__(self, seconds: float, on_timeout: Callable[[], None]):
        self._lock = threading.Lock()
        self._disarmed = False
        self._on_timeout = on_timeout
        self._timer = threading.Timer(seconds, self._fire)
        self._timer.daemon = True

    def arm(self) -> None:
        self._timer.start()

    def disarm(self) -> None:
        with self._lock:
            self._disarmed = True
        self._timer.cancel()

    def _fire(self) -> None:
        with self._lock:
            if self._disarmed:
                return
            self._disarmed = True
        self._on_timeout()


def _job_result(
    fmt: str, src: str, dst: str, code: int, warnings: list[str],
    error: str | None, started: float,
) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "ok": code == EXIT_OK,
        "exitCode": code,
        "format": fmt,
        "input": src,
        "output": dst,
        "warnings": list(warnings),
        "error": error,
        "elapsedMs": int((time.monotonic() - started) * 1000),
        "version": _version(),
    }


def _watchdog_seconds(fmt: str) -> float:
    # PDF/HTML enforce JOB_TIMEOUT_S in their own event loop first.
    return JOB_TIMEOUT_S + (15 if fmt in ("pdf", "html") else 0)


def _fail_hard(code: int, payload: dict[str, Any], result_path: str | None, log: _Log) -> None:
    log.write(f"fatal: {payload.get('error')}")
    _write_json(result_path, payload)
    log.close()
    os._exit(code)


def _run_single(argv: list[str]) -> int:
    started = time.monotonic()
    opts: dict[str, str] = {}
    fmt, src_s, dst_s = "", "", ""
    log = _Log(None)
    warnings: list[str] = []
    code = EXIT_OK
    error: str | None = None
    try:
        pos, opts = parse_args(argv, 3)
        fmt, src_s, dst_s = pos[0].lower(), pos[1], pos[2]
        log = _Log(opts.get("--log"))
        src, dst = validate_job(fmt, src_s, dst_s)
        src_s, dst_s = str(src), str(dst)
        result_path = opts.get("--result-json")
        dog = _Watchdog(
            _watchdog_seconds(fmt),
            lambda: _fail_hard(
                EXIT_FAILED,
                _job_result(fmt, src_s, dst_s, EXIT_FAILED, warnings,
                            f"timed out after {JOB_TIMEOUT_S}s", started),
                result_path, log,
            ),
        )
        dog.arm()
        try:
            export_one(fmt, src, dst, log, warnings)
        finally:
            dog.disarm()
    except CliError as exc:
        code, error = exc.code, exc.message
        opts = exc.opts or opts
        if "--log" in opts and log._fh is None:
            log = _Log(opts["--log"])
    except Exception as exc:  # pragma: no cover - defensive
        code, error = EXIT_FAILED, f"{type(exc).__name__}: {exc}"
    if error:
        log.write(f"error ({code}): {error}")
        _emit(f"error: {error}")
    payload = _job_result(fmt, src_s, dst_s, code, warnings, error, started)
    _write_json(opts.get("--result-json"), payload)
    log.close()
    _cleanup_profile()
    return code


def _run_batch(argv: list[str]) -> int:
    started = time.monotonic()
    opts: dict[str, str] = {}
    log = _Log(None)
    results: list[dict[str, Any]] = []
    code = EXIT_OK
    error: str | None = None
    try:
        pos, opts = parse_args(argv, 1)
        log = _Log(opts.get("--log"))
        manifest_path = Path(pos[0])
        if not manifest_path.is_file():
            raise CliError(EXIT_INPUT, f"manifest not found: {manifest_path}")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
            jobs = manifest["jobs"]
            if not isinstance(jobs, list) or not jobs:
                raise ValueError("jobs must be a non-empty list")
            for job in jobs:
                for key in ("format", "input", "output"):
                    if not isinstance(job[key], str):
                        raise ValueError(f"job.{key} must be a string")
        except (ValueError, KeyError, TypeError, OSError) as exc:
            raise CliError(EXIT_USAGE, f"invalid manifest: {exc}", opts) from exc

        result_path = opts.get("--result-json")
        state: dict[str, Any] = {"index": 0}

        def timed_out() -> None:
            i = state["index"]
            partial = list(results)
            for k in range(i, len(jobs)):
                err = (f"timed out after {JOB_TIMEOUT_S}s" if k == i
                       else "skipped after a previous job timed out")
                partial.append(_job_result(
                    str(jobs[k]["format"]).lower(), jobs[k]["input"], jobs[k]["output"],
                    EXIT_FAILED, [], err, started))
            _fail_hard(EXIT_FAILED, _batch_payload(partial, EXIT_FAILED, started,
                                                   "batch job timed out"), result_path, log)

        for i, job in enumerate(jobs):
            state["index"] = i
            fmt = job["format"].lower()
            job_started = time.monotonic()
            warnings: list[str] = []
            jerr: str | None = None
            jcode = EXIT_OK
            src_s, dst_s = job["input"], job["output"]
            try:
                src, dst = validate_job(fmt, src_s, dst_s)
                src_s, dst_s = str(src), str(dst)
                dog = _Watchdog(_watchdog_seconds(fmt), timed_out)
                dog.arm()
                try:
                    export_one(fmt, src, dst, log, warnings)
                finally:
                    dog.disarm()
            except CliError as exc:
                jcode, jerr = exc.code, exc.message
                log.write(f"job {i} error ({jcode}): {jerr}")
            except Exception as exc:  # pragma: no cover - defensive
                jcode, jerr = EXIT_FAILED, f"{type(exc).__name__}: {exc}"
            results.append(_job_result(fmt, src_s, dst_s, jcode, warnings, jerr, job_started))
        if any(r["exitCode"] != EXIT_OK for r in results):
            code = EXIT_FAILED
            error = "one or more jobs failed"
    except CliError as exc:
        code, error = exc.code, exc.message
        opts = exc.opts or opts
    except Exception as exc:  # pragma: no cover - defensive
        code, error = EXIT_FAILED, f"{type(exc).__name__}: {exc}"
    if error:
        log.write(f"error ({code}): {error}")
        _emit(f"error: {error}")
    _write_json(opts.get("--result-json"), _batch_payload(results, code, started, error))
    log.close()
    _cleanup_profile()
    return code


def _batch_payload(
    results: list[dict[str, Any]], code: int, started: float, error: str | None
) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "ok": code == EXIT_OK,
        "exitCode": code,
        "error": error,
        "results": results,
        "elapsedMs": int((time.monotonic() - started) * 1000),
        "version": _version(),
    }


def _info(argv: list[str], payload_fn: Callable[[], dict[str, Any]]) -> int:
    try:
        _pos, opts = parse_args(argv, 0)
    except CliError as exc:
        _emit(f"error: {exc.message}")
        _write_json(exc.opts.get("--result-json"), {
            "schema": SCHEMA, "ok": False, "exitCode": exc.code, "error": exc.message,
        })
        return exc.code
    payload = payload_fn()
    _write_json(opts.get("--result-json"), payload)
    _emit(json.dumps(payload, ensure_ascii=False))
    return EXIT_OK


def main(argv: list[str]) -> int:
    """Entry for every ``--xxx`` command line (argv excludes the program name)."""
    mode = argv[0] if argv else ""
    rest = argv[1:]
    if mode == "--version":
        return _info(rest, lambda: {"schema": SCHEMA, "version": _version()})
    if mode == "--capabilities":
        return _info(rest, lambda: {
            "schema": SCHEMA,
            "version": _version(),
            "formats": list(FORMATS),
            "batch": True,
            "exitCodes": dict(_EXIT_CODES),
        })
    if mode == "--export":
        return _run_single(rest)
    if mode == "--export-batch":
        return _run_batch(rest)
    _emit(f"error: unknown option: {mode}")
    return EXIT_USAGE
