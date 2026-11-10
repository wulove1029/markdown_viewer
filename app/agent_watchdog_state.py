"""Pure (Qt-free) state machine for the AI_Agent_Watchdog integration.

Feeds on ``/api/events`` and ``/api/status`` payloads, keeps the set of
sessions that need the user, decides when a toast is due and renders the
status-bar badge.  The clock is injected so every rule is testable without
sleeping.
"""

from __future__ import annotations

import ipaddress
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

WAITING = "WAITING"
STUCK = "STUCK"
MIXED = "MIXED"            # Toast.kind of a merged waiting + stuck toast
ATTENTION_STATES = (WAITING, STUCK)

DEBOUNCE_SECONDS = 25.0
COOLDOWN_SECONDS = 300.0
BACKOFF_STEPS = (5, 10, 30, 60)

# apply_events() results
BASELINE = "baseline"      # first contact: caller should fetch /api/status
RESYNC = "resync"          # boot_id changed or truncated: rebuild from status
OK = "ok"
INVALID = "invalid"        # malformed payload: treat as one failed request

MERGE_WINDOW_SECONDS = 5.0  # toasts due this soon after the first ride along

# Optional top-level declaration in /api/events: {"panel": {"path": "/x"}} names
# the page the panel should show instead of "/".
PANEL_KEY = "panel"
PANEL_PATH_KEY = "path"
PANEL_SCHEME_PARAM = "scheme"   # query parameter carrying the viewer's theme
# The panel only shows what the agents are doing: every page it loads is asked
# to leave out whatever would change the Watchdog's state.  A Watchdog that
# does not know the parameter ignores it.
PANEL_READONLY_QUERY = "readonly=1"
PANEL_PATH_MAX_LENGTH = 200


_PANEL_PATH_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._~-/"
)


def valid_panel_path(path: object) -> bool:
    """True for a plain absolute URL path made of unreserved ASCII only.

    No query, fragment, escapes, empty segments or "." / ".." segments.
    """
    if not isinstance(path, str) or not 0 < len(path) <= PANEL_PATH_MAX_LENGTH:
        return False
    if not path.startswith("/") or "//" in path:
        return False
    if not _PANEL_PATH_CHARS.issuperset(path):
        return False
    return not any(segment in (".", "..") for segment in path.split("/"))


def panel_path_from_payload(payload: object) -> str | None:
    """The declared panel path of an /api/events body, or None.

    Anything missing or unacceptable is simply "not declared"; it never makes
    the payload itself invalid.  Unknown keys inside the declaration are fine.
    A declared "/" is the default page, so it counts as not declared too.
    """
    if not isinstance(payload, dict):
        return None
    panel = payload.get(PANEL_KEY)
    if not isinstance(panel, dict):
        return None
    path = panel.get(PANEL_PATH_KEY)
    return path if valid_panel_path(path) and path != "/" else None


def panel_url(base_url: str, path: str | None, dark: bool) -> str:
    """URL the panel loads: the declared page with the theme, else "/".

    Either way the page is asked to be read-only.
    """
    if path is None:
        return f"{base_url}/?{PANEL_READONLY_QUERY}"
    scheme = "dark" if dark else "light"
    return f"{base_url}{path}?{PANEL_SCHEME_PARAM}={scheme}&{PANEL_READONLY_QUERY}"


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def valid_events_payload(payload: object) -> bool:
    """Strict shape check of an /api/events body (unknown keys are fine)."""
    if not isinstance(payload, dict):
        return False
    if not _is_int(payload.get("schema")) or payload["schema"] != 1:
        return False
    boot_id = payload.get("boot_id")
    if not isinstance(boot_id, str) or not boot_id:
        return False
    latest = payload.get("latest_seq")
    if not isinstance(latest, int) or isinstance(latest, bool) or latest < 0:
        return False
    if not isinstance(payload.get("truncated"), bool):
        return False
    events = payload.get("events")
    if not isinstance(events, list):
        return False
    for event in events:
        if not isinstance(event, dict) or not _is_int(event.get("seq")):
            return False
        if not isinstance(event.get("logical_id"), str):
            return False
        for key in ("from", "to"):
            if event.get(key) is not None and not isinstance(event[key], str):
                return False
    return True


def is_loopback_url(url: str) -> bool:
    """True only for http(s) URLs whose host is a literal loopback address."""
    try:
        parts = urlsplit(url.strip())
        host = parts.hostname
        parts.port  # noqa: B018 - raises ValueError on a malformed port
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or not host:
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False  # "localhost" and other names are intentionally refused


class Backoff:
    """Retry delay after connection failures: 5 -> 10 -> 30 -> 60 seconds."""

    def __init__(self, steps: tuple[int, ...] = BACKOFF_STEPS) -> None:
        self._steps = steps
        self._index = 0

    def next_delay(self) -> int:
        delay = self._steps[min(self._index, len(self._steps) - 1)]
        self._index += 1
        return delay

    def reset(self) -> None:
        self._index = 0


@dataclass(frozen=True)
class Toast:
    kind: str          # WAITING | STUCK
    title: str
    message: str
    ids: tuple[str, ...]


def _label(info: dict[str, Any]) -> str:
    project = info.get("project") or info.get("logical_id") or "?"
    agent = info.get("agent")
    return f"{project}（{agent}）" if agent else str(project)


class WatchdogState:
    def __init__(
        self,
        clock: Callable[[], float] = time.time,
        last_toast: dict[str, float] | None = None,
        toast_waiting: bool = True,
        toast_stuck: bool = True,
    ) -> None:
        self._clock = clock
        self.toast_waiting = toast_waiting
        self.toast_stuck = toast_stuck
        self.last_toast: dict[str, float] = dict(last_toast or {})
        self.boot_id: str | None = None
        self.seq: int | None = None
        self.legacy = False
        self.unavailable = True
        # Declared by the latest valid /api/events answer; None = not declared.
        self.panel_path: str | None = None
        # logical_id -> {"state", "project", "agent", "host"}
        self.attention: dict[str, dict[str, Any]] = {}
        # logical_id -> (due_time, kind)
        self._pending: dict[str, tuple[float, str]] = {}
        self.last_toast_dirty = False

    # --- connectivity -----------------------------------------------------

    def set_unavailable(self, unavailable: bool) -> None:
        """Pending toasts survive an outage: due_toasts() holds them back while
        unavailable and re-checks the session when the Watchdog is back."""
        self.unavailable = unavailable
        if unavailable:
            self.panel_path = None

    def set_legacy(self, legacy: bool) -> None:
        """Old Watchdog without /api/events: badge only, never a toast."""
        self.legacy = legacy
        if legacy:
            self._pending.clear()
            self.panel_path = None

    def clear_panel(self) -> None:
        """Forget the declared panel path (restart or switch to another base)."""
        self.panel_path = None

    def clear_pending(self) -> None:
        self._pending.clear()

    def reset_stream(self) -> None:
        """Forget the cursor (e.g. after a 400) so the next poll re-baselines."""
        self.boot_id = None
        self.seq = None

    # --- inputs -----------------------------------------------------------

    def apply_events(self, payload: dict[str, Any]) -> str:
        """Apply an /api/events payload; INVALID if malformed.

        A malformed payload leaves everything untouched, the declared panel
        path included: one bad answer is retried, and the declaration is only
        dropped once the Watchdog counts as unavailable, legacy or replaced.
        """
        if not valid_events_payload(payload):
            return INVALID
        self.panel_path = panel_path_from_payload(payload)
        boot_id = payload["boot_id"]
        latest = payload["latest_seq"]
        self.unavailable = False
        if self.boot_id is None:
            self.boot_id = boot_id
            self.seq = latest
            return BASELINE
        if boot_id != self.boot_id:
            self.boot_id = boot_id
            self.seq = latest
            self._pending.clear()
            return RESYNC
        if payload["truncated"]:
            self.seq = max(self.seq or 0, latest)
            self._pending.clear()
            return RESYNC
        events = sorted(payload["events"], key=lambda e: e["seq"])
        for event in events:
            if self.seq is not None and event["seq"] <= self.seq:
                continue
            self._apply_event(event)
        # Within one boot the cursor only moves forward (a late reply must not
        # rewind it and replay events).
        self.seq = max(self.seq or 0, latest)
        return OK

    def _apply_event(self, event: dict[str, Any]) -> None:
        lid = event.get("logical_id")
        if not isinstance(lid, str):
            return
        to = event.get("to")
        self._pending.pop(lid, None)  # any newer event cancels the debounce
        if to not in ATTENTION_STATES:
            self.attention.pop(lid, None)
            return
        self.attention[lid] = {
            "state": to,
            "project": event.get("project"),
            "agent": event.get("agent"),
            "host": event.get("host"),
            "logical_id": lid,
        }
        if event.get("from") is None or self.legacy:
            return  # first sighting (e.g. just appeared): never notify
        self._pending[lid] = (self._clock() + DEBOUNCE_SECONDS, to)

    def apply_status(self, payload: dict[str, Any]) -> None:
        """Rebuild the attention set from the snapshot (authoritative)."""
        self.unavailable = False
        rebuilt: dict[str, dict[str, Any]] = {}
        for item in payload.get("attention") or []:
            if not isinstance(item, dict):
                continue
            lid = item.get("logical_id")
            state = item.get("state")
            if isinstance(lid, str) and state in ATTENTION_STATES:
                rebuilt[lid] = {
                    "state": state,
                    "project": item.get("project"),
                    "agent": item.get("agent"),
                    "host": item.get("host"),
                    "logical_id": lid,
                }
        self.attention = rebuilt
        for lid in list(self._pending):
            if lid not in rebuilt or rebuilt[lid]["state"] != self._pending[lid][1]:
                del self._pending[lid]

    # --- toasts -----------------------------------------------------------

    def _kind_enabled(self, kind: str) -> bool:
        return self.toast_waiting if kind == WAITING else self.toast_stuck

    def due_toasts(self) -> list[Toast]:
        """Toasts whose debounce window elapsed; one merged toast per kind."""
        if self.unavailable or self.legacy:
            return []
        now = self._clock()
        if not any(due <= now for due, _ in self._pending.values()):
            return []
        # The first toast to expire carries along everything due within the
        # merge window, so one burst is one notification.
        horizon = now + MERGE_WINDOW_SECONDS
        groups: dict[str, list[str]] = {WAITING: [], STUCK: []}
        for lid, (due, kind) in list(self._pending.items()):
            if due > horizon:
                continue
            del self._pending[lid]
            current = self.attention.get(lid)
            if current is None or current["state"] != kind:
                continue
            if not self._kind_enabled(kind):
                continue
            last = self.last_toast.get(lid)
            if last is not None and 0 <= now - last < COOLDOWN_SECONDS:
                continue
            groups[kind].append(lid)
        toasts: list[Toast] = []
        waiting_ids, stuck_ids = groups[WAITING], groups[STUCK]
        if waiting_ids and stuck_ids:
            # Two showMessage calls in one second: Windows keeps only the last.
            all_ids = waiting_ids + stuck_ids
            for lid in all_ids:
                self.last_toast[lid] = now
            self.last_toast_dirty = True
            names = "、".join(_label(self.attention[i]) for i in all_ids)
            toasts.append(Toast(
                MIXED, "AI Agent 需要你注意",
                f"{len(waiting_ids)} 個在等你、{len(stuck_ids)} 個可能卡住：{names}",
                tuple(all_ids),
            ))
            groups = {WAITING: [], STUCK: []}
        for kind, ids in groups.items():
            if not ids:
                continue
            names = "、".join(_label(self.attention[i]) for i in ids)
            if kind == WAITING:
                message = (f"{names} 在等你回覆" if len(ids) == 1
                           else f"{len(ids)} 個 agent 在等你：{names}")
                title = "AI Agent 等待回覆"
            else:
                message = (f"{names} 可能卡住" if len(ids) == 1
                           else f"{len(ids)} 個 agent 可能卡住：{names}")
                title = "AI Agent 可能卡住"
            for lid in ids:
                self.last_toast[lid] = now
            self.last_toast_dirty = True
            toasts.append(Toast(kind, title, message, tuple(ids)))
        if toasts:
            self._prune_cooldowns(now)
        return toasts

    def _prune_cooldowns(self, now: float) -> None:
        for lid in [k for k, v in self.last_toast.items()
                    if now - v >= COOLDOWN_SECONDS or v > now]:
            del self.last_toast[lid]

    def next_due_in(self) -> float | None:
        if not self._pending:
            return None
        return max(0.0, min(d for d, _ in self._pending.values()) - self._clock())

    # --- presentation -----------------------------------------------------

    def _counts(self) -> tuple[int, int]:
        waiting = sum(1 for v in self.attention.values() if v["state"] == WAITING)
        stuck = sum(1 for v in self.attention.values() if v["state"] == STUCK)
        return waiting, stuck

    def badge_text(self) -> str:
        if self.unavailable:
            return "🤖 未啟動"
        waiting, stuck = self._counts()
        parts = []
        if waiting:
            parts.append(f"{waiting} 等待中")
        if stuck:
            parts.append(f"{stuck} 可能卡住")
        return "🤖 " + (" · ".join(parts) if parts else "0")

    def badge_level(self) -> str:
        """'off' (grey), 'idle' (faint), or 'attention'."""
        if self.unavailable:
            return "off"
        return "attention" if self.attention else "idle"

    def tooltip(self) -> str:
        if self.unavailable:
            return "AI Agent 監看：Watchdog 未啟動（點擊開啟面板）"
        if not self.attention:
            return "AI Agent 監看：目前沒有等待中或可能卡住的 session"
        lines = []
        for kind, text in ((WAITING, "等待中"), (STUCK, "可能卡住")):
            for info in self.attention.values():
                if info["state"] == kind:
                    lines.append(
                        f"{text}：{info.get('project') or '?'} · "
                        f"{info.get('agent') or '?'} · {info.get('host') or '?'}"
                    )
        return "\n".join(lines)


def load_last_toast(raw: object) -> dict[str, float]:
    """Parse the persisted cooldown table; anything malformed yields {}."""
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: float(v) for k, v in data.items()
            if isinstance(k, str) and isinstance(v, (int, float))
            and not isinstance(v, bool)}


def dump_last_toast(table: dict[str, float]) -> str:
    return json.dumps(table, ensure_ascii=False)
