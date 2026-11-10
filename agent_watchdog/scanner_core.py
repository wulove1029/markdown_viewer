# -*- coding: utf-8 -*-
"""Transcript parsing shared by the local watchdog and the remote scanner.

`aiwatch.py` imports this module.  `remote_scan.py` is concatenated after it and
piped into `python3 -` on a remote host, so this file has to stay stdlib-only
(no psutil, no third-party imports), must never import aiwatch, and must not
assume this machine's operating system.  Keep the syntax conservative enough for
an older python3 on the remote side.
"""

import json
import os
import posixpath
import re
import threading
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

SHOW_WINDOW_S = 12 * 3600      # hide sessions older than this (all OFFLINE by then)
TAIL_BYTES = 256 * 1024

#: The remote scanner brackets its JSON with these markers and the local side
#: parses only what sits between them.  Anything a login shell prints on stdout
#: (a ~/.bashrc echo, a MOTD, a banner) would otherwise be fed to json.loads and
#: come back as "Expecting value: line 1 column 1", which reads like a broken
#: connection rather than a chatty shell.  They live here because both halves of
#: the protocol have to agree on one spelling.
REMOTE_BEGIN_MARK = "---AIWATCH-BEGIN---"
REMOTE_END_MARK = "---AIWATCH-END---"

#: Transcripts are append-only, so a file whose mtime is older than the show
#: window cannot contain a record inside it.  The slack absorbs clock skew
#: between the machine that wrote the timestamps and the one that stats them,
#: so the cheap stat-only pre-check can run *before* any parsing.
WINDOW_PRECHECK_SLACK_S = 3600

#: Upper bound on the parsed-tail / session-meta caches (entries, not bytes).
TAIL_CACHE_MAX = 256

AGENT_TOOLS = ("Task", "Agent")


# ------------------------------------------------------------------- utilities

def tail_lines(path, size=TAIL_BYTES):
    try:
        with open(str(path), "rb") as f:
            f.seek(0, 2)
            total = f.tell()
            f.seek(max(0, total - size))
            data = f.read()
    except OSError:
        return []
    lines = data.decode("utf-8", errors="replace").splitlines()
    if total > size and lines:
        lines = lines[1:]  # first line may be truncated
    return lines


def parse_iso(ts):
    if isinstance(ts, (int, float)):
        return float(ts)
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None


def truncate(text, n=140):
    text = re.sub(r"\s+", " ", text or "").strip()
    return text[: n - 1] + "…" if len(text) > n else text


def record_timestamp(rec):
    """Return a transcript record timestamp, if the format exposes one."""
    payload = rec.get("payload") if isinstance(rec, dict) else None
    for value in (
            rec.get("timestamp") if isinstance(rec, dict) else None,
            rec.get("created_at") if isinstance(rec, dict) else None,
            payload.get("timestamp") if isinstance(payload, dict) else None):
        parsed = parse_iso(value)
        if parsed is not None:
            return parsed
    return None


def normalize_cwd(cwd):
    """Normalize a workspace path without applying this host's path rules remotely."""
    value = str(cwd or "").strip().replace("\\", "/")
    if not value:
        return ""
    value = posixpath.normpath(value)
    # Windows drive and UNC paths are case-insensitive; POSIX paths are not.
    if re.match(r"^[A-Za-z]:/", value) or value.startswith("//"):
        value = value.casefold()
    return value.rstrip("/") or "/"


def logical_session_id(agent, cwd, host="local", fallback=""):
    """Stable office identity; transcript filenames deliberately are not included."""
    base_agent = str(agent or "?").split("@", 1)[0].casefold()
    workspace = normalize_cwd(cwd) or normalize_cwd(fallback) or "unknown"
    return "|".join((str(host or "local").casefold(), base_agent, workspace))


# ---------------------------------------------------------------- tail caching

_tail_cache = OrderedDict()   # (path, mtime, size) -> parsed records
_meta_cache = OrderedDict()   # (path, mtime, size) -> {"meta": dict, "ts": float}
_seen_sizes = {}              # path -> last size seen, to spot truncation
#: The background scanner owns these caches in normal operation, but a request
#: thread scans inline whenever the scanner is not running, and several can do
#: so at once.  Reordering an OrderedDict while another thread iterates it
#: raises, so every cache mutation is serialised.  Parsing stays outside the
#: lock: two threads parsing the same file is waste, not corruption.
_cache_lock = threading.Lock()


def file_stamp(path):
    """(mtime, size) identity of a file, or None when it cannot be statted."""
    try:
        st = os.stat(str(path))
    except OSError:
        return None
    return (st.st_mtime, st.st_size)


def immutable_key(path_key):
    """Cache key for content that does not change when a file is appended to.

    (dev, inode) names the file itself, so an active transcript keeps hitting
    the cache while it grows; replacing the path with a different file changes
    it.  Where the platform reports no inode we fall back to (mtime, size),
    which is merely pessimistic.  Inode reuse after a delete is covered by the
    truncation guard, which purges the path as soon as it shrinks.
    """
    try:
        st = os.stat(path_key)
    except OSError:
        return None
    inode = getattr(st, "st_ino", 0) or 0
    if inode:
        return (path_key, "ino", getattr(st, "st_dev", 0), inode)
    return (path_key, "stamp", st.st_mtime, st.st_size)


def _cache_get(cache, key):
    with _cache_lock:
        value = cache.get(key)
        if value is not None:
            cache.move_to_end(key)
        return value


def _cache_put(cache, key, value):
    with _cache_lock:
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > TAIL_CACHE_MAX:
            cache.popitem(last=False)
    return value


def _forget_if_truncated(path_key, size):
    """A file that shrank was truncated or rotated: drop everything cached for it.

    Content identity is (mtime, size); a rotated file can land on the exact key
    its predecessor used, which would serve the old content forever.  Noticing
    the shrink and purging the path forces the next read to go to disk in full.
    """
    with _cache_lock:
        previous = _seen_sizes.get(path_key)
        if len(_seen_sizes) > 4 * TAIL_CACHE_MAX:
            _seen_sizes.clear()
        _seen_sizes[path_key] = size
        if previous is None or size >= previous:
            return False
        for cache in (_tail_cache, _meta_cache):
            for key in [k for k in cache if k[0] == path_key]:
                del cache[key]
        return True


def clear_tail_cache():
    """Test/maintenance hook: forget every cached transcript read."""
    with _cache_lock:
        _tail_cache.clear()
        _meta_cache.clear()
        _seen_sizes.clear()


def read_jsonl_tail(path):
    """Tail a transcript and return the records that parsed.

    The returned list is shared with the cache, so callers must treat it as
    read-only.  Re-parsing an unchanged transcript on every poll was the single
    largest cost in a scan; keying on (path, mtime, size) makes a repeat scan of
    a quiet session free while any write still invalidates the entry.
    """
    path_key = str(path)
    stamp = file_stamp(path_key)
    if stamp is None:
        return []
    _forget_if_truncated(path_key, stamp[1])
    key = (path_key, stamp[0], stamp[1])
    cached = _cache_get(_tail_cache, key)
    if cached is not None:
        return cached
    records = []
    for line in tail_lines(path):
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except ValueError:  # json.JSONDecodeError is a subclass
            continue
    return _cache_put(_tail_cache, key, records)


# ---------------------------------------------------------------- Claude Code

def summarize_claude_event(rec):
    """Return (kind, label) for a user/assistant transcript line."""
    msg = rec.get("message") or {}
    role = msg.get("role")
    content = msg.get("content")
    if isinstance(content, str):
        if role == "user":
            return "user_input", truncate(content)
        return "assistant_text", truncate(content)
    if not isinstance(content, list):
        return None
    tools, texts = [], []
    has_result = False
    for part in content:
        if not isinstance(part, dict):
            continue
        ptype = part.get("type")
        if ptype == "tool_use":
            tools.append(part.get("name") or "tool")
        elif ptype == "text":
            texts.append(part.get("text") or "")
        elif ptype == "tool_result":
            has_result = True
    if role == "assistant":
        if tools:
            return "tool", "呼叫 " + ", ".join(tools[:3])
        if texts:
            return "assistant_text", truncate(" ".join(texts))
        return "thinking", "思考中"
    if has_result:
        return "tool_result", "工具執行完成，AI 處理中"
    if texts:
        return "user_input", truncate(" ".join(texts))
    return None


def count_pending_subagents(records):
    """Task/Agent tool_use ids on the main thread with no tool_result yet."""
    spawned, finished = set(), set()
    for rec in records:
        if rec.get("type") not in ("user", "assistant") or rec.get("isSidechain"):
            continue
        content = (rec.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "tool_use" and part.get("name") in AGENT_TOOLS:
                spawned.add(part.get("id"))
            elif part.get("type") == "tool_result":
                finished.add(part.get("tool_use_id"))
    return len(spawned - finished)


# Background work that Claude Code reports back on by itself: when it finishes,
# a <task-notification> user record wakes the session up.  A launch is only
# recognised at the very START of a tool_result text.  Real launch results always
# begin there, while a tool that merely prints one (a grep through transcripts,
# a Read of a test fixture) carries the phrase somewhere in the middle, and a
# launch that never completes would hold the session in BACKGROUND for hours.
_BG_AGENT_LAUNCH_RE = re.compile(
    r"\s*Async agent launched successfully\..*?\bagentId:\s*([0-9A-Fa-f]+)",
    re.DOTALL)
_BG_COMMAND_LAUNCH_RE = re.compile(
    r"\s*Command running in background with ID:\s*([A-Za-z0-9_-]+)")
_TASK_NOTIFICATION_TAG = "<task-notification>"
_TASK_ID_RE = re.compile(r"<task-id>\s*([^<\s]+)\s*</task-id>")


def _text_blocks(content):
    """Text of a content value that is a plain string or a list of text parts.

    Only parts of type "text" are read, so applied to a message it never sees
    tool output, and applied to a tool_result it covers both shapes.
    """
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        return [p.get("text") for p in content
                if isinstance(p, dict) and p.get("type") == "text"
                and isinstance(p.get("text"), str)]
    return []


def pending_background_launches(records):
    """[(kind, timestamp)] of main-thread background work with no completion yet.

    `since` is the timestamp of the oldest such launch (None when there is none
    or no launch record carries a timestamp).  Only what is inside this tail is
    known: a launch older than the tail is invisible, which errs towards the
    ordinary verdict.  Rules:
      * launches come only from tool_result content (async Agent / Bash or
        PowerShell run_in_background);
      * any <task-notification> naming a <task-id> ends that task, whatever its
        <status> says, however many ids it names and however often it repeats;
      * a notification only counts from a user record that is neither isMeta
        nor relayed from another session (origin.kind == "peer"): those quote
        the same strings but are about somebody else's work;
      * isSidechain records (a subagent's own traffic) are ignored entirely.
    """
    launched = {}       # task id -> (kind, launch timestamp) of the first launch
    finished = set()
    for rec in records:
        if not isinstance(rec, dict) or rec.get("type") != "user" \
                or rec.get("isSidechain"):
            continue
        msg = rec.get("message")
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict) or part.get("type") != "tool_result":
                    continue
                for text in _text_blocks(part.get("content")):
                    kind, match = "agent", _BG_AGENT_LAUNCH_RE.match(text)
                    if not match:
                        kind, match = "command", _BG_COMMAND_LAUNCH_RE.match(text)
                    if match and match.group(1) not in launched:
                        launched[match.group(1)] = (kind, record_timestamp(rec))
        origin = rec.get("origin")
        if rec.get("isMeta") is True or (
                isinstance(origin, dict) and origin.get("kind") == "peer"):
            continue
        for text in _text_blocks(content):
            if _TASK_NOTIFICATION_TAG in text:
                finished.update(_TASK_ID_RE.findall(text))
    return [launch for task_id, launch in launched.items() if task_id not in finished]


def pending_background_tasks(records):
    """(count, since): how many launches are unfinished, and the oldest one's time."""
    pending = pending_background_launches(records)
    stamps = [ts for _kind, ts in pending if ts is not None]
    return len(pending), (min(stamps) if stamps else None)


def newest_launch_by_kind(pending):
    """(agent_at, command_at): the newest unfinished launch time of each kind.

    The caller (state_core.classify) holds the policy: an async agent is trusted
    for hours, a background command (maybe a dev server that never ends) only
    briefly, so the verdict needs the newest of each kind, not the oldest overall.
    """
    newest = {"agent": None, "command": None}
    for kind, ts in pending:
        if ts is not None and (newest[kind] is None or ts > newest[kind]):
            newest[kind] = ts
    return newest["agent"], newest["command"]


def parse_claude_tail(path):
    info = {"title": None, "last_prompt": None, "cwd": None, "branch": None,
            "kind": None, "label": None, "ts": None, "subagents": 0,
            "entrypoint": None, "background_pending": 0,
            "background_since": None, "background_agent_at": None,
            "background_command_at": None}
    records = read_jsonl_tail(path)
    timestamps = [record_timestamp(rec) for rec in records]
    info["ts"] = max((ts for ts in timestamps if ts is not None), default=None)
    info["subagents"] = count_pending_subagents(records)
    launches = pending_background_launches(records)
    info["background_pending"] = len(launches)
    stamps = [ts for _kind, ts in launches if ts is not None]
    info["background_since"] = min(stamps) if stamps else None
    info["background_agent_at"], info["background_command_at"] = \
        newest_launch_by_kind(launches)
    entrypoint_seen = False
    for rec in reversed(records):
        rtype = rec.get("type")
        if not entrypoint_seen and "entrypoint" in rec:
            # Which program hosts the session (claude-vscode, claude-desktop,
            # cli...); the newest record that carries the key decides, even when
            # its value is unusable (then None, never an older record's value).
            entrypoint_seen = True
            if isinstance(rec["entrypoint"], str):
                info["entrypoint"] = rec["entrypoint"]
        if rtype in ("ai-title", "custom-title") and not info["title"]:
            info["title"] = rec.get("aiTitle") or rec.get("customTitle")
        elif rtype == "last-prompt" and not info["last_prompt"]:
            info["last_prompt"] = truncate(rec.get("lastPrompt"), 100)
        elif rtype in ("user", "assistant") and info["kind"] is None:
            if rec.get("isSidechain"):
                continue  # subagent traffic; keep looking for the main thread
            summarized = summarize_claude_event(rec)
            if not summarized:
                continue
            info["kind"], info["label"] = summarized
            info["cwd"] = rec.get("cwd")
            info["branch"] = rec.get("gitBranch")
        if (info["kind"] and info["title"] and info["last_prompt"]
                and entrypoint_seen):
            break
    return info


def scan_claude_dir(root, now, window=SHOW_WINDOW_S):
    """Newest transcript of every project directory under ~/.claude/projects."""
    sessions = []
    root = Path(root)
    if not root.is_dir():
        return sessions
    try:
        proj_dirs = list(root.iterdir())
    except OSError:
        return sessions
    for proj_dir in proj_dirs:
        if not proj_dir.is_dir():
            continue
        jsonls = list(proj_dir.glob("*.jsonl"))
        if not jsonls:
            continue
        try:
            latest, file_mtime = max(
                ((p, p.stat().st_mtime) for p in jsonls), key=lambda t: t[1])
        except OSError:
            continue
        if now - file_mtime > window + WINDOW_PRECHECK_SLACK_S:
            continue  # stat says it cannot be in the window; do not parse it
        info = parse_claude_tail(latest)
        activity_ts = info["ts"] or file_mtime
        if now - activity_ts > window:
            continue
        cwd = info["cwd"] or proj_dir.name
        sessions.append({
            "agent": "Claude",
            "project": Path(cwd).name if info["cwd"] else proj_dir.name,
            "cwd": info["cwd"],
            "branch": info["branch"],
            "title": info["title"],
            "last_prompt": info["last_prompt"],
            "kind": info["kind"],
            "label": info["label"],
            "subagents": info["subagents"],
            "entrypoint": info["entrypoint"],
            "background_pending": info["background_pending"],
            "background_since": info["background_since"],
            "background_agent_at": info["background_agent_at"],
            "background_command_at": info["background_command_at"],
            "mtime": activity_ts,
            "file_mtime": file_mtime,
            "file": str(latest),
        })
    return sessions


# ------------------------------------------------------------------ Codex CLI

CODEX_SKIP = {"token_count", "turn_context", "world_state",
              "thread_settings_applied", "inter_agent_communication_metadata"}


def codex_payload_text(payload):
    for key in ("message", "text", "last_agent_message", "content"):
        content = payload.get(key)
        if isinstance(content, str) and content:
            return content
        if isinstance(content, list):
            text = " ".join(
                str(part.get("text") or part.get("content") or "")
                for part in content if isinstance(part, dict))
            if text.strip():
                return text
    return ""


def summarize_codex_payload(payload):
    ptype = payload.get("type")
    phase = payload.get("phase")
    if ptype in CODEX_SKIP or ptype is None:
        return None
    if phase == "commentary" and ptype in ("agent_message", "message"):
        return "thinking", truncate(codex_payload_text(payload) or "進度更新")
    if phase == "final_answer" and ptype in ("agent_message", "message"):
        return "assistant_text", truncate(codex_payload_text(payload) or "任務完成")
    if ptype == "task_complete":
        # Lifecycle marker only. Keep looking backward for the real final answer.
        return None
    if ptype == "turn_aborted":
        return "assistant_text", "任務已中止"
    if ptype in ("function_call", "custom_tool_call"):
        return "tool", "呼叫 " + (payload.get("name") or "tool")
    if ptype in ("function_call_output", "custom_tool_call_output"):
        return "tool_result", "工具執行完成，AI 處理中"
    if ptype == "reasoning":
        return "thinking", "思考中"
    if ptype in ("agent_message",):
        return "assistant_text", truncate(codex_payload_text(payload))
    if ptype in ("user_message",):
        return "user_input", truncate(codex_payload_text(payload))
    if ptype == "message":
        role = payload.get("role")
        text = codex_payload_text(payload)
        if role == "assistant":
            return "assistant_text", truncate(text)
        if role == "user":
            return "user_input", truncate(text)
        return None
    if ptype == "task_started":
        return "user_input", "任務開始"
    return None


def codex_meta(payload):
    """Normalize the evolving Codex session_meta subagent fields."""
    source = payload.get("source")
    subagent = source.get("subagent") if isinstance(source, dict) else None
    spawn = subagent.get("thread_spawn") if isinstance(subagent, dict) else None
    if not isinstance(spawn, dict):
        spawn = subagent if isinstance(subagent, dict) else {}
    parent_id = payload.get("parent_thread_id") or spawn.get("parent_thread_id")
    thread_source = payload.get("thread_source")
    is_subagent = (thread_source == "subagent" or bool(subagent) or bool(parent_id))
    thread_id = payload.get("id") or payload.get("thread_id")
    session_id = payload.get("session_id") or thread_id
    return {
        "thread_id": thread_id,
        "session_id": session_id,
        "parent_thread_id": parent_id,
        "thread_source": thread_source,
        "is_subagent": is_subagent,
    }


def codex_session_meta(path):
    """First line of a rollout (cwd / git / thread ids), cached per file version.

    session_meta is written once and never rewritten, so re-opening and
    re-parsing it on every poll was pure waste — and it meant every rollout file
    was opened twice per scan.
    """
    path_key = str(path)
    stamp = file_stamp(path_key)
    if stamp is None:
        return None
    _forget_if_truncated(path_key, stamp[1])
    key = immutable_key(path_key)
    if key is None:
        return None
    cached = _cache_get(_meta_cache, key)
    if cached is not None:
        return cached or None  # False marks "this file has no usable meta"
    try:
        with open(path_key, "r", encoding="utf-8", errors="replace") as f:
            first = json.loads(f.readline())
        payload = first.get("payload") or {}
    except (OSError, ValueError, AttributeError):  # JSONDecodeError is a ValueError
        payload = None
    if not isinstance(payload, dict):
        _cache_put(_meta_cache, key, False)
        return None
    return _cache_put(_meta_cache, key,
                      {"meta": payload, "ts": record_timestamp(first)})


def parse_codex_rollout(path, file_mtime):
    path = Path(path)
    session_meta = codex_session_meta(path)
    if session_meta is None:
        return None
    meta_payload = session_meta["meta"]
    records = read_jsonl_tail(path)

    timestamps = [session_meta["ts"]]
    timestamps.extend(record_timestamp(rec) for rec in records)
    activity_ts = max((ts for ts in timestamps if ts is not None),
                      default=file_mtime)
    kind = label = None
    for rec in reversed(records):
        payload = rec.get("payload")
        if not isinstance(payload, dict):
            continue
        summarized = summarize_codex_payload(payload)
        if summarized:
            kind, label = summarized
            break

    lifecycle = None
    completion_fallback = None
    for rec in records:
        payload = rec.get("payload")
        if not isinstance(payload, dict):
            continue
        ptype, phase = payload.get("type"), payload.get("phase")
        if ptype == "task_started":
            lifecycle = "active"
            completion_fallback = None
        elif ptype == "task_complete":
            lifecycle = "complete"
            completion_fallback = ("assistant_text", "任務完成")
        elif ptype == "turn_aborted":
            lifecycle = "complete"
            completion_fallback = ("assistant_text", "任務已中止")
        elif phase == "final_answer":
            lifecycle = "complete"

    if lifecycle == "complete" and kind != "assistant_text" and completion_fallback:
        kind, label = completion_fallback

    cwd = meta_payload.get("cwd")
    git = meta_payload.get("git")
    rollout = {
        "agent": "Codex",
        "project": Path(cwd).name if cwd else path.stem,
        "cwd": cwd,
        "branch": git.get("branch") if isinstance(git, dict) else None,
        "title": None,
        "last_prompt": None,
        "kind": kind,
        "label": label,
        "mtime": activity_ts,
        "file_mtime": file_mtime,
        "file": str(path),
        "active": lifecycle != "complete",
    }
    rollout.update(codex_meta(meta_payload))
    return rollout


def codex_descends_from(child, main, by_thread):
    if not child.get("is_subagent"):
        return False
    main_ids = {value for value in (main.get("thread_id"), main.get("session_id"))
                if value}
    if child.get("session_id") in main_ids:
        return True
    parent_id, seen = child.get("parent_thread_id"), set()
    while parent_id and parent_id not in seen:
        if parent_id in main_ids:
            return True
        seen.add(parent_id)
        parent = by_thread.get(parent_id)
        parent_id = parent.get("parent_thread_id") if parent else None
    return False


def aggregate_codex_group(rollouts):
    """Keep the root transcript stable while borrowing live child activity."""
    by_thread = {}
    for rollout in rollouts:
        key = rollout.get("thread_id") or rollout["file"]
        previous = by_thread.get(key)
        if previous is None or rollout["mtime"] > previous["mtime"]:
            by_thread[key] = rollout
    rollouts = list(by_thread.values())
    mains = [r for r in rollouts if not r.get("is_subagent")]

    def active_children(main):
        return [r for r in rollouts
                if r.get("active") and codex_descends_from(r, main, by_thread)]

    if mains:
        choices = []
        for main in mains:
            children = active_children(main)
            effective_ts = max([main["mtime"]] + [c["mtime"] for c in children])
            choices.append((effective_ts, main["file_mtime"], main, children))
        _ts, _file_ts, display, children = max(choices, key=lambda item: item[:2])
    else:
        display = max(rollouts, key=lambda r: (r["mtime"], r["file_mtime"]))
        root_id = display.get("session_id")
        children = [r for r in rollouts
                    if r.get("is_subagent") and r.get("active")
                    and (not root_id or r.get("session_id") == root_id)]

    activity = max([display] + children, key=lambda r: r["mtime"])
    result = {key: value for key, value in display.items()
              if key not in ("active", "is_subagent")}
    result.update(
        kind=activity.get("kind"),
        label=activity.get("label"),
        mtime=activity["mtime"],
        subagents=len(children),
        activity_file=activity["file"],
    )
    return result


#: Codex files a rollout under the day the session *started* (sessions/YYYY/MM/DD)
#: and keeps appending to it, across midnight and on resume.  The date of the
#: directory therefore says nothing about recent activity; walk a week of them
#: and let the per-file mtime check in scan_codex_paths apply the show window.
CODEX_DAY_LOOKBACK_S = 7 * 86400


def codex_files_in_window(root, now, window=SHOW_WINDOW_S):
    """Candidate rollout files: every date directory from the last week (or the
    show window, if that is longer).  Not filtered by mtime here."""
    files = []
    root = Path(root)
    if not root.is_dir():
        return files
    cutoff = now - max(window, CODEX_DAY_LOOKBACK_S)
    try:
        for year_dir in root.iterdir():
            if not (year_dir.is_dir() and year_dir.name.isdigit()):
                continue
            for month_dir in year_dir.iterdir():
                if not month_dir.is_dir():
                    continue
                for day_dir in month_dir.iterdir():
                    if not day_dir.is_dir():
                        continue
                    try:
                        day_ts = datetime(int(year_dir.name), int(month_dir.name),
                                          int(day_dir.name)).timestamp()
                    except ValueError:
                        continue
                    if day_ts + 86400 < cutoff:
                        continue
                    files.extend(day_dir.glob("*.jsonl"))
    except OSError:
        pass
    return files


def scan_codex_paths(paths, now, window=SHOW_WINDOW_S):
    """Parse rollout files and collapse each workspace into one actor."""
    by_cwd = {}
    for path in paths:
        try:
            file_mtime = Path(path).stat().st_mtime
        except OSError:
            continue
        if now - file_mtime > window + WINDOW_PRECHECK_SLACK_S:
            continue  # stat says it cannot be in the window; do not parse it
        rollout = parse_codex_rollout(path, file_mtime)
        if rollout is None or now - rollout["mtime"] > window:
            continue
        key = normalize_cwd(rollout.get("cwd")) or str(path)
        by_cwd.setdefault(key, []).append(rollout)
    return [aggregate_codex_group(group) for group in by_cwd.values()]


def scan_codex_dir(root, now, window=SHOW_WINDOW_S):
    return scan_codex_paths(codex_files_in_window(root, now, window), now, window)
