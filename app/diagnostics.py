"""Bounded process-local timings with an explicit, content-free JSON export."""

from __future__ import annotations

import json
import math
import os
import tempfile
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

OPERATIONS = frozenset({
    "library_search", "markdown_render", "markdown_metadata", "html_prepare",
    "pdf_render", "properties_load", "background_work",
})
_COUNTERS = (
    "work_items", "cache_hits", "cache_misses", "cancelled", "fallback_requested", "failed",
)


class Diagnostics:
    """Keep only numeric values and fixed operation names, never user input.

    Aggregates have fixed cardinality and survive event-ring eviction. Recording
    does no disk I/O and invalid diagnostic input never interrupts product work.
    """

    def __init__(self, limit: int = 256):
        self.limit = max(1, min(4096, int(limit)))
        self._events: deque[dict] = deque(maxlen=self.limit)
        self._aggregates: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._started = time.monotonic()
        self._event_count = 0

    def record(
        self, operation: str, *, duration_ms: float = 0.0, work_items: int = 1,
        cache_hits: int = 0, cache_misses: int = 0, cancelled: bool = False,
        fallback_requested: bool = False, failed: bool = False,
    ) -> bool:
        if not isinstance(operation, str) or operation not in OPERATIONS:
            return False
        if type(duration_ms) not in (int, float) or not math.isfinite(duration_ms):
            return False
        if duration_ms < 0 or duration_ms > 86_400_000:
            return False
        if any(type(value) is not int or not 0 <= value <= 1_000_000_000
               for value in (work_items, cache_hits, cache_misses)):
            return False
        if any(type(value) is not bool for value in (cancelled, fallback_requested, failed)):
            return False
        event = {
            "operation": operation,
            "elapsed_ms": round((time.monotonic() - self._started) * 1000, 3),
            "duration_ms": round(float(duration_ms), 3), "work_items": work_items,
            "cache_hits": cache_hits, "cache_misses": cache_misses,
            "cancelled": int(cancelled), "fallback_requested": int(fallback_requested),
            "failed": int(failed),
        }
        with self._lock:
            self._events.append(event)
            self._event_count += 1
            aggregate = self._aggregates.setdefault(operation, {
                "events": 0, "duration_total_ms": 0.0, "duration_max_ms": 0.0,
                **dict.fromkeys(_COUNTERS, 0),
            })
            aggregate["events"] += 1
            aggregate["duration_total_ms"] += event["duration_ms"]
            aggregate["duration_max_ms"] = max(
                aggregate["duration_max_ms"], event["duration_ms"],
            )
            for key in _COUNTERS:
                aggregate[key] += event[key]
        return True

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "schema_version": 1,
                "scope": "current_process_session",
                "storage": "memory_until_explicit_export",
                "contains_document_content": False,
                "record_limit": self.limit,
                "recorded_events": self._event_count,
                "omitted_events": self._event_count - len(self._events),
                "elapsed_ms": round((time.monotonic() - self._started) * 1000, 3),
                "aggregates": {name: dict(values) for name, values in self._aggregates.items()},
                "events": [dict(event) for event in self._events],
            }


recorder = Diagnostics()


def record(operation: str, **metrics) -> bool:
    """Instrumentation entry point; unsupported fields are discarded as a unit."""
    try:
        return recorder.record(operation, **metrics)
    except (TypeError, ValueError, OverflowError):
        return False


def snapshot() -> dict:
    """Read numeric session totals without traversing or opening any document."""
    from .render_service import service
    from .search_index import default_index

    result = recorder.snapshot()
    renderer = service()
    # These shared-cache totals describe the whole process, not one query.
    # Cross-service values are sampled sequentially, not one atomic transaction.
    result["runtime_counters"] = {
        "search_cache_hits": default_index.hits,
        "search_decodes": default_index.reads,
        "search_evictions": default_index.evictions,
        "search_retained_bytes": default_index.retained_bytes,
        "render_workers_spawned": renderer.spawned,
        "render_workers_retired": renderer.killed,
        "remote_renders": renderer.remote_renders,
        "transport_fallbacks": renderer.fallbacks,
    }
    result["exported_at_utc"] = datetime.now(timezone.utc).isoformat()
    return result


def export(path: str | Path) -> Path:
    """Replace the selected JSON atomically, preserving it on write failure."""
    destination = Path(path)
    payload = json.dumps(snapshot(), ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=destination.parent,
            prefix=".mdv-diagnostics-", suffix=".tmp", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        return destination
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def export_dialog(parent=None) -> Path | None:
    """Offer a local export; the user chooses whether and where to save it."""
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    selected, _filter = QFileDialog.getSaveFileName(
        parent, "匯出本機效能診斷（不含文件內容或路徑）",
        "markdown-viewer-diagnostics.json", "JSON 檔案 (*.json)",
    )
    if not selected:
        return None
    try:
        destination = export(selected)
    except OSError as exc:
        QMessageBox.warning(parent, "無法匯出診斷", f"無法寫入診斷檔，請選擇可寫入的位置。\n{exc}")
        return None
    if parent is not None and callable(getattr(parent, "statusBar", None)):
        parent.statusBar().showMessage("已匯出本次執行的效能診斷；不含文件內容、名稱或路徑。", 6000)
    return destination
