"""Cheap change detection for document libraries (focus-triggered rescan).

The viewer only watches the single open file. Other Markdown files changed by
sync tools, ``git pull`` or other editors would leave the link index stale,
so on window activation we take a *signature* of every library (``os.scandir``
plus ``stat`` only, never file contents) and rebuild when it differs.
"""

from __future__ import annotations

import functools
import hashlib
import logging
import os
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from .document_libraries import should_skip_directory
from .file_types import MARKDOWN_EXTENSIONS
from .worker_relay import WorkerRelay

log = logging.getLogger(__name__)

RESCAN_ON_FOCUS_KEY = "library/rescan_on_focus"
MAX_FILES = 8000  # mirrors links._MAX_FILES
DEBOUNCE_MS = 1500
MIN_INTERVAL_S = 10.0
SCAN_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class LibrarySignature:
    count: int
    max_mtime_ns: int
    digest: str
    truncated: bool = False

    def differs_from(self, other: LibrarySignature | None) -> bool:
        """Compare normally; changes beyond the file cap are not detected."""
        if other is None:
            return True
        return (self.count, self.max_mtime_ns, self.digest, self.truncated) != (
            other.count, other.max_mtime_ns, other.digest, other.truncated,
        )


def compute_library_signature(
    roots: Iterable[str | Path],
    excluded_folders: list[str] | None = None,
    *,
    max_files: int = MAX_FILES,
    timeout_s: float = SCAN_TIMEOUT_S,
    cancel: threading.Event | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> LibrarySignature | None:
    """Return a signature of the Markdown files under *roots*.

    Uses only scandir/stat. Returns ``None`` when the scan exceeded
    *timeout_s* or was cancelled (the caller abandons this round). Hitting
    *max_files* returns a signature flagged ``truncated``.
    """
    deadline = clock() + timeout_s
    excluded = excluded_folders if excluded_folders is not None else []
    entries: list[tuple[str, int, int]] = []
    seen: set[str] = set()
    truncated = False

    for root in roots:
        root_path = Path(root)
        stack: list[tuple[str, Path]] = [(str(root_path), Path())]
        while stack:
            if clock() > deadline or (cancel is not None and cancel.is_set()):
                return None
            directory, relative = stack.pop()
            try:
                it = os.scandir(directory)
            except OSError:
                continue
            with it:
                # Sorted traversal makes "the first N files" a stable set.
                listing = sorted(it, key=lambda e: e.name.casefold())
                subdirs: list[tuple[str, Path]] = []
                for entry in listing:
                    if clock() > deadline or (cancel is not None and cancel.is_set()):
                        return None
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if not should_skip_directory(relative / entry.name, excluded):
                                subdirs.append((entry.path, relative / entry.name))
                            continue
                        if os.path.splitext(entry.name)[1].lower() not in MARKDOWN_EXTENSIONS:
                            continue
                        key = entry.path.casefold()
                        if key in seen:
                            continue
                        stat = entry.stat()
                    except OSError:
                        continue
                    if len(entries) >= max_files:
                        truncated = True
                        break
                    seen.add(key)
                    entries.append((key, stat.st_mtime_ns, stat.st_size))
                stack.extend(reversed(subdirs))
            if truncated:
                break
        if truncated:
            break

    entries.sort()
    digest = hashlib.blake2b(digest_size=16)
    for key, mtime_ns, size in entries:
        digest.update(f"{key}\0{mtime_ns}\0{size}\n".encode("utf-8", "surrogatepass"))
    return LibrarySignature(
        count=len(entries),
        max_mtime_ns=max((e[1] for e in entries), default=0),
        digest=digest.hexdigest(),
        truncated=truncated,
    )


def _default_scan(roots, excluded, cancel=None, *, timeout_s: float, clock: Callable[[], float]):
    # Runs on the scan thread: existence checks may block on offline shares.
    roots = [r for r in roots if Path(r).exists()]
    return compute_library_signature(
        roots, excluded, timeout_s=timeout_s, cancel=cancel, clock=clock
    )


class _ScanThread:
    """One signature scan on its own daemon thread; holds no Qt object.

    It reports ``(self, generation, signature_or_None, baseline_only)``
    through the watcher's relay once, after the scan, cancelled or not. Qt
    neither owns nor destroys this thread, so a scan blocked on an offline
    share may outlive its watcher, or the application, without "QThread:
    Destroyed while thread is still running"; its late report is dropped.
    """

    def __init__(self, generation, baseline_only, scan, roots, excluded, report):
        self._generation = generation
        self._baseline_only = baseline_only
        self._scan = scan
        self._roots = roots
        self._excluded = excluded
        self._report = report
        self.cancel = threading.Event()
        self._thread = threading.Thread(target=self._run, name="library-scan", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def wait(self, timeout_s: float) -> bool:
        """Join for at most *timeout_s*; True when the thread has ended."""
        self._thread.join(timeout_s)
        return not self._thread.is_alive()

    def _run(self) -> None:
        sig = None
        try:
            sig = self._scan(self._roots, self._excluded, cancel=self.cancel)
        except Exception:
            log.warning("Library signature scan failed", exc_info=True)
            sig = None
        finally:
            self._report(self, self._generation, sig, self._baseline_only)


class LibraryChangeWatcher(QObject):
    """Debounced, rate-limited, off-thread library change detector."""

    changed = Signal()

    def __init__(
        self,
        roots_provider: Callable[[], Iterable[str | Path]],
        enabled_provider: Callable[[], bool],
        excluded_provider: Callable[[], list[str]] | None = None,
        parent: QObject | None = None,
        *,
        debounce_ms: int = DEBOUNCE_MS,
        min_interval_s: float = MIN_INTERVAL_S,
        scan_fn: Callable[..., LibrarySignature | None] | None = None,
        timeout_s: float = SCAN_TIMEOUT_S,
        clock: Callable[[], float] = time.monotonic,
    ):
        super().__init__(parent)
        self._roots_provider = roots_provider
        self._enabled = enabled_provider
        self._excluded_provider = excluded_provider
        self._min_interval_s = min_interval_s
        self._clock = clock
        # Never a bound method of this watcher: scans may end, and be
        # released, on their own thread after the watcher is gone.
        self._scan_fn = scan_fn or functools.partial(
            _default_scan, timeout_s=timeout_s, clock=clock
        )
        self._baseline: LibrarySignature | None = None
        self._last_scan_at: float | None = None
        self._generation = 0
        self._thread: _ScanThread | None = None
        self._queued_baseline = False
        self.scan_count = 0
        self._emitting = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(debounce_ms)
        self._timer.timeout.connect(self._debounce_fired)
        # Scans report through this relay only; it drops reports once the
        # watcher is destroyed.
        self._relay = WorkerRelay(self, self._on_scan_reported)

    # -- public API ---------------------------------------------------------
    def notify_activated(self) -> None:
        """Window gained focus: schedule one debounced check."""
        if not self._enabled():
            return
        if not self._timer.isActive():
            self._timer.start()

    def rebaseline(self) -> None:
        """Record the current state as 'indexed' without emitting ``changed``."""
        if self._emitting or not self._enabled():
            return
        self._generation += 1  # invalidate any in-flight result
        if self._thread is not None:
            self._queued_baseline = True
            self._thread.cancel.set()
            return
        self._start_scan(baseline_only=True)

    def shutdown(self) -> None:
        self._timer.stop()
        self._queued_baseline = False
        self._generation += 1
        thread = self._thread
        if thread is not None:
            thread.cancel.set()
            # Bounded: a scan stuck on an offline share is left to finish on
            # its daemon thread, and whatever it reports is ignored.
            thread.wait(1.5)
            self._thread = None

    # -- internals ----------------------------------------------------------
    def _debounce_fired(self) -> None:
        if not self._enabled() or self._thread is not None:
            return
        now = self._clock()
        if self._last_scan_at is not None and now - self._last_scan_at < self._min_interval_s:
            return
        self._start_scan(baseline_only=False)

    def _start_scan(self, baseline_only: bool) -> None:
        try:
            # GUI thread: configuration read only, no filesystem access.
            roots = [str(r) for r in self._roots_provider()]
            excluded = list(self._excluded_provider()) if self._excluded_provider else []
        except Exception:
            log.warning("Could not resolve library roots for rescan", exc_info=True)
            return
        self._last_scan_at = self._clock()
        self.scan_count += 1
        thread = _ScanThread(
            self._generation, baseline_only, self._scan_fn, roots, excluded, self._relay.emit
        )
        self._thread = thread
        thread.start()

    def _on_scan_reported(self, thread: _ScanThread, generation: int, sig, baseline_only: bool):
        try:
            self._on_done(generation, sig, baseline_only)
        finally:
            self._on_thread_finished(thread)

    def _on_done(self, generation: int, sig, baseline_only: bool) -> None:
        if generation != self._generation:
            return
        if sig is None:
            log.info("Library signature scan abandoned (timeout or cancelled)")
            return
        previous = self._baseline
        self._baseline = sig
        if baseline_only or previous is None:
            return
        if sig.differs_from(previous):
            # The rebuild this triggers would rebaseline; sig is already the baseline.
            self._emitting = True
            try:
                self.changed.emit()
            finally:
                self._emitting = False

    def _on_thread_finished(self, thread: _ScanThread) -> None:
        if thread is self._thread:
            self._thread = None
        if self._queued_baseline and self._thread is None:
            self._queued_baseline = False
            self._start_scan(baseline_only=True)
