"""Cheap change detection for document libraries (focus-triggered rescan).

The viewer only watches the single open file. Other Markdown files changed by
sync tools, ``git pull`` or other editors would leave the link index stale,
so on window activation we take a *signature* of every library (``os.scandir``
plus ``stat`` only, never file contents) and rebuild when it differs.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from .document_libraries import should_skip_directory
from .file_types import MARKDOWN_EXTENSIONS

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


# Threads still running when their watcher is torn down stay referenced here
# until they finish, so Qt never destroys a running QThread.
_ORPHANS: set[QThread] = set()


class _ScanThread(QThread):
    done = Signal(int, object, bool)  # generation, signature-or-None, baseline_only

    def __init__(self, generation, baseline_only, scan, roots, excluded, parent=None):
        super().__init__(parent)
        self._generation = generation
        self._baseline_only = baseline_only
        self._scan = scan
        self._roots = roots
        self._excluded = excluded
        self.cancel = threading.Event()

    def run(self):
        try:
            sig = self._scan(self._roots, self._excluded, cancel=self.cancel)
        except Exception:
            log.warning("Library signature scan failed", exc_info=True)
            sig = None
        self.done.emit(self._generation, sig, self._baseline_only)


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
        self._timeout_s = timeout_s
        self._scan_fn = scan_fn or self._default_scan
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

    def _default_scan(self, roots, excluded, cancel=None):
        # Runs on the scan thread: existence checks may block on offline shares.
        roots = [r for r in roots if Path(r).exists()]
        return compute_library_signature(
            roots, excluded, timeout_s=self._timeout_s, cancel=cancel, clock=self._clock
        )

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
            if not thread.wait(1500):
                _ORPHANS.add(thread)
                thread.finished.connect(lambda t=thread: _ORPHANS.discard(t))
                thread.setParent(None)
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
            self._generation, baseline_only, self._scan_fn, roots, excluded, self
        )
        thread.done.connect(self._on_done)
        thread.finished.connect(self._on_thread_finished)
        self._thread = thread
        thread.start()

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

    def _on_thread_finished(self) -> None:
        thread = self.sender()
        if thread is self._thread:
            self._thread = None
        if isinstance(thread, QThread):
            thread.deleteLater()
        if self._queued_baseline and self._thread is None:
            self._queued_baseline = False
            self._start_scan(baseline_only=True)
