# -*- coding: utf-8 -*-
"""AI Agent Watchdog (release: __version__ below)

Monitors Claude Code / Codex CLI sessions by tailing their on-disk
transcripts, cross-checks with live processes (psutil), and serves a
small web dashboard with an attention queue.

Local scanning runs on a background thread and publishes into a single cached
snapshot; every HTTP request is a dict copy of that cache, so opening N tabs
costs the same as opening one.

Usage:
    py -3 -X utf8 aiwatch.py --once      # print one snapshot to console
    py -3 -X utf8 aiwatch.py --serve     # dashboard at http://localhost:8787

Embedding (a host program such as a frozen desktop app): set the
AIWATCH_* variables (AIWATCH_HOME, AIWATCH_RESOURCES, AIWATCH_EMBEDDED,
AIWATCH_SELF_LAUNCH; see below), `import aiwatch`, then call
`aiwatch.main([...])`.  Importing this module parses no argv and starts
nothing.
"""

#: The release of this program.  Not the same thing as /api/status `version`,
#: which counts published snapshots and restarts from 1 with every process.
__version__ = "1.3.0"

import argparse
import hashlib
import json
import math
import os
import queue
import re
import signal
import socket
import sys
import threading
import time
import traceback
import uuid
from time import monotonic as _monotonic   # immune to tests faking `time`
from collections import deque
from datetime import datetime
from urllib.parse import parse_qs
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from state_core import (  # noqa: F401  state machine, re-exported for callers/tests
    ACTIVE_S, ATTENTION_STATES, BACKGROUND_COMMAND_MAX_S, BACKGROUND_MAX_S,
    OFFLINE_S, PROC_DEAD_GRACE_S, STATES, STUCK_TOOL_S, WAITING_TO_IDLE_S, attention_queue, classify,
)
from scanner_core import (  # parsing shared with the remote scanner
    REMOTE_BEGIN_MARK, REMOTE_END_MARK, SHOW_WINDOW_S,
    WINDOW_PRECHECK_SLACK_S, codex_files_in_window, logical_session_id,
    normalize_cwd, read_jsonl_tail, record_timestamp, scan_claude_dir,
    scan_codex_paths, truncate)

try:
    import psutil
except ImportError:  # process layer is optional
    psutil = None

HOME = Path.home()
CLAUDE_PROJECTS = HOME / ".claude" / "projects"
CODEX_SESSIONS = HOME / ".codex" / "sessions"


# ------------------------------------------------------ Where things live
#
# Two directories, never mixed up:
#
# * the resource directory: the pages, static/ and the scanner sources that are
#   shipped to remote hosts.  Read only.  By default the directory of this file;
#   a host that unpacked them elsewhere names it with AIWATCH_RESOURCES.
# * the data directory: everything written at run time (endpoint.json,
#   install.json, aiwatch.log, last_error.json, hidden.json, office.json,
#   events.jsonl).  AIWATCH_HOME, else ~/.aiwatch.  The resource directory may
#   be read-only (Program Files), so nothing is ever written there.

ENV_HOME = "AIWATCH_HOME"
ENV_RESOURCES = "AIWATCH_RESOURCES"
#: "1": a host program ships and starts this copy (embedded mode).
ENV_EMBEDDED = "AIWATCH_EMBEDDED"
#: JSON array of strings: how the host starts this embedded copy again.
ENV_SELF_LAUNCH = "AIWATCH_SELF_LAUNCH"


def _module_dir():
    here = globals().get("__file__")
    return Path(here).resolve().parent if here else Path.cwd().resolve()


#: The directory this module was loaded from.  Kept absolute and resolved once:
#: see the remote scanner below for why a cwd-relative path is not acceptable.
_SCRIPT_DIR = _module_dir()


def resource_dir():
    """Pages, static/ and remote scanner sources: AIWATCH_RESOURCES or here."""
    override = os.environ.get(ENV_RESOURCES)
    return Path(override) if override else _SCRIPT_DIR


def aiwatch_home():
    """The data directory: AIWATCH_HOME, else ~/.aiwatch."""
    override = os.environ.get(ENV_HOME)
    return Path(override) if override else HOME / ".aiwatch"


def embedded_mode():
    """True when a host program declared that it ships this copy."""
    return (os.environ.get(ENV_EMBEDDED) or "").strip().lower() in ("1", "true", "yes", "on")


def _pinned(name):
    """A path attribute assigned on this module (tests do), else None."""
    return globals().get(name)


def hidden_file():
    return _pinned("HIDDEN_FILE") or aiwatch_home() / "hidden.json"


def office_file():
    return _pinned("OFFICE_FILE") or aiwatch_home() / "office.json"


def remotes_file():
    """remotes.json is user configuration: the data directory's copy wins, and
    only when there is none is the one beside the program used (deployments
    that predate the data directory keep working unchanged)."""
    pinned = _pinned("REMOTES_FILE")
    if pinned is not None:
        return pinned
    user = aiwatch_home() / "remotes.json"
    return user if user.is_file() else resource_dir() / "remotes.json"


def scanner_core_file():
    return _pinned("SCANNER_CORE_FILE") or resource_dir() / "scanner_core.py"


def remote_scanner_file():
    return _pinned("REMOTE_SCANNER_FILE") or resource_dir() / "remote_scan.py"


def static_dir():
    return _pinned("STATIC_DIR") or resource_dir()


#: Module attributes kept for compatibility (callers and tests read them).
#: Reading one gives the path in force right now; assigning one pins it (the
#: tests do), and deleting it again un-pins it.
_DYNAMIC_PATHS = {
    "HIDDEN_FILE": hidden_file, "OFFICE_FILE": office_file,
    "REMOTES_FILE": remotes_file, "SCANNER_CORE_FILE": scanner_core_file,
    "REMOTE_SCANNER_FILE": remote_scanner_file, "STATIC_DIR": static_dir,
}


def __getattr__(name):                     # PEP 562: only for missing names
    factory = _DYNAMIC_PATHS.get(name)
    if factory is None:
        raise AttributeError("module %r has no attribute %r" % (__name__, name))
    return factory()


#: Run-time files that older versions wrote beside aiwatch.py.  Copied into the
#: data directory once (see migrate_legacy_data); the old copies stay.
LEGACY_DATA_FILES = ("hidden.json", "office.json", "events.jsonl", "events.jsonl.1")
#: Where those old copies are looked for.  None: the resource directory.
LEGACY_DATA_DIR = None


def _same_dir(a, b):
    return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(
        os.path.abspath(str(b)))


def migrate_legacy_data(names=LEGACY_DATA_FILES):
    """Bring each old file into the data directory when it has none.  Never raises.

    Copy, never move: the program directory may be read-only, and a copy of an
    older version still running from there keeps its own file.  A file the data
    directory already has is never overwritten, so this happens at most once
    per file.  Returns the names copied.
    """
    copied = []
    try:
        source_dir = Path(LEGACY_DATA_DIR) if LEGACY_DATA_DIR is not None \
            else resource_dir()
        home = aiwatch_home()
        if _same_dir(source_dir, home):
            return copied
    except Exception:
        return copied
    for name in names:
        source, target = source_dir / name, home / name
        tmp = home / ("%s.%d.migrate.tmp" % (name, os.getpid()))
        try:
            if target.exists() or not source.is_file():
                continue
            home.mkdir(parents=True, exist_ok=True)
            data = source.read_bytes()
            tmp.write_bytes(data)
            try:
                # os.link fails when the target exists, so a file another copy
                # created in the meantime is never overwritten (os.replace would).
                os.link(tmp, target)
            except FileExistsError:      # another copy got there first
                continue
            except OSError:
                # No hard links here (the file system may not have them): create
                # the target exclusively (O_CREAT|O_EXCL, mode "xb"), which
                # equally refuses an existing file, and write the content in.
                try:
                    with open(target, "xb") as out:
                        out.write(data)
                except FileExistsError:  # another copy got there first
                    continue
                except BaseException:
                    try:                 # never leave a half-written target
                        target.unlink()
                    except OSError:
                        pass
                    raise
            copied.append(name)
            print("copied %s from %s into %s" % (name, source_dir, home))
        except Exception as exc:         # a failed copy never stops the service
            print("could not copy %s from %s into %s (%s: %s); continuing"
                  % (name, source_dir, home, type(exc).__name__, exc),
                  file=sys.stderr)
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass
    return copied

EVENTS_LOG_MAX_BYTES = 5 * 1024 * 1024   # rotate events.jsonl to .1 at this size
EVENTS_MAX = 500               # /api/events ring buffer size (memory only)
LOCAL_SCAN_S = 3               # background transcript scan period
PROC_SCAN_S = 15               # process liveness needs far less resolution
DIR_CHECK_TTL_S = 30           # os.path.isdir() answers are reused this long
SNAPSHOT_STALE_S = 30          # cache older than this is flagged to the client


# ------------------------------------------- Claude Code / Codex CLI (this host)

def scan_claude(now):
    return scan_claude_dir(CLAUDE_PROJECTS, now, SHOW_WINDOW_S)


def recent_codex_files(now):
    """Rollout files worth parsing; its own function so tests can stub it."""
    return codex_files_in_window(CODEX_SESSIONS, now, SHOW_WINDOW_S)


def scan_codex(now):
    return scan_codex_paths(recent_codex_files(now), now, SHOW_WINDOW_S)


# ------------------------------------------------------------ Antigravity IDE

ANTIGRAVITY_ROOTS = [HOME / ".gemini" / "antigravity",
                     HOME / ".gemini" / "antigravity-cli"]
AG_TOOL_TYPES = {"RUN_COMMAND", "VIEW_FILE", "CODE_ACTION",
                 "GREP_SEARCH", "LIST_DIRECTORY"}
AG_SKIP_TYPES = {"CHECKPOINT", "CONVERSATION_HISTORY", "SYSTEM_MESSAGE", "GENERIC"}


def _ag_unwrap(value):
    """Antigravity args are sometimes double-JSON-encoded strings."""
    if isinstance(value, str) and value.startswith('"'):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value if isinstance(value, str) else None


def _uri_to_path(uri):
    """A local path from a file:/// URI, or None when it is not one.

    Malformed input here is not hypothetical: the URI is scraped out of an
    opaque SQLite blob with a regular expression, so a truncated or re-encoded
    blob can yield a bare prefix with nothing after it.  None means "no
    workspace", which the caller already handles; an exception, or the empty
    string this used to be able to return, does not.
    """
    from urllib.parse import unquote
    if not isinstance(uri, str) or not uri.startswith("file:///"):
        return None
    path = unquote(uri[len("file:///"):]).replace("/", "\\")
    if not path:
        return None
    if len(path) > 1 and path[1] == "|":     # file:///C|/x, an old Windows spelling
        path = path[0] + ":" + path[2:]
    return path


#: A conversation belongs to one workspace for its whole life, so a successful
#: lookup is kept for the life of the process; a failed one is re-tried only
#: this often.  Without the second half, the case with no answer at all is the
#: one that pays the most: one SQLite connection every LOCAL_SCAN_S, for ever.
AG_WORKSPACE_MISS_TTL_S = 300
AG_WORKSPACE_CACHE_MAX = 256
_ag_workspace_lock = threading.Lock()
_ag_workspace_cache = {}       # str(db path) -> (checked_at, workspace or None)
_ag_workspace_problems = {}    # message -> first seen; unexpected failures only


def _note_ag_workspace_problem(message):
    """Record an unexpected lookup failure once.  True if it was new.

    Once, not once per scan: this runs on a 3-second loop, so a permanent fault
    would otherwise write the same line ~1200 times an hour.
    """
    with _ag_workspace_lock:
        if message in _ag_workspace_problems:
            return False
        _ag_workspace_problems[message] = time.time()
    print("aiwatch: Antigravity workspace lookup failed: %s" % message,
          file=sys.stderr)
    return True


def ag_workspace_problems():
    """Unexpected workspace-lookup failures seen so far: message -> first seen."""
    with _ag_workspace_lock:
        return dict(_ag_workspace_problems)


def clear_ag_workspace_cache():
    """Test/maintenance hook: forget cached workspaces and recorded failures."""
    with _ag_workspace_lock:
        _ag_workspace_cache.clear()
        _ag_workspace_problems.clear()


def _read_ag_workspace(db_path):
    """The uncached read: workspace path out of the conversation SQLite, or None.

    The except clause is deliberately narrow.  A blanket `except Exception` here
    turned every fault — a missing sqlite3 module, a corrupt db, a bad URI —
    into cwd=None, and cwd=None does not just lose the project name: it degrades
    logical_id, which is the dedupe key, so one workspace could come back as
    several cards under a hash prefix nobody can read.
    """
    try:
        import sqlite3
    except ImportError:                      # a python built without sqlite3
        _note_ag_workspace_problem("sqlite3 is unavailable")
        return None
    try:
        con = sqlite3.connect("file:%s?mode=ro" % Path(db_path).as_posix(), uri=True)
        try:
            row = con.execute(
                "SELECT data FROM trajectory_metadata_blob WHERE id='main'").fetchone()
        finally:
            con.close()
    except (sqlite3.Error, OSError):
        # Expected and common: no db for this brain dir yet, a schema from
        # another Antigravity build, a file being written as we read it.
        return None
    if not row or not row[0]:
        return None
    match = re.search(rb'file:///[^\x00-\x1f"\']+', row[0])
    if not match:
        return None
    return _uri_to_path(match.group(0).decode("utf-8", "replace"))


def ag_workspace(db_path, miss_ttl=AG_WORKSPACE_MISS_TTL_S):
    """Cached workspace lookup for one conversation db.

    The scan loop asks for this every LOCAL_SCAN_S seconds for every Antigravity
    session whose transcript carries no Cwd tool argument, and the answer cannot
    change between two asks, so uncached it was a SQLite connection per session
    per 3 seconds for the whole uptime of the process.
    """
    key = str(db_path)
    now = time.time()
    with _ag_workspace_lock:
        entry = _ag_workspace_cache.get(key)
        if entry is not None and (entry[1] is not None
                                  or 0 <= now - entry[0] < miss_ttl):
            return entry[1]
    try:
        workspace = _read_ag_workspace(db_path)
    except Exception as exc:                 # noqa: BLE001
        # Not in the expected set, so it is a bug or a broken environment.  It
        # still must not take the scan down, but it must leave a trace: silently
        # degrading to cwd=None is exactly what this rewrite exists to stop.
        _note_ag_workspace_problem("%s: %s" % (type(exc).__name__, exc))
        workspace = None
    with _ag_workspace_lock:
        if len(_ag_workspace_cache) > AG_WORKSPACE_CACHE_MAX:
            _ag_workspace_cache.clear()
        _ag_workspace_cache[key] = (now, workspace)
    return workspace


def summarize_ag_record(rec):
    rtype, status = rec.get("type"), rec.get("status")
    if rtype in AG_SKIP_TYPES:
        return None
    action = None
    for tc in rec.get("tool_calls") or []:
        if not isinstance(tc, dict):
            continue
        args = tc.get("args") or {}
        action = _ag_unwrap(args.get("toolAction")) or _ag_unwrap(args.get("toolSummary"))
        if action:
            break
    if rtype == "USER_INPUT":
        return "user_input", truncate(_ag_unwrap(rec.get("content")) or "")
    if rtype == "PLANNER_RESPONSE":
        if status == "RUNNING":
            return "thinking", truncate(action or "思考中")
        if rec.get("tool_calls"):
            return "tool", truncate(action or "執行工具")
        return "assistant_text", truncate(_ag_unwrap(rec.get("content")) or "")
    if rtype in AG_TOOL_TYPES:
        kind = "tool" if status == "RUNNING" else "tool_result"
        return kind, truncate(action or "執行 " + str(rtype).lower())
    if rtype == "ERROR_MESSAGE":
        return "tool_result", truncate(_ag_unwrap(rec.get("error")) or "發生錯誤")
    return None


def scan_antigravity(now):
    by_cwd = {}
    for root in ANTIGRAVITY_ROOTS:
        brain = root / "brain"
        if not brain.is_dir():
            continue
        try:
            brain_dirs = list(brain.iterdir())
        except OSError:
            continue
        for bdir in brain_dirs:
            transcript = bdir / ".system_generated" / "logs" / "transcript.jsonl"
            try:
                file_mtime = transcript.stat().st_mtime
            except OSError:
                continue
            # Pre-check on the stat alone.  Parsing first and filtering after
            # meant every long-dead brain dir still paid for a transcript tail
            # *and* a SQLite connection, which was 71% of a whole snapshot.
            if now - file_mtime > SHOW_WINDOW_S + WINDOW_PRECHECK_SLACK_S:
                continue
            kind = label = cwd = activity_ts = None
            for rec in reversed(read_jsonl_tail(transcript)):
                if not isinstance(rec, dict):
                    continue
                rec_ts = record_timestamp(rec)
                if rec_ts is not None:
                    activity_ts = max(activity_ts or rec_ts, rec_ts)
                if cwd is None:
                    for tc in rec.get("tool_calls") or []:
                        if isinstance(tc, dict):
                            c = _ag_unwrap((tc.get("args") or {}).get("Cwd"))
                            if c:
                                cwd = c
                                break
                if kind is None:
                    summarized = summarize_ag_record(rec)
                    if summarized:
                        kind, label = summarized
                if kind and cwd:
                    break
            activity_ts = activity_ts or file_mtime
            if now - activity_ts > SHOW_WINDOW_S:
                continue
            if cwd is None:
                # After the activity check, never before.  The stat pre-check
                # above only knows the file's mtime; a transcript that was
                # touched recently but whose records are all outside the window
                # still gets dropped here, and a dropped session must not have
                # bought a SQLite connection on the way out.
                cwd = ag_workspace(root / "conversations" / (bdir.name + ".db"))
            key = normalize_cwd(cwd) or str(bdir)
            prev = by_cwd.get(key)
            if prev and prev["mtime"] >= activity_ts:
                continue
            by_cwd[key] = {
                "agent": "Antigravity",
                "project": Path(cwd).name if cwd else bdir.name[:8],
                "cwd": cwd,
                "branch": None,
                "title": None,
                "last_prompt": None,
                "kind": kind,
                "label": label,
                "subagents": 0,
                "mtime": activity_ts,
                "file_mtime": file_mtime,
                "file": str(transcript),
            }
    return list(by_cwd.values())


# ------------------------------------------------------- Remote hosts (SSH)

#: The concatenated scanner below is piped into `python3 -` on every configured
#: host, i.e. it is arbitrary code execution on all of them; a path that depends
#: on the process working directory is not something to hand that power to.
#: Its sources come from resource_dir(): _SCRIPT_DIR (absolute, resolved once at
#: import) or AIWATCH_RESOURCES, which must be absolute (resource_problem()).
#: remotes.json: see remotes_file().

REMOTE_POLL_S = 20
#: Per-host backoff ladder, in seconds, indexed by consecutive failures.  A host
#: that is permanently broken (bad key, no python3) used to be re-tried every
#: 20s for ever: 180 failed authentications an hour is exactly the pattern
#: fail2ban blocks, and the address it would block is this workstation's.
REMOTE_BACKOFF_S = (20, 40, 80, 160, 300)
#: Must stay below REMOTE_POLL_S.  It used to be 30, so a single host that
#: accepted the TCP connection and then stalled (ConnectTimeout only covers the
#: connect phase) held the whole serial round past its own polling period.
REMOTE_SSH_TIMEOUT_S = 12
REMOTE_CONNECT_TIMEOUT_S = 6
REMOTE_MAX_WORKERS = 8
#: Slack added to the whole-round ceiling below.
REMOTE_ROUND_SLACK_S = 6
#: Flat whole-round ceiling for the common case (one pool of workers).  Kept as
#: a module constant because that is what the tests and callers reach for.
REMOTE_ROUND_TIMEOUT_S = REMOTE_SSH_TIMEOUT_S + REMOTE_ROUND_SLACK_S
REMOTE_STDOUT_LIMIT = 4 * 1024 * 1024
#: A host that has been unreachable this long stops contributing cards: its
#: sessions are a photograph of the past, and an hour is long past the point
#: where they describe anything that is still running.
REMOTE_STALE_TTL_S = 3600
#: Clock-offset guards: below the deadband it is round-trip noise, above the
#: ceiling the remote clock is broken and correcting by it would do more damage
#: than the skew itself.
REMOTE_CLOCK_DEADBAND_S = 2.0
REMOTE_CLOCK_MAX_OFFSET_S = 3600
#: Minimum seconds between any two ssh launches of this process.  Hosts often
#: share a server (two aliases can be one machine), and a server that
#: rate-limits new SSH connections per source address dropped every new
#: connection from this workstation for minutes after a restart had opened
#: them all at once.  Enforced at launch time (_await_ssh_launch_slot), so it
#: holds however rounds line up, including after a slow host.
REMOTE_STAGGER_S = 4

_remote_lock = threading.Lock()
#: name -> {"sessions", "err", "at", "last_success_at", "stale", "stale_since",
#:          "fails", "failing_since", "permanent", "interval", "next_at"}
_remote_cache = {}
#: name -> non-fatal note (clock skew): the poll succeeded, so it cannot travel
#: on the error channel without marking perfectly good sessions stale.
_remote_warnings = {}
#: Set when the poller loop itself raised.  Without it a dead poller thread is
#: invisible: the cache simply stops moving and every card keeps its last value.
_poller_state = {"error": None, "at": None}


class RemoteScriptError(Exception):
    """The payload we were about to execute on a remote host is not trusted."""


def load_remotes():
    try:
        with open(remotes_file(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    remotes = []
    for item in data if isinstance(data, list) else []:
        if isinstance(item, str):
            remotes.append({"host": item, "name": item})
        elif isinstance(item, dict) and item.get("host"):
            remotes.append({"host": item["host"],
                            "name": item.get("name") or item["host"]})
    return remotes


_script_lock = threading.Lock()
_script_state = {"script": None, "sha256": None}


def _concat_remote_script():
    """scanner_core.py + remote_scan.py as one file to feed `python3 -`.

    The shim's `from scanner_core import` line is dropped: concatenation already
    puts both halves in one namespace, and the remote has no scanner_core.py.
    """
    root = resource_dir()
    if not root.is_absolute():
        raise RemoteScriptError("AIWATCH_RESOURCES must be an absolute path: %s"
                                % root)
    core = scanner_core_file().read_bytes()
    shim = b"\n".join(
        line for line in remote_scanner_file().read_bytes().splitlines()
        if not line.startswith(b"from scanner_core import"))
    return core + b"\n" + shim


def remote_script():
    """The concatenated scanner, read from disk exactly once per process.

    Re-reading it every round meant an edit in progress could be shipped as half
    a file, and it gave anything that can write into this directory a fresh
    opportunity every 20 seconds on every host in remotes.json.
    """
    with _script_lock:
        if _script_state["script"] is None:
            script = _concat_remote_script()
            _script_state["script"] = script
            _script_state["sha256"] = hashlib.sha256(script).hexdigest()
        return _script_state["script"]


def remote_script_digest():
    remote_script()
    with _script_lock:
        return _script_state["sha256"]


def reset_remote_script_cache():
    """Test/maintenance hook: forget the cached payload and its hash."""
    with _script_lock:
        _script_state["script"] = None
        _script_state["sha256"] = None


def verified_remote_script():
    """The startup payload, refused if either copy of it has since moved.

    Two comparisons, because there are two ways this becomes someone else's
    code execution on every host in remotes.json:

    * the bytes in memory no longer hash to what we read (something wrote into
      this process), and
    * the files on disk no longer hash to what we read (a sync tool, a checked
      out branch, an editor, anything with write access to this directory).

    The second one is the reason the check exists at all, and comparing the
    cached bytes against themselves — which is what this used to do — can never
    detect it.  Re-hashing 25KB per host per round is not a cost worth
    optimising away.  A legitimate edit means restarting aiwatch, which is the
    intended trade: we would otherwise be re-deciding on every poll whether to
    trust whatever now sits in this directory.
    """
    script = remote_script()
    expected = remote_script_digest()
    if hashlib.sha256(script).hexdigest() != expected:
        raise RemoteScriptError(
            "remote scanner payload was altered in memory; refusing to send")
    on_disk = hashlib.sha256(_concat_remote_script()).hexdigest()
    if on_disk != expected:
        raise RemoteScriptError(
            "remote scanner changed on disk since startup (%s != %s); refusing "
            "to send — restart the watchdog to accept the new payload"
            % (on_disk[:12], expected[:12]))
    return script


#: Errors that repeating the request cannot fix.  Everything else (DNS, routing,
#: a rebooting host) keeps the ordinary ladder, because those do come back.
PERMANENT_ERROR_MARKERS = (
    "permission denied",
    "host key verification failed",
    "no matching host key",
    "too many authentication failures",
    "unprotected private key",
    "remote host identification has changed",
    "command not found",
    "exit 127",
    "no such identity",
    "refusing to send",
)


def remote_error_is_permanent(err):
    text = (err or "").casefold()
    return any(marker in text for marker in PERMANENT_ERROR_MARKERS)


def remote_backoff_delay(fails, permanent=False):
    """Seconds to wait before the next attempt on a host.

    fails == 0 is the healthy cadence; a permanently broken host jumps straight
    to the ceiling instead of walking up the ladder every time it is re-tried.
    """
    fails = int(fails or 0)          # a missing counter is a healthy one
    if fails <= 0:
        return float(REMOTE_POLL_S)
    if permanent:
        return float(REMOTE_BACKOFF_S[-1])
    return float(REMOTE_BACKOFF_S[min(int(fails), len(REMOTE_BACKOFF_S)) - 1])


def ssh_command(host):
    """BatchMode: never prompt.  StrictHostKeyChecking: never accept silently.

    The payload on stdin is executed by the far end and the reply carries cwds,
    branches and the first 140 characters of prompts and answers, so a host key
    we have not seen before has to be an error, not a shrug.  The user's
    ssh_config may say `accept-new` or `no`; this overrides it for our traffic.
    """
    return ["ssh",
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=yes",
            "-o", "ConnectTimeout=%d" % REMOTE_CONNECT_TIMEOUT_S,
            host, "python3", "-"]


def extract_marked_payload(text):
    """Return (payload_text, error) for the region between the sentinels."""
    begin = text.find(REMOTE_BEGIN_MARK)
    end = text.rfind(REMOTE_END_MARK)
    if begin < 0 or end < 0 or end < begin + len(REMOTE_BEGIN_MARK):
        return None, ("remote output has no AIWATCH markers "
                      "(shell banner/MOTD on stdout, or the scanner never ran)")
    return text[begin + len(REMOTE_BEGIN_MARK):end], None


def clock_offset(remote_now, received_at):
    """(offset, warning): how far behind the remote clock is, sanity-checked.

    age is computed as `local now - remote mtime`, so a few tens of seconds of
    skew is enough to flip the 60-second ACTIVE_S verdict for every session on
    that host.
    """
    remote_now = plausible_ts(remote_now)
    if remote_now is None:
        return 0.0, None                 # nothing to correct with
    offset = received_at - remote_now
    if abs(offset) > REMOTE_CLOCK_MAX_OFFSET_S:
        return 0.0, ("遠端時鐘偏移 %+d 秒，超出可信範圍，已忽略不校正"
                     % int(offset))
    if abs(offset) < REMOTE_CLOCK_DEADBAND_S:
        return 0.0, None                 # round-trip noise, not skew
    return offset, None


def _shift_ts(value, offset):
    ts = plausible_ts(value)
    return ts + offset if ts is not None else value


def _set_remote_warning(name, warning):
    with _remote_lock:
        if warning:
            _remote_warnings[name] = warning
        else:
            _remote_warnings.pop(name, None)


def _unpack_remote_payload(payload):
    """(sessions, remote_now) from the marked object, else (None, None).

    A bare list used to be accepted as "a scanner older than the clock field",
    but no such scanner can reach us: the remote runs the script this process
    pipes to it, which always emits the object.  Accepting a list only made a
    reply with no clock reading in it look normal, and the clock reading is what
    keeps a skewed host's ages honest.
    """
    if not isinstance(payload, dict):
        return None, None
    sessions = payload.get("sessions")
    return (sessions if isinstance(sessions, list) else None, payload.get("now"))


def _no_console_flags():
    """CREATE_NO_WINDOW when this process has no console window of its own.

    Under pythonw (--background, as the apps start it) every console program we
    spawn would otherwise get a fresh, visible console: one ssh window per poll.
    With a console (a foreground run) children keep sharing it, so closing that window
    still takes them down with us.  0 off Windows.
    """
    if sys.platform != "win32":
        return 0
    try:
        import ctypes
        if ctypes.windll.kernel32.GetConsoleWindow():
            return 0
    except Exception:
        pass
    import subprocess
    return subprocess.CREATE_NO_WINDOW


def _kill_tree(proc):
    """Kill proc and every descendant.  Best effort, bounded, never raises.

    CPython's own timeout path kills only the direct child.  ssh with a
    ProxyJump leaves an `ssh -W` grandchild holding our pipes, and the
    follow-up communicate() then waits for it for ever.  The tree has to be
    taken down while the parent is still alive (taskkill /T walks from it).
    """
    import subprocess
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           stdin=subprocess.DEVNULL, timeout=5,
                           creationflags=_no_console_flags())
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass


_inflight = set()               # live ssh Popen objects, for shutdown reaping
_inflight_lock = threading.Lock()
_inflight_closed = False        # set by _kill_inflight_remotes; main() reopens


def _reopen_inflight():
    """Allow ssh launches again; main() calls this before starting pollers."""
    global _inflight_closed
    with _inflight_lock:
        _inflight_closed = False


def _kill_inflight_remotes(budget=8.0):
    """Kill the tree of every ssh still running.  Bounded, best effort, silent.

    Daemon poll threads vanish at exit but their ssh children (and ProxyJump
    grandchildren) do not, so main() calls this on the way out.  It also closes
    the gate: a poll thread that passed its stop check just before we got here
    and launches ssh afterwards has that ssh killed by _run_bounded itself,
    instead of slipping past this sweep.
    """
    global _inflight_closed
    with _inflight_lock:
        _inflight_closed = True
        procs = list(_inflight)
    end = _monotonic() + budget
    for proc in procs:
        if _monotonic() >= end:
            break
        try:
            if proc.poll() is None:
                _kill_tree(proc)
        except Exception:
            pass


def _run_bounded(cmd, input=None, timeout=None):
    """subprocess.run(capture_output=True) whose timeout really bounds it.

    Same result (CompletedProcess) and same TimeoutExpired, but on timeout the
    whole process tree is killed and the pipes are drained for at most two more
    seconds, so total time is at most timeout + ~7s however the children behave.
    """
    import subprocess
    kwargs = {}
    if sys.platform != "win32":
        kwargs["start_new_session"] = True      # own group, so killpg gets all
    else:
        # Own process group: a console CTRL_BREAK aimed at us must not reach
        # ssh first, or the parent dies before taskkill /T can walk its tree
        # and the ProxyJump grandchild is orphaned.
        kwargs["creationflags"] = (subprocess.CREATE_NEW_PROCESS_GROUP
                                   | _no_console_flags())
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, **kwargs)
    with _inflight_lock:
        closed = _inflight_closed
        if not closed:
            _inflight.add(proc)
    if closed:
        # Launched after shutdown reaping already ran: never let it outlive us.
        _kill_tree(proc)
        try:
            proc.communicate(timeout=2)
        except Exception:
            pass
        raise RuntimeError("remote poll cancelled: shutting down")
    try:
        stdout, stderr = proc.communicate(input, timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            proc.communicate(timeout=2)
        except Exception:
            pass          # a straggler still holds a pipe: give up on it
        raise subprocess.TimeoutExpired(cmd, timeout)
    except BaseException:
        _kill_tree(proc)
        raise
    finally:
        with _inflight_lock:
            _inflight.discard(proc)
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)


_ssh_launch_lock = threading.Lock()
_ssh_last_launch = None         # _monotonic() of the previous ssh launch


def _await_ssh_launch_slot():
    """Keep ssh launches REMOTE_STAGGER_S apart across the whole process.

    Holding the lock while sleeping is the point: the next launcher queues
    behind this one, however the hosts are grouped into rounds.
    """
    global _ssh_last_launch
    with _ssh_launch_lock:
        if _ssh_last_launch is not None:
            wait = _ssh_last_launch + REMOTE_STAGGER_S - _monotonic()
            if wait > 0:
                time.sleep(wait)
        _ssh_last_launch = _monotonic()


def poll_remote(remote, timeout=REMOTE_SSH_TIMEOUT_S):
    """Run the scanner on one host.  Returns (sessions, error); never raises."""
    import subprocess
    name = remote.get("name") or remote.get("host")
    try:
        script = verified_remote_script()
    except (RemoteScriptError, OSError) as exc:
        return None, str(exc)[:200]
    try:
        _await_ssh_launch_slot()
        result = _run_bounded(ssh_command(remote["host"]), input=script,
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "ssh timed out after %ss" % timeout
    except Exception as exc:
        return None, ("%s: %s" % (type(exc).__name__, exc))[:200]
    received_at = time.time()
    if result.returncode != 0:
        lines = [line for line in
                 (result.stderr or b"").decode("utf-8", "replace").splitlines()
                 if line.strip()]
        detail = lines[-1].strip()[:160] if lines else ""
        return None, ("ssh exit %d: %s" % (result.returncode, detail)).strip()
    stdout = result.stdout or b""
    if len(stdout) > REMOTE_STDOUT_LIMIT:
        # Post-hoc: _run_bounded has already buffered it.  The point is that a
        # host spraying megabytes is reported as such instead of being handed to
        # json.loads and coming back as an unreadable parser error.
        return None, "remote output too large (%d bytes)" % len(stdout)
    payload_text, err = extract_marked_payload(stdout.decode("utf-8", "replace"))
    if err:
        return None, err
    try:
        payload = json.loads(payload_text.strip() or "null")
    except ValueError as exc:
        return None, ("remote JSON invalid: %s" % exc)[:200]
    sessions, remote_now = _unpack_remote_payload(payload)
    if sessions is None:
        return None, "unexpected remote output"
    offset, warning = clock_offset(remote_now, received_at)
    _set_remote_warning(name, warning)
    out = []
    for s in sessions:
        # A remote that answers with a valid JSON list of non-dicts used to
        # raise AttributeError right here, outside any try, and that exception
        # travelled all the way out of the poller thread and killed it.
        if not isinstance(s, dict):
            continue
        s = dict(s)
        base_agent = str(s.get("base_agent") or s.get("agent") or "?").split("@", 1)[0]
        s["base_agent"] = base_agent
        s["agent"] = f"{base_agent}@{name}"
        s["host"] = remote["host"]
        s["remote"] = name
        s.setdefault("title", None)
        s.setdefault("last_prompt", None)
        s.setdefault("branch", None)
        if offset:
            s["mtime"] = _shift_ts(s.get("mtime"), offset)
            s["file_mtime"] = _shift_ts(s.get("file_mtime"), offset)
            # Written by the same remote clock; unshifted, the skew would be
            # charged to the age of the oldest background launch.
            for key in ("background_since", "background_agent_at",
                        "background_command_at"):
                if key in s:
                    s[key] = _shift_ts(s.get(key), offset)
            s["clock_offset_s"] = round(offset, 3)
        s["file"] = f"{name}:{s.get('file')}"
        # No logical_id here: normalize_session() derives it from these same
        # three fields for local and remote rows alike, and computing it twice
        # meant the copy that actually reached the dashboard was the second one
        # anyway — with a different fallback, so the two could disagree.
        out.append(s)
    return out, None


def _safe_poll(remote):
    """poll_remote plus a net: one broken host must not end the round."""
    try:
        return poll_remote(remote)
    except Exception as exc:
        return None, ("%s: %s" % (type(exc).__name__, exc))[:200]


def _cache_entry_after_poll(previous, sessions, err, now):
    if err:
        fails = int(previous.get("fails") or 0) + 1
        permanent = remote_error_is_permanent(err)
        delay = remote_backoff_delay(fails, permanent)
        return {"sessions": previous.get("sessions", []),
                "err": err,
                "at": now,
                "last_success_at": previous.get("last_success_at"),
                "stale": True,
                "stale_since": previous.get("stale_since") or now,
                "fails": fails,
                "failing_since": previous.get("failing_since") or now,
                "permanent": permanent,
                "interval": delay,
                "next_at": now + delay}
    return {"sessions": sessions or [],
            "err": None,
            "at": now,
            "last_success_at": now,
            "stale": False,
            "stale_since": None,
            "fails": 0,
            "failing_since": None,
            "permanent": False,
            "interval": float(REMOTE_POLL_S),
            "next_at": now + REMOTE_POLL_S}


def round_timeout(host_count, workers=None, per_host=None, slack=None):
    """How long a whole round may take, given the pool has to queue.

    With more hosts than workers the round runs in waves, so a flat ceiling
    would start reporting healthy hosts in the second wave as "did not finish"
    and push them up the backoff ladder for a fault that is ours.
    """
    workers = REMOTE_MAX_WORKERS if workers is None else workers
    per_host = REMOTE_SSH_TIMEOUT_S if per_host is None else per_host
    slack = REMOTE_ROUND_SLACK_S if slack is None else slack
    if host_count <= 0:
        return float(per_host + slack)
    waves = -(-int(host_count) // max(1, int(workers)))   # ceiling division
    return float(waves * per_host + slack)


def poll_all_remotes(force=True, now=None, remotes=None):
    """Poll every due host in parallel; return seconds until the next one is due.

    force=True (the default, and what --once and the tests use) ignores the
    backoff schedule.  The poller loop passes force=False so a host that keeps
    refusing our key is left alone until its own next_at comes round.
    """
    remotes = load_remotes() if remotes is None else remotes
    now = time.time() if now is None else now
    with _remote_lock:
        due_at = {name: entry.get("next_at", 0.0)
                  for name, entry in _remote_cache.items()}
    due = [r for r in remotes if force or now >= due_at.get(r["name"], 0.0)]

    results = {}
    # Launches are spaced REMOTE_STAGGER_S apart (the previous round's last one
    # included), so the last host of this round may start that much later.
    deadline = round_timeout(len(due)) + len(due) * REMOTE_STAGGER_S
    if due:
        # Serial polling meant the worst case was hosts x timeout; the whole
        # point of a 20s cadence is lost if one stalled host owns the round.
        # Even a single host goes through the workers: it used to be run inline
        # as an "optimisation", which quietly exempted the commonest
        # configuration of all from the round deadline.
        # Daemon threads on purpose: ThreadPoolExecutor workers are joined at
        # interpreter exit, so one wedged ssh kept the whole process alive
        # after Ctrl+C.  Nothing here may ever block shutdown.
        todo = queue.Queue()
        for r in due:
            todo.put(r)
        cond = threading.Condition()
        state = {"abandoned": False}

        def worker():
            while True:
                try:
                    r = todo.get_nowait()
                except queue.Empty:
                    return
                if state["abandoned"]:
                    return
                res = _safe_poll(r)
                with cond:
                    if state["abandoned"]:
                        return
                    results[r["name"]] = res
                    cond.notify_all()

        for n in range(min(len(due), REMOTE_MAX_WORKERS)):
            threading.Thread(target=worker, daemon=True,
                             name="remote-poll-%d" % n).start()
        end = _monotonic() + deadline
        with cond:
            while len(results) < len(due):
                left = end - _monotonic()
                if left <= 0:
                    break         # stragglers fall through to the message below
                cond.wait(left)
            state["abandoned"] = True
            results = dict(results)

    with _remote_lock:
        for remote in due:
            name = remote["name"]
            sessions, err = results.get(
                name, (None, "poll did not finish within %gs" % deadline))
            if err:
                # The clock note belongs to a reading we no longer have; the
                # failure is the thing worth showing.
                _remote_warnings.pop(name, None)
            _remote_cache[name] = _cache_entry_after_poll(
                _remote_cache.get(name) or {}, sessions, err, now)
        names = {r["name"] for r in remotes}
        for gone in [k for k in _remote_cache if k not in names]:
            del _remote_cache[gone]
            _remote_warnings.pop(gone, None)
        next_at = [entry.get("next_at", now) for entry in _remote_cache.values()]
    return max(0.0, min(next_at) - now) if next_at else float(REMOTE_POLL_S)


def remote_poller(stop=None, interval=REMOTE_POLL_S):
    """The polling loop.  It must never die: when it does, nothing says so.

    A dead poller leaves the cache frozen, remote_errors empty and every card
    showing its last known state, which is the one failure mode a watchdog can
    least afford — it looks exactly like everything being fine.
    """
    while True:
        if stop is not None and stop.is_set():
            return
        wait = float(interval)
        try:
            wait = poll_all_remotes(force=False)
            _record_poller_error(None)
        except Exception as exc:
            _record_poller_error(exc)
            traceback.print_exc()
        # Never sleep past the base cadence: a host added to remotes.json while
        # every existing one is in a 300s backoff still has to be picked up.
        wait = min(max(float(wait), 1.0), float(interval))
        if stop is not None:
            if stop.wait(wait):
                return
        else:
            time.sleep(wait)


def _record_poller_error(exc):
    with _remote_lock:
        if exc is None:
            _poller_state["error"] = None
            _poller_state["at"] = None
        else:
            _poller_state["error"] = ("%s: %s" % (type(exc).__name__, exc))[:200]
            _poller_state["at"] = time.time()


def _describe_remote_error(entry, now):
    """The raw error plus how long it has been going on.

    The dashboard used to replay one identical line for ever, so a host that has
    been dead since breakfast looked the same as one that blipped once.
    """
    err = entry.get("err") or ""
    fails = int(entry.get("fails") or 0)
    since = plausible_ts(entry.get("failing_since"))
    if fails <= 1 or since is None:
        return err
    return "%s（已失敗 %d 次，自 %s 起）" % (
        err, fails, datetime.fromtimestamp(since).strftime("%H:%M"))


def remote_sessions(now=None):
    """Cached remote sessions plus the error lines the dashboard shows."""
    now = time.time() if now is None else now
    sessions, errors = [], []
    with _remote_lock:
        for name, entry in _remote_cache.items():
            stale_since = entry.get("stale_since")
            if (entry.get("stale")
                    and isinstance(stale_since, (int, float))
                    and not isinstance(stale_since, bool)
                    and now - stale_since > REMOTE_STALE_TTL_S):
                # Nothing here describes anything that is still running; the
                # error line below keeps saying why the host is gone.
                entry["sessions"] = []
            for cached in entry["sessions"]:
                if not isinstance(cached, dict):
                    continue
                mtime = plausible_ts(cached.get("mtime"))
                if mtime is not None and now - mtime > SHOW_WINDOW_S:
                    continue          # the show window applies to remotes too
                # An unusable mtime is deliberately *not* dropped here:
                # normalize_session() rejects it and snapshot() counts it in
                # "malformed", which is how a broken remote gets noticed.
                session = dict(cached)
                session["stale"] = bool(entry.get("stale"))
                session["stale_since"] = stale_since
                sessions.append(session)
            if entry.get("err"):
                errors.append("%s: %s" % (name, _describe_remote_error(entry, now)))
            warning = _remote_warnings.get(name)
            if warning:
                errors.append("%s: %s" % (name, warning))
        poller_error = _poller_state.get("error")
    if poller_error:
        errors.append("remote poller 異常: %s" % poller_error)
    return sessions, errors


# ------------------------------------------------------------------ Processes

#: Exact executable names.  Substring matching on the joined command line used
#: to claim bash.exe / py.exe / python.exe as "claude" and cmd.exe /
#: extension-host.exe as "codex", and an `elif` chain then filed a codex process
#: under claude whenever a claude path appeared anywhere in its arguments.
PROC_NAME_AGENTS = {
    "claude": "claude", "claude.exe": "claude",
    "codex": "codex", "codex.exe": "codex",
}
NODE_NAMES = {"node", "node.exe"}
#: Only argv[0]/argv[1] are matched: a transcript path deeper in the command
#: line describes the work, not the program that is running.
ARGV_AGENT_RE = re.compile(r"^(claude|codex)(\.(exe|cmd|js|mjs|bat|ps1))?$", re.I)


def _argv_agent(cmdline):
    for arg in list(cmdline or [])[:2]:
        stem = os.path.basename(str(arg).strip('"').replace("\\", "/").rstrip("/"))
        matched = ARGV_AGENT_RE.match(stem)
        if matched:
            return matched.group(1).lower()
    return None


def own_process_tree():
    """pids of aiwatch itself plus everything it spawned.

    Built with a single system snapshot.  Asking each candidate for its own
    ppid() instead costs ~13ms *per call* on Windows (psutil rebuilds the whole
    pid->ppid map every time), which made the scan slower than the version it
    replaced; children(recursive=True) pays that price exactly once.

    Only reached from _scan_processes_now(), which has already returned when
    psutil is missing, so there is no None guard here.
    """
    tree = {os.getpid()}
    try:
        tree.update(child.pid
                    for child in psutil.Process(os.getpid()).children(recursive=True))
    except (psutil.Error, OSError):
        pass
    return tree


_proc_lock = threading.Lock()
#: (checked_at, result).  checked_at is None until the first real scan.
_proc_cache = {"at": None, "result": None}


def reset_process_cache():
    """Test/maintenance hook: force the next scan_processes() to enumerate."""
    with _proc_lock:
        _proc_cache.update(at=None, result=None)


def scan_processes(ttl=PROC_SCAN_S):
    """Cached {'cwds': {agent: set(cwd)}, 'alive': {agent: bool}} or None.

    Enumerating every process costs ~13ms and the answer — whether an agent is
    running at all — does not need transcript resolution.  The TTL lives here
    rather than in the scanner loop because the loop is not the only caller:
    --once, the inline fallback in current_snapshot() and every /api/hide click
    (through hideable_session_id) reach this too, and each of those used to be a
    full enumeration.

    The TTL is always measured on the wall clock, never on a caller-supplied
    `now`: snapshot() takes one as a parameter and a synthetic value would
    freeze this cache for ever, exactly as it would workspace_exists().
    """
    now = time.time()
    with _proc_lock:
        at = _proc_cache["at"]
        if at is not None and 0 <= now - at < ttl:
            return _proc_cache["result"]
    result = _scan_processes_now()
    with _proc_lock:
        _proc_cache.update(at=now, result=result)
    return result


def _scan_processes_now():
    """One real enumeration; see scan_processes() for the cached entry point.

    Two phases on purpose: reading the command line of all ~470 processes costs
    ~27ms on Windows (one PEB read each) against ~2ms for the names alone, so
    names filter first and only the handful of candidates are inspected.
    """
    if psutil is None:
        return None
    cwds = {"claude": set(), "codex": set(), "antigravity": set()}
    alive = {"claude": False, "codex": False, "antigravity": False}
    candidates = []
    for proc in psutil.process_iter(["name"]):
        try:
            name = (proc.info.get("name") or "").lower()
            agent = PROC_NAME_AGENTS.get(name)
            if agent is None and name.startswith("antigravity"):
                agent = "antigravity"
            if agent is None and name in NODE_NAMES:
                agent = _argv_agent(proc.cmdline())   # candidates only
            if agent is not None:
                candidates.append((proc, agent))
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess, OSError):
            continue
    if not candidates:
        return {"cwds": cwds, "alive": alive}
    own_tree = own_process_tree()
    for proc, agent in candidates:
        try:
            if proc.pid in own_tree:
                continue                              # the watchdog's own tools
            alive[agent] = True
            cwd = normalize_cwd(proc.cwd())
            if cwd:
                cwds[agent].add(cwd)
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess, OSError):
            continue
    return {"cwds": cwds, "alive": alive}


# -------------------------------------------------------- Workspace existence

_dir_lock = threading.Lock()
_dir_cache = {}  # cwd -> (checked_at, exists)


def workspace_exists(cwd, ttl=DIR_CHECK_TTL_S):
    """Cached os.path.isdir().

    On a dropped network share or an unplugged USB drive isdir() blocks for
    seconds; asking once per session per request piled request threads up, so
    the answer is cached and only the background scanner ever asks.

    The TTL is always measured on the wall clock, never on a caller-supplied
    `now`: snapshot() takes one as a parameter, and a value that is not wall
    time froze entries here for ever.
    """
    if not cwd:
        return True
    now = time.time()
    with _dir_lock:
        entry = _dir_cache.get(cwd)
        if entry is not None and 0 <= now - entry[0] < ttl:
            return entry[1]
    exists = os.path.isdir(cwd)
    with _dir_lock:
        if len(_dir_cache) > 512:
            _dir_cache.clear()
        _dir_cache[cwd] = (now, exists)
    return exists


# ------------------------------------------------------- Hidden (dismissed)

#: hidden.json lives in the data directory: hidden_file().  Tests pin it by
#: assigning aiwatch.HIDDEN_FILE.
#: Bumped when the key scheme changes.  v1 keyed dismissals by transcript path,
#: which never expired for Claude (a new uuid.jsonl every launch means the hidden
#: file gets no further activity) and expired instantly for Codex (the display
#: rollout is re-picked every round).  v1 files are dropped, not migrated.
HIDDEN_FORMAT = 2
#: Slack on the auto-restore comparison, so a same-second rewrite of the
#: transcript does not immediately un-hide what the user just dismissed.
HIDDEN_RESTORE_SLACK_S = 1
#: Reentrant on purpose.  load_hidden() and save_hidden() mutate _hidden_state,
#: so they take this themselves instead of trusting every caller to have taken
#: it first — and their callers (filter_hidden, hideable_session_id, /api/hide)
#: legitimately hold it already across a read-modify-write.
_hidden_lock = threading.RLock()
_hidden_state = {"path": None, "stamp": None, "data": {}}


def _hidden_stamp(path):
    try:
        st = os.stat(str(path))
    except OSError:
        return None
    return (st.st_mtime, st.st_size)


def _read_hidden_file(path, now=None):
    try:
        with open(str(path), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):  # json.JSONDecodeError is a ValueError
        return {}
    if not isinstance(data, dict) or data.get("version") != HIDDEN_FORMAT:
        return {}  # legacy / unknown layout: discard, never raise
    entries = data.get("hidden")
    if not isinstance(entries, dict):
        return {}
    cutoff = (time.time() if now is None else now) - SHOW_WINDOW_S
    clean = {}
    for key, value in entries.items():
        if not isinstance(key, str) or not key:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if value < cutoff:
            continue  # zombie: whatever it hid left the show window long ago
        clean[key] = float(value)
    return clean


def load_hidden():
    """The dismissal map, re-read only when hidden.json actually changed.

    A copy is returned, not the live cache: a caller that edits the map and then
    fails to write it (read-only disk, full volume) would otherwise leave memory
    and disk disagreeing with no way to notice, since the file's stamp is
    unchanged and nothing would ever re-read it.

    It writes _hidden_state, so it takes _hidden_lock itself.  Every call site
    happened to hold the lock already, which made the invariant true by accident
    and unenforced; the lock is reentrant so both spellings are correct.
    """
    with _hidden_lock:
        path = hidden_file()
        stamp = _hidden_stamp(path)
        if _hidden_state["path"] == str(path) and _hidden_state["stamp"] == stamp:
            return dict(_hidden_state["data"])
        data = _read_hidden_file(path)
        _hidden_state.update(path=str(path), stamp=stamp, data=data)
        return dict(data)


def save_hidden(data):
    """Write the dismissal map and re-stamp the cache; False if the write failed.

    Holds _hidden_lock for the same reason load_hidden() does: the file and the
    in-memory stamp have to move together or the next reader trusts a cache that
    no longer matches the disk.
    """
    with _hidden_lock:
        payload = {"version": HIDDEN_FORMAT, "hidden": dict(data)}
        path = hidden_file()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(str(path), "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=1)
        except OSError:
            return False
        _hidden_state.update(path=str(path),
                             stamp=_hidden_stamp(path), data=dict(data))
        return True


def filter_hidden(sessions):
    """Drop dismissed sessions; auto-restore any with new activity.

    Keyed by logical_id, so a dismissal survives Claude opening a new transcript
    and does not evaporate when Codex picks a different display rollout.
    The count reported back is what this round actually hid, not the size of the
    file: entries for sessions that were not even scanned used to inflate the
    dashboard's "已移除 N 位".
    """
    with _hidden_lock:
        hidden = load_hidden()
        changed = False
        kept, hidden_now = [], 0
        for s in sessions:
            key = s.get("logical_id")
            hidden_mtime = hidden.get(key)
            if hidden_mtime is None:
                kept.append(s)
                continue
            if s["mtime"] > hidden_mtime + HIDDEN_RESTORE_SLACK_S:
                del hidden[key]          # new activity -> comes back
                changed = True
                kept.append(s)
            else:
                hidden_now += 1
        if changed:
            save_hidden(hidden)
        return kept, hidden_now


def hideable_session_id(session_id):
    """True when the id names a session this host actually knows about.

    /api/hide used to write down whatever string it was handed, so anyone who
    could reach the port could grow hidden.json without limit — and every bogus
    entry then survived SHOW_WINDOW_S because the eviction rule is about age,
    not about whether anything could ever match it.  Ids that are already hidden
    stay acceptable so a second click (or a stale tab) is a no-op, not an error.

    current_snapshot() is the cache, not a scan: this runs inside a POST
    handler, so a click on 「移除」 must never be able to cost a full transcript
    and process enumeration.
    """
    for s in current_snapshot().get("sessions", []):
        if s.get("logical_id") == session_id:
            return True
    with _hidden_lock:
        return session_id in load_hidden()


# ------------------------------------------------------- Office (style + acks)

#: Same directory (and same test-isolation hook) as hidden.json: office_file();
#: tests pin aiwatch.OFFICE_FILE the way they pin HIDDEN_FILE.
OFFICE_FORMAT = 1
OFFICE_STYLES = ("win", "card", "compute", "flat", "sprite")
OFFICE_DEFAULT_STYLE = "win"
#: The view a page offers first: view id -> page path.  Not chosen yet = None.
OFFICE_VIEWS = {"starmap": "/starmap", "office": "/office", "list": "/"}
#: What a page opens for somebody who never chose a view.
OFFICE_DEFAULT_VIEW_PATH = OFFICE_VIEWS["starmap"]
_KEEP_VIEW = object()                    # save_office(): leave the stored view alone
#: Acks are only meaningful for these states (the ones that need the user).
OFFICE_ACK_STATES = ("WAITING", "STUCK")
MAX_OFFICE_STATE_LEN = 32
#: Reentrant: read-modify-write sections call load/save while holding it.
_office_lock = threading.RLock()


def _clean_office(data):
    """(style, acks) from whatever json.load() produced; defaults, never raises."""
    style, acks = OFFICE_DEFAULT_STYLE, {}
    if not isinstance(data, dict) or data.get("version") != OFFICE_FORMAT:
        return style, acks
    if data.get("style") in OFFICE_STYLES:
        style = data["style"]
    raw = data.get("acks")
    if isinstance(raw, dict):
        for key, entry in raw.items():
            if not (isinstance(key, str) and key and len(key) <= MAX_HIDE_ID_LEN
                    and isinstance(entry, dict)):
                continue
            state = entry.get("state")
            at = entry.get("at")
            if not (isinstance(state, str) and 0 < len(state) <= MAX_OFFICE_STATE_LEN
                    and plausible_ts(at) is not None):
                continue
            clean = {"state": state, "at": float(at)}
            for field in ("mtime", "since"):     # informational, optional
                if plausible_ts(entry.get(field)) is not None:
                    clean[field] = float(entry[field])
            acks[key] = clean
    return style, acks


def _finite_number(value):
    try:
        return math.isfinite(value)
    except OverflowError:                # an int too large for a float
        return False


def load_office_view():
    """The chosen view id from office.json, or None (never chosen / unreadable)."""
    with _office_lock:
        try:
            with open(str(office_file()), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError, RecursionError):
            return None
        if isinstance(data, dict) and data.get("version") == OFFICE_FORMAT:
            view = data.get("view")
            if isinstance(view, str) and view in OFFICE_VIEWS:
                return view
        return None


def office_panel_path():
    """The page a host program should open: the chosen view, else the star map."""
    view = load_office_view()
    return OFFICE_VIEWS[view] if view else OFFICE_DEFAULT_VIEW_PATH


def load_office():
    """(style, acks) from office.json; unreadable or corrupt -> the defaults."""
    with _office_lock:
        try:
            with open(str(office_file()), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError, RecursionError):
            return OFFICE_DEFAULT_STYLE, {}
        return _clean_office(data)


def save_office(style, acks, view=_KEEP_VIEW):
    """Atomic write (temp + rename); False if it failed.

    The stored view is kept unless `view` is given (a view id, or None to clear)."""
    with _office_lock:
        if view is _KEEP_VIEW:
            view = load_office_view()
        payload = {"version": OFFICE_FORMAT, "style": style, "acks": dict(acks)}
        if view is not None:
            payload["view"] = view
        path = office_file()
        tmp = Path(str(path) + ".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(str(tmp), "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=1)
            os.replace(str(tmp), str(path))
        except OSError:
            try:
                os.unlink(str(tmp))
            except OSError:
                pass
            return False
        return True


def prune_acks(acks, sessions, now=None):
    """The acks that still describe a live (logical_id, state, mtime) occurrence.

    A session present in `sessions` with another state invalidates its ack
    (mtime plays no part: a transcript can grow while the state stays).  One
    that is absent (remote unreachable, scan hiccup) keeps the ack until the
    ack itself is older than SHOW_WINDOW_S.
    """
    now = time.time() if now is None else now
    live = {s.get("logical_id"): s for s in sessions}
    kept = {}
    for key, ack in acks.items():
        s = live.get(key)
        if s is None:
            if ack["at"] >= now - SHOW_WINDOW_S:
                kept[key] = ack
        elif s.get("state") == ack["state"]:
            kept[key] = ack
    return kept


def prune_office_acks(sessions):
    """Drop invalid acks from office.json; returns (style, acks) after pruning."""
    with _office_lock:
        style, acks = load_office()
        kept = prune_acks(acks, sessions)
        if kept != acks:
            save_office(style, kept)
        return style, kept


# -------------------------------------------------------------- State machine

#: datetime.fromtimestamp() raises outside roughly this range, and a transcript
#: timestamp outside it is corrupt anyway.  1970-01-02 keeps the lower bound
#: clear of every UTC offset; 2100-01-01 is the upper bound.
MIN_PLAUSIBLE_TS = 86400.0
MAX_PLAUSIBLE_TS = 4102444800.0


def plausible_ts(value):
    """A timestamp we can safely do arithmetic and strftime on, else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    if value != value or value in (float("inf"), float("-inf")):
        return None                      # NaN / infinity
    if not MIN_PLAUSIBLE_TS <= value <= MAX_PLAUSIBLE_TS:
        return None
    return value


def _as_text(value):
    return value if isinstance(value, str) else None


#: Claude Code's own "entrypoint" values look like "claude-vscode", "cli".
_ENTRYPOINT_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")


def _as_entrypoint(value):
    """Which program hosts the session, or None.  a client app uses it to raise that
    program's window, so only a short lowercase token ever passes (fullmatch:
    `$` would let a trailing newline through)."""
    return (value if isinstance(value, str) and _ENTRYPOINT_RE.fullmatch(value)
            else None)


def normalize_session(s):
    """Fill in the fields the UI contract needs; return None for unusable rows.

    Remote hosts run whatever scanner version they were last given, so a session
    dict is untrusted input: it arrives as raw json.loads output from an SSH
    pipe.  Every field that is later indexed, formatted or turned into a Path is
    type- and range-checked here, because anything that raises below this point
    aborts the whole round — the background thread survives, but it never
    publishes again and the dashboard silently freezes on its last snapshot.
    """
    if not isinstance(s, dict):
        return None
    mtime = plausible_ts(s.get("mtime"))
    if mtime is None:
        return None                      # without a usable timestamp nothing works
    cwd = s.get("cwd")
    if cwd is not None and not isinstance(cwd, str):
        return None                      # Path(cwd) below would raise TypeError
    s = dict(s)
    s["mtime"] = mtime
    s["cwd"] = cwd or None
    base_agent = str(s.get("base_agent") or s.get("agent") or "?").split("@", 1)[0]
    host = s.get("host") if isinstance(s.get("host"), str) and s.get("host") else "local"
    s["base_agent"] = base_agent
    s["host"] = host
    s["agent"] = _as_text(s.get("agent")) or base_agent
    s["branch"] = _as_text(s.get("branch"))
    s["title"] = _as_text(s.get("title"))
    s["last_prompt"] = _as_text(s.get("last_prompt"))
    s["kind"] = _as_text(s.get("kind"))
    s["label"] = _as_text(s.get("label"))
    # Only Claude Code transcripts carry it; anything else (a remote Codex row
    # saying "cli") is null by contract.
    s["entrypoint"] = (_as_entrypoint(s.get("entrypoint"))
                       if base_agent.lower() == "claude" else None)
    subagents = s.get("subagents")
    s["subagents"] = (subagents if isinstance(subagents, int)
                      and not isinstance(subagents, bool) and subagents >= 0 else 0)
    # Background work still out when the turn ended (decides BACKGROUND vs
    # WAITING).  A start time only means something while work is pending.
    pending = s.get("background_pending")
    s["background_pending"] = (pending if isinstance(pending, int)
                               and not isinstance(pending, bool) and pending >= 0
                               else 0)
    for key in ("background_since", "background_agent_at", "background_command_at"):
        s[key] = plausible_ts(s.get(key)) if s["background_pending"] else None
    s["file"] = str(s.get("file") or "")
    s["file_mtime"] = plausible_ts(s.get("file_mtime")) or mtime
    s["stale"] = bool(s.get("stale"))
    s["stale_since"] = plausible_ts(s.get("stale_since"))
    project = _as_text(s.get("project"))
    s["project"] = project or (Path(s["cwd"]).name if s["cwd"] else base_agent)
    s["logical_id"] = logical_session_id(
        base_agent, s["cwd"], host, fallback=s["project"] or s["file"])
    return s


def dedupe_by_logical_id(sessions):
    """One card per office identity, newest wins.

    The same workspace can reach us through two encoded project dirs (case
    differences, a symlink, a changed drive letter); logical_id already carries
    agent and host, so different agents and different machines never merge.
    """
    best = {}
    for s in sessions:
        previous = best.get(s["logical_id"])
        if previous is None or s["mtime"] > previous["mtime"]:
            best[s["logical_id"]] = s
    return list(best.values())


_UNSET = object()


def snapshot(procs=_UNSET, now=None):
    """One full scan.  Called by the background scanner, --once and the tests."""
    now = time.time() if now is None else now
    raw = scan_claude(now) + scan_codex(now) + scan_antigravity(now)
    # `now` goes all the way down: remote_sessions() applies the stale TTL and
    # the show window itself, and reading the wall clock there while everything
    # else uses the injected value made one snapshot describe two instants.
    rsessions, remote_errors = remote_sessions(now)
    raw += rsessions

    sessions, malformed = [], 0
    for item in raw:
        normalized = normalize_session(item)
        if normalized is None:
            malformed += 1               # skip the row, never the whole round
            continue
        sessions.append(normalized)
    sessions = dedupe_by_logical_id(sessions)
    sessions, hidden_count = filter_hidden(sessions)

    if procs is _UNSET:
        procs = scan_processes()
    for s in sessions:
        # A workspace on an unmounted drive used to make its session disappear;
        # it is kept and flagged instead.
        s["cwd_missing"] = bool(s["cwd"]) and not s.get("remote") \
            and not workspace_exists(s["cwd"])
        proc_alive = None
        if procs is not None and not s.get("remote"):
            agent = s["base_agent"].lower()
            if agent == "antigravity":
                # Electron GUI: process cwd never matches the workspace,
                # so only check whether the app is running at all
                proc_alive = procs["alive"].get(agent)
            elif s["cwd"] and agent in procs["cwds"]:
                proc_alive = normalize_cwd(s["cwd"]) in procs["cwds"][agent]
        age = max(0, now - s["mtime"])
        if s["stale"]:
            # The host is unreachable, so mtime is frozen while now keeps moving.
            # Letting age grow would invent a STUCK that is really a dead SSH link.
            state = "OFFLINE"
        else:
            # A launch stamped slightly in the future is clock jitter, not a
            # reason to make the age negative.
            ages = [None if s[key] is None else max(0.0, now - s[key])
                    for key in ("background_agent_at", "background_command_at")]
            state = classify(s["kind"], age, proc_alive, *ages)
        emoji, state_zh = STATES[state]
        s.update(state=state, emoji=emoji, state_zh=state_zh,
                 age_s=int(age), proc_alive=proc_alive,
                 last_activity=datetime.fromtimestamp(s["mtime"]).strftime("%H:%M:%S"))
    order = {"STUCK": 0, "WAITING": 1, "WORKING": 2, "TOOL": 2, "BACKGROUND": 2,
             "IDLE": 3, "OFFLINE": 4}
    sessions.sort(key=lambda s: (order[s["state"]], s["age_s"]))
    return {"generated_at": datetime.fromtimestamp(now).strftime("%H:%M:%S"),
            "sessions": sessions, "attention": attention_queue(sessions),
            "hidden": hidden_count, "remote_errors": remote_errors,
            "malformed": malformed}


# --------------------------------------------------------- Background scanning

_snapshot_lock = threading.Lock()
#: Held only by readers that have to scan inline, so concurrent requests share
#: one scan instead of each running their own.  Never taken with _snapshot_lock.
_fallback_lock = threading.Lock()
_snapshot_state = {"snapshot": None, "at": 0.0, "version": 0, "error": None}
_scanner_thread = None


# ------------------------------------------------- Machine identity / endpoint
#
# Two computers that sync files both produce "local|claude|d:/...", so clients
# need to know which machine an answer came from.  The raw MachineGuid never
# leaves this process: only a truncated sha256 of it is exposed.

_machine_cache = None


def _raw_machine_id():
    if sys.platform == "win32":
        import winreg
        flags = winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Microsoft\Cryptography", 0, flags) as key:
            return str(winreg.QueryValueEx(key, "MachineGuid")[0])
    for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            value = Path(path).read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value:
            return value
    return "%012x" % uuid.getnode()


def machine_identity():
    """{"id": 16 hex chars, "name": hostname}; computed once, never raises."""
    global _machine_cache
    if _machine_cache is None:
        try:
            name = socket.gethostname()
        except Exception:
            name = "unknown"
        try:
            raw = _raw_machine_id()
        except Exception:
            raw = None
        if not raw:
            raw = name           # any read failure: fall back to the hostname
        _machine_cache = {"id": hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16],
                          "name": name}
    return dict(_machine_cache)


_endpoint_errors = set()
# main()'s finally and the console-close handler (its own OS thread) can both
# remove the file; unserialised, one of them hits a pending delete on Windows.
_endpoint_lock = threading.Lock()


def _endpoint_warn(what, exc):
    key = (what, type(exc).__name__)
    if key not in _endpoint_errors:
        _endpoint_errors.add(key)
        print("endpoint.json %s failed (%s: %s); continuing"
              % (what, type(exc).__name__, exc), file=sys.stderr)


def write_endpoint_file(port):
    """Atomically publish where this server listens.  Never raises."""
    try:
        home = aiwatch_home()
        home.mkdir(parents=True, exist_ok=True)
        payload = {"schema": 1, "port": port, "pid": os.getpid(),
                   "boot_id": BOOT_ID, "started_at": round(time.time(), 3),
                   "machine": machine_identity(),
                   "url": "http://127.0.0.1:%d" % port,
                   # Additive: which release answers here, and whether a host
                   # program ships this copy (embedded mode).
                   "watchdog_version": __version__,
                   "embedded": embedded_mode()}
        final = home / "endpoint.json"
        tmp = home / ("endpoint.json.%d.tmp" % os.getpid())
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, final)
    except Exception as exc:   # must never stop the service
        _endpoint_warn("write", exc)


#: How long remove_endpoint_file() keeps retrying a delete a reader is blocking.
ENDPOINT_REMOVE_RETRY_S = 1.0


def remove_endpoint_file():
    """Delete endpoint.json only if it still names this process.  Never raises."""
    with _endpoint_lock:
        try:
            path = aiwatch_home() / "endpoint.json"
            # A client reading the file right now holds it open and Windows
            # refuses the delete (WinError 32): retry for about a second.  The
            # owner is re-read before EVERY attempt, so a file another process
            # swapped in during the wait is never deleted.
            deadline = time.monotonic() + ENDPOINT_REMOVE_RETRY_S
            while True:
                try:
                    owner = json.loads(path.read_text(encoding="utf-8")).get("pid")
                except FileNotFoundError:
                    return              # never written, or already removed
                except ValueError:
                    return              # not ours to judge
                except PermissionError:
                    owner = os.getpid()  # unreadable for a moment: still ours to try
                if owner != os.getpid():
                    return
                try:
                    path.unlink()
                    return
                except FileNotFoundError:
                    return              # someone else removed it after our read
                except PermissionError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.05)
        except OSError as exc:
            _endpoint_warn("remove", exc)


# ------------------------------------------------------------ Event feed
#
# /api/events: state-transition events produced here so clients do not have to
# diff /api/status themselves.  Lock order: _snapshot_lock and _events_lock are
# never held at the same time; publish_snapshot() releases the former before
# taking the latter.
EVENTS_SCHEMA = 1
BOOT_ID = uuid.uuid4().hex
_events_lock = threading.Lock()
_events_state = {"seq": 0, "buf": deque(maxlen=EVENTS_MAX), "last": {},
                 "version": 0}   # newest snapshot version already diffed
#: Optional JSONL history (--events-log).  None = never write a file.  Written
#: inside _events_lock so line order equals seq order.
EVENTS_LOG_PATH = None
_events_log_errors = set()


def _append_events_log(event):
    """Append one event as a JSON line; never raises (an OSError is reported once)."""
    path = EVENTS_LOG_PATH
    if path is None:
        return
    try:
        path = str(path)
        try:
            if os.path.getsize(path) >= EVENTS_LOG_MAX_BYTES:
                os.replace(path, path + ".1")
        except FileNotFoundError:
            pass
        except OSError as exc:
            # Rotation failed (e.g. .1 held open elsewhere): keep appending to the
            # current file so no event is lost; the next event retries rotation.
            _events_log_warn("rotate", exc)
        line = json.dumps(dict(event, boot_id=BOOT_ID), ensure_ascii=False)
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write(line + "\n")
    except OSError as exc:
        _events_log_warn("write", exc)


def _events_log_warn(what, exc):
    key = (what, type(exc).__name__)
    if key not in _events_log_errors:
        _events_log_errors.add(key)
        print("events-log %s failed (%s: %s); further errors of this kind "
              "are silent" % (what, type(exc).__name__, exc), file=sys.stderr)


def reset_events():
    """Forget all events and remembered sessions (tests)."""
    with _events_lock:
        _events_state.update(seq=0, buf=deque(maxlen=EVENTS_MAX), last={}, version=0)
        _events_log_errors.clear()


def _detect_events(snap, at, version=None):
    """Diff a published snapshot against the previous one; append transitions.

    `version` is publish_snapshot()'s counter (monotonic, bumped under
    _snapshot_lock).  Two publishers can reach this function in the opposite
    order to their publish order; a snapshot older than one already diffed is
    dropped, because the newer one is what /api/status serves and the diff is
    taken against the last state seen, so the final state is never lost.
    """
    ts = round(float(at), 3)
    current = {}
    for s in snap.get("sessions") or []:
        lid = s.get("logical_id")
        if lid:
            current[lid] = s
    with _events_lock:
        if version is not None:
            if version <= _events_state["version"]:
                return
            _events_state["version"] = version
        last, buf = _events_state["last"], _events_state["buf"]

        def emit(lid, info, old, new):
            _events_state["seq"] += 1
            event = {"seq": _events_state["seq"], "ts": ts, "logical_id": lid,
                     "agent": info["agent"], "host": info["host"],
                     "project": info["project"], "from": old, "to": new,
                     "title": info["title"]}
            buf.append(event)
            _append_events_log(event)

        for lid, s in current.items():
            info = {k: s.get(k) for k in ("agent", "host", "project", "title")}
            info["state"] = s.get("state")
            prev = last.get(lid)
            if prev is None or prev["state"] != info["state"]:
                info["since"] = ts            # when this state began, as we saw it
                emit(lid, info, None if prev is None else prev["state"], info["state"])
            else:
                info["since"] = prev.get("since", ts)
            last[lid] = info
        for lid in [k for k in last if k not in current]:
            gone = last.pop(lid)
            emit(lid, gone, gone["state"], None)


def state_since(lid, state, default):
    """Epoch seconds at which `lid` entered `state` (as observed), else `default`."""
    with _events_lock:
        entry = _events_state["last"].get(lid)
    if entry and entry.get("state") == state and entry.get("since") is not None:
        return entry["since"]
    return default


def events_since(since=None):
    """Body of /api/events.  `since` is None (baseline) or a non-negative int."""
    with _events_lock:
        latest = _events_state["seq"]
        buf = list(_events_state["buf"])
    oldest = buf[0]["seq"] if buf else None
    events, truncated = [], False
    if since is not None:
        if since > latest:
            truncated = True              # Watchdog restarted: refetch /api/status
        else:
            events = [e for e in buf if e["seq"] > since]
            truncated = oldest is not None and oldest > since + 1
    return {"schema": EVENTS_SCHEMA, "boot_id": BOOT_ID, "latest_seq": latest,
            "oldest_seq": oldest, "truncated": truncated, "events": events,
            "machine": machine_identity(),
            # Additive: which page a host app should open for us.
            "panel": {"path": office_panel_path()},
            # Additive: the release (not a format number: that is `schema`)
            # and whether a host program ships this copy.
            "watchdog_version": __version__, "embedded": embedded_mode()}


def publish_snapshot(snap, at=None):
    with _snapshot_lock:
        _snapshot_state["snapshot"] = snap
        _snapshot_state["at"] = time.time() if at is None else at
        _snapshot_state["version"] += 1
        result = _snapshot_state["version"], _snapshot_state["at"]
    _detect_events(snap, result[1], result[0])   # after _snapshot_lock is released
    try:
        prune_office_acks(snap.get("sessions", []))
    except Exception:
        pass                              # an office.json problem never stops scanning
    return result


def local_scanner(stop=None, interval=LOCAL_SCAN_S, proc_interval=PROC_SCAN_S):
    """Own the local scan so an HTTP request costs a dict copy, not 150ms of I/O.

    Process liveness is refreshed on its own slower beat: whether an agent is
    running does not need transcript resolution, and enumerating processes is
    the most expensive part of a scan.  That beat is now enforced inside
    scan_processes() itself, so this loop only chooses the interval and every
    other caller gets the same protection.
    """
    while True:
        if stop is not None and stop.is_set():
            return
        try:
            publish_snapshot(snapshot(procs=scan_processes(ttl=proc_interval)))
            with _snapshot_lock:
                _snapshot_state["error"] = None
        except Exception as exc:  # a scan must never kill the thread silently
            message = "%s: %s" % (type(exc).__name__, exc)
            with _snapshot_lock:
                _snapshot_state["error"] = message[:200]
            traceback.print_exc()
        if stop is not None:
            if stop.wait(interval):
                return
        else:
            time.sleep(interval)


def scanner_running():
    thread = _scanner_thread
    return thread is not None and thread.is_alive()


def _read_snapshot_state():
    with _snapshot_lock:
        return (_snapshot_state["snapshot"], _snapshot_state["at"],
                _snapshot_state["version"], _snapshot_state["error"])


def current_snapshot():
    """What /api/status serves: the cache, plus how much to trust it."""
    cached, at, version, error = _read_snapshot_state()
    if cached is None or not scanner_running():
        # --once, the tests, and a scanner thread that died all scan inline.
        # Single-flight: without this, N tabs arriving before the first publish
        # (or any time after the scanner died) each run their own full scan,
        # which is the exact cost this cache exists to remove.
        with _fallback_lock:
            fresh, fresh_at, fresh_version, error = _read_snapshot_state()
            if fresh is not None and fresh_version != version:
                # somebody scanned while we queued for the lock; reuse that
                # rather than repeat it.  A caller that finds the version
                # unchanged still scans, so a request never serves data from
                # before its own arrival.
                cached, at, version = fresh, fresh_at, fresh_version
            else:
                cached = snapshot()
                version, at = publish_snapshot(cached)
    payload = dict(cached)
    age = max(0.0, time.time() - at)
    payload["cache_age_s"] = round(age, 3)
    payload["cache_stale"] = age > SNAPSHOT_STALE_S
    payload["scanner_error"] = error
    payload["version"] = version
    payload["machine"] = machine_identity()
    # Additive.  `version` above is the snapshot counter, not the release.
    payload["watchdog_version"] = __version__
    payload["capabilities"] = capabilities()
    return payload


def capabilities():
    """What this copy can tell, for clients that render accordingly.

    process_check: psutil is importable, so a session whose agent process is
    gone can be told apart (proc_alive true/false).  Without it proc_alive is
    always null and the state comes from the transcripts alone.
    """
    return {"process_check": psutil is not None}


# --------------------------------------------------------------------- Output

def fmt_age(sec):
    if sec < 60:
        return f"{sec} 秒前"
    if sec < 3600:
        return f"{sec // 60} 分鐘前"
    return f"{sec // 3600} 小時 {sec % 3600 // 60} 分前"


def print_once():
    snap = snapshot()
    if snap["attention"]:
        print("\n⚠ 需要你注意：")
        for s in snap["attention"]:
            print(f"  {s['emoji']} [{s['project']}] {s['agent']} — {s['state_zh']}（{fmt_age(s['age_s'])}）")
    print(f"\n{'狀態':<6} {'專案':<28} {'Agent':<7} {'最後活動':<14} 目前")
    print("-" * 100)
    for s in snap["sessions"]:
        label = s["label"] or "-"
        print(f"{s['emoji']} {s['state_zh']:<5} {s['project']:<28} {s['agent']:<7} "
              f"{fmt_age(s['age_s']):<14} {truncate(label, 60)}")
    print(f"\n共 {len(snap['sessions'])} 個 session（{snap['generated_at']}）")
    # The dashboard has shown these all along; --once used to drop them, so a
    # host that had been refusing our key for hours looked like a host with no
    # sessions on it.
    for problem in snap["remote_errors"]:
        print(f"⚠ 遠端: {problem}")


# ------------------------------------------------------------------ Web server

#: Pages are read from static_dir() (the resource directory; tests pin
#: aiwatch.STATIC_DIR).
STATIC_ROUTES = {          # whitelist: a request path never builds a file path
    "/": "dash.html",
    "/index.html": "dash.html",
    "/office": "office.html",
    "/office.html": "office.html",
    "/office/pixel": "office-pixel.html",
    "/starmap": "starmap.html",
    "/starmap.html": "starmap.html",
    "/static/watchdog.js": "static/watchdog.js",
}
CONTENT_TYPES = {".html": "text/html; charset=utf-8",
                 ".js": "application/javascript; charset=utf-8"}
#: Only reachable by adding a suffix to STATIC_ROUTES without adding it here.
#: Serving those bytes as an unknown type is a route that still works; a
#: KeyError inside read_static() is a 500 on a file that is sitting right there.
DEFAULT_CONTENT_TYPE = "application/octet-stream"

#: ?compact=1 (narrow embedded panels) only offers the star map: these pages
#: answer with a 302 to it, query string kept verbatim, whatever view is stored
#: in office.json.  Never an /api/* route, and nothing changes without compact=1.
#: static/watchdog.js does the same in the page (compactLeave) as a fallback.
COMPACT_REDIRECT_ROUTES = frozenset(("/", "/index.html", "/office", "/office.html", "/office/pixel"))
COMPACT_VIEW_PATH = "/starmap"


def compact_requested(query):
    """Same rule as compactFrom() in static/watchdog.js: any compact value exactly "1"."""
    try:
        return "1" in parse_qs(query or "", keep_blank_values=True).get("compact", [])
    except ValueError:
        return False


def compact_redirect(path):
    """Where a compact request for a non-star-map page goes (path + query), else None."""
    route, _, query = (path or "").partition("?")
    if route not in COMPACT_REDIRECT_ROUTES or not compact_requested(query):
        return None
    return COMPACT_VIEW_PATH + "?" + query


#: A POST body big enough to matter is a mistake or an attack; the real ones are
#: two short fields.  Declared lengths above this are refused before reading.
MAX_POST_BYTES = 64 * 1024
#: logical_id is host|agent|workspace; 256 is generous for a path.
MAX_HIDE_ID_LEN = 256
#: A dismissal records the activity it dismissed, so its timestamp cannot be in
#: the future.  Accepting one made the entry immortal in both directions: the
#: auto-restore compares `mtime > hidden_mtime` and never fires, and the
#: SHOW_WINDOW_S eviction compares `value < cutoff` and never fires either — one
#: POST could hide a workspace until someone edited the file by hand.  The slack
#: absorbs ordinary clock jitter between the browser's number and ours.
MAX_HIDE_FUTURE_S = 300
#: Which Host/Origin values this service answers to.  A page on any other origin
#: that resolves to this address (DNS rebinding) is not a client of ours:
#: /api/status carries absolute cwds and the first 140 characters of prompts.
LOCAL_HOSTNAMES = {"localhost", "127.0.0.1", "::1", "[::1]"}

_static_lock = threading.Lock()
_static_cache = {}  # name -> (stamp, body, ctype, etag)


def read_static(name):
    """(body, content_type, etag) for a whitelisted file under this directory.

    The bytes are kept in memory and only re-read when (mtime, size) moves, and
    the ETag is derived from the same stamp so an unchanged file can be answered
    with a 304 instead of its whole body on every poll.
    """
    path = static_dir().joinpath(*name.split("/"))
    st = os.stat(str(path))              # OSError -> 404, as before
    stamp = (st.st_mtime, st.st_size)
    with _static_lock:
        entry = _static_cache.get(name)
        if entry is not None and entry[0] == stamp:
            return entry[1], entry[2], entry[3]
    body = path.read_bytes()
    ctype = CONTENT_TYPES.get(path.suffix, DEFAULT_CONTENT_TYPE)
    etag = '"%x-%x"' % (int(st.st_mtime * 1000), st.st_size)
    with _static_lock:
        _static_cache[name] = (stamp, body, ctype, etag)
    return body, ctype, etag


def split_host_port(value):
    """('127.0.0.1', 8787) from a Host/Origin authority; port None when absent.

    A malformed port comes back as -1, which matches no listening socket.
    """
    value = (value or "").strip()
    if value.startswith("["):                       # [::1]:8787
        end = value.find("]")
        if end < 0:
            return value, -1
        host, rest = value[:end + 1], value[end + 1:]
        port = rest[1:] if rest.startswith(":") else ""
    elif value.count(":") == 1:
        host, _, port = value.partition(":")
    else:
        host, port = value, ""            # bare name, or a bare IPv6 literal
    if not port:
        return host, None
    try:
        return host, int(port)
    except ValueError:
        return host, -1


class Handler(BaseHTTPRequestHandler):
    #: Keep-alive: the dashboard polls every 5s and used to pay for a new TCP
    #: connection and a new thread each time.  Every response below carries a
    #: Content-Length (send_error does too, and it closes the connection), so
    #: no reply can leave the client waiting for a body that never ends.
    protocol_version = "HTTP/1.1"
    #: ...and an idle kept-alive connection cannot hold its thread for ever.
    timeout = 60
    #: Headers and body are two separate writes on an unbuffered socket.  With
    #: Nagle on and the connection now staying open, the body waited for the
    #: delayed ACK of the header packet: ~200ms added to every single reply.
    disable_nagle_algorithm = True

    #: Whether this *request* has already put a reply on the wire.  One handler
    #: instance now serves many requests (keep-alive), so it is reset per call.
    responded = False
    #: Whether the request body has been read (or drained) already.
    body_consumed = False
    #: Longest unread body an early refusal will still swallow, and how long it
    #: waits for it.  Beyond either, the connection is simply closed.
    DRAIN_MAX_BYTES = 64 * 1024
    DRAIN_TIMEOUT_S = 1.0

    def parse_request(self):
        # One handler serves many keep-alive requests.  Until this request's
        # headers have parsed there is nothing known to drain: a refusal of a
        # bad request line must not drain by the previous request's headers
        # (and base parse_request closes the connection on such errors).
        self.body_consumed = True
        ok = super().parse_request()
        if ok:
            self.body_consumed = False
        return ok

    def drain_request_body(self):
        """Read what the client already sent before answering with an error.

        Replying and closing with request bytes still unread makes Windows (and
        Linux) answer the close with a RST, which can destroy the reply in the
        client's receive buffer: the client sees WinError 10053/10054 instead of
        the 403.  So every early refusal swallows the body first; a body too big
        or too slow to be worth it just gets `Connection: close` (and may still
        see that RST: a deliberate trade-off).  DRAIN_TIMEOUT_S bounds the whole
        drain, not each read, so a client trickling bytes cannot hold it open.
        """
        if self.body_consumed:
            return
        self.body_consumed = True
        headers = getattr(self, "headers", None)
        if headers is None:
            return                       # the request line itself was refused
        if headers.get("Transfer-Encoding"):
            self.close_connection = True  # cannot be drained without parsing chunks
            return
        raw = (headers.get("Content-Length") or "").strip()
        if not raw:
            return
        if not raw.isdigit() or int(raw) > self.DRAIN_MAX_BYTES:
            self.close_connection = True
            return
        remaining = int(raw)
        deadline = time.monotonic() + self.DRAIN_TIMEOUT_S
        try:
            while remaining > 0:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                self.connection.settimeout(left)
                # read1: at most one recv, so `left` really caps the wait.
                chunk = self.rfile.read1(remaining)
                if not chunk:
                    break
                remaining -= len(chunk)
        except (OSError, ValueError):
            self.close_connection = True
        finally:
            try:
                self.connection.settimeout(self.timeout)
            except OSError:
                pass
        if remaining > 0:
            self.close_connection = True

    def send_error(self, code, message=None, explain=None):
        # Every refusal path (400/403/404/413/415/500...) goes through here.
        try:
            self.drain_request_body()
        except Exception:
            self.close_connection = True
        super().send_error(code, message, explain)

    def do_GET(self):
        # An unexpected exception used to escape into socketserver, drop the
        # connection and make every open tab report "cannot reach the service".
        # A 500 lets the dashboard say "this round failed" instead.
        self.responded = False
        try:
            self.handle_get()
        except Exception:
            self.fail_500()

    def do_POST(self):
        self.responded = False
        try:
            self.handle_post()
        except Exception:
            self.fail_500()

    def note_activity(self):
        """Tell the idle-exit watcher a client is still using us.

        Called only once the Host (and for POST the Origin) guard passed, so a
        refused request cannot keep a --background copy from idling out.
        """
        note = getattr(self.server, "note_activity", None)
        if note is not None:
            note()

    # ------------------------------------------------------------- guards

    def host_allowed(self):
        """Reject a Host that is not this service's own address.

        Nothing stops a hostile domain from resolving to 127.0.0.1; the browser
        then treats the reply as same-origin with that domain and can read it.
        A missing Host is not that attack (browsers always send one).
        """
        headers = self.headers.get_all("Host") or []
        if len(headers) > 1:
            return False          # two Hosts: only ever an attempt to confuse us
        if not headers or not headers[0]:
            return True
        host, port = split_host_port(headers[0])
        if host.casefold() not in LOCAL_HOSTNAMES:
            return False
        return port is None or port == self.server.server_port

    def origin_allowed(self):
        """Origin, when the client sends one, must be this service.

        A cross-origin POST from a page always carries it; curl and the tests do
        not, and `null` (sandboxed iframe, redirected form) is never us.
        """
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        scheme, sep, authority = origin.partition("://")
        if not sep or scheme.casefold() not in ("http", "https"):
            return False                 # "null" and anything else opaque
        host, port = split_host_port(authority)
        if host.casefold() not in LOCAL_HOSTNAMES:
            return False
        default = 443 if scheme.casefold() == "https" else 80
        return (port if port is not None else default) == self.server.server_port

    def read_json_body(self):
        """(body, error_sent).  Enforces the size cap before reading anything."""
        raw = (self.headers.get("Content-Length") or "").strip()
        # Digits only, deliberately stricter than int(): that accepts "-1"
        # (rfile.read(-1) reads to EOF, i.e. the client decides how much memory
        # this costs), and also "1_0", " 1 " and "+1", which are three different
        # lengths to three different parsers.
        if raw and not raw.isdigit():
            self.send_error(400, "bad Content-Length")
            return None, True
        length = int(raw) if raw else 0
        if length > MAX_POST_BYTES:
            self.send_error(413, "payload too large")
            return None, True
        self.body_consumed = True         # read below, whatever it yields
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, OSError):     # json.JSONDecodeError is a ValueError
            self.send_error(400)
            return None, True
        if not isinstance(body, dict):
            self.send_error(400)
            return None, True
        return body, False

    # ------------------------------------------------------------- routes

    def handle_get(self):
        if not self.host_allowed():
            self.send_error(403, "host not allowed")
            return
        self.note_activity()
        route = self.path.split("?")[0]
        if route == "/api/status":
            body = json.dumps(current_snapshot(), ensure_ascii=False).encode("utf-8")
            # Per-request, per-user and already seconds old: no cache anywhere.
            self.respond(body, "application/json; charset=utf-8",
                         headers=(("Cache-Control", "no-store"),))
            return
        if route == "/api/events":
            # No CORS headers and no OPTIONS, same as /api/status (see handle_post).
            query = self.path.partition("?")[2]
            values = parse_qs(query, keep_blank_values=True).get("since")
            since = None
            if values:
                if not re.fullmatch(r"[0-9]{1,18}", values[0]):
                    self.send_error(400, "since must be a non-negative integer")
                    return
                since = int(values[0])
            # Result unused: forces an inline scan (tests, --once) so events exist.
            current_snapshot()
            body = json.dumps(events_since(since), ensure_ascii=False).encode("utf-8")
            self.respond(body, "application/json; charset=utf-8",
                         headers=(("Cache-Control", "no-store"),))
            return
        if route == "/api/office":
            # Prunes first, so a page never sees an ack the session has outlived.
            style, acks = prune_office_acks(current_snapshot().get("sessions", []))
            body = json.dumps({"schema": 1, "style": style, "acks": acks,
                               "view": load_office_view()},
                              ensure_ascii=False).encode("utf-8")
            self.respond(body, "application/json; charset=utf-8",
                         headers=(("Cache-Control", "no-store"),))
            return
        location = compact_redirect(self.path)
        if location is not None:
            self.respond(b"", "text/plain; charset=utf-8", code=302,
                         headers=(("Location", location), ("Cache-Control", "no-store")))
            return
        if route not in STATIC_ROUTES:
            self.send_error(404)
            return
        name = STATIC_ROUTES[route]
        try:
            body, ctype, etag = read_static(name)
        except OSError:
            self.send_error(404, name + " not found")
            return
        if self.etag_matches(etag):
            self.send_not_modified(etag)
            return
        self.respond(body, ctype, headers=(("ETag", etag),
                                           ("Cache-Control", "no-cache")))

    def reply_json(self, payload):
        self.respond(json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                     "application/json; charset=utf-8",
                     headers=(("Cache-Control", "no-store"),))

    def handle_office_post(self, route, body):
        """/api/office/{style,view,ack,unack}; the common POST guards already passed."""
        if route == "/api/office/style":
            style = body.get("style")
            if not (isinstance(style, str) and style in OFFICE_STYLES):
                self.send_error(400, "unknown style")
                return
            with _office_lock:
                _, acks = load_office()
                if not save_office(style, acks):
                    self.send_error(500, "could not save office.json")
                    return
            self.reply_json({"ok": True, "style": style})
            return
        if route == "/api/office/view":
            view = body.get("view")
            if not (isinstance(view, str) and view in OFFICE_VIEWS):
                self.send_error(400, "unknown view")
                return
            with _office_lock:
                style, acks = load_office()
                if not save_office(style, acks, view):
                    self.send_error(500, "could not save office.json")
                    return
            self.reply_json({"ok": True, "view": view})
            return
        session_id = body.get("id")
        if not (isinstance(session_id, str) and session_id
                and len(session_id) <= MAX_HIDE_ID_LEN):
            self.send_error(400)
            return
        if route == "/api/office/unack":
            with _office_lock:
                style, acks = load_office()
                if acks.pop(session_id, None) is not None:
                    if not save_office(style, acks):
                        self.send_error(500, "could not save office.json")
                        return
            self.reply_json({"ok": True})
            return
        # /api/office/ack
        state, mtime = body.get("state"), body.get("mtime")
        # mtime is optional and never used for validity; if sent it must be finite.
        if not (isinstance(state, str) and 0 < len(state) <= MAX_OFFICE_STATE_LEN
                and (mtime is None
                     or (not isinstance(mtime, bool)
                         and isinstance(mtime, (int, float))
                         and _finite_number(mtime)))):
            self.send_error(400)
            return
        # The cache, like /api/hide: a click must not cost a full scan.  Warm it
        # outside the lock (it may scan inline); inside, re-read the cache only
        # (it takes _snapshot_lock briefly, never an office -> scan-lock chain).
        warm = current_snapshot()
        with _office_lock:
            cached = _read_snapshot_state()[0]
            sessions = (cached if cached is not None else warm).get("sessions", [])
            match = next((s for s in sessions if s.get("logical_id") == session_id), None)
            if (match is None or match.get("state") != state
                    or state not in OFFICE_ACK_STATES):
                self.send_error(409, "session is not in a state that can be acknowledged")
                return
            style, acks = load_office()
            old = acks.get(session_id)
            if old is not None and old["state"] == state:
                self.reply_json({"ok": True})      # idempotent: keep the first ack
                return
            now = time.time()
            acks[session_id] = {"state": state, "mtime": float(match["mtime"]),
                                "since": state_since(session_id, state, now),
                                "at": now}
            if not save_office(style, prune_acks(acks, sessions)):
                self.send_error(500, "could not save office.json")
                return
        self.reply_json({"ok": True})

    def handle_post(self):
        if not self.host_allowed():
            self.send_error(403, "host not allowed")
            return
        if not self.origin_allowed():
            self.send_error(403, "origin not allowed")
            return
        self.note_activity()
        # application/json is not a CORS "simple request", so a browser has to
        # pre-flight it; we answer no OPTIONS, so the pre-flight fails and the
        # POST is never sent.  Without this, any page the user visits could
        # empty their hidden list with a form-style fetch.
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
        if ctype.casefold() != "application/json":
            self.send_error(415, "Content-Type must be application/json")
            return
        body, failed = self.read_json_body()
        if failed:
            return
        route = self.path.split("?")[0]
        if route == "/api/hide":
            # Keyed by logical_id, not by transcript path: see HIDDEN_FORMAT.
            session_id, mtime = body.get("id"), body.get("mtime")
            if not (isinstance(session_id, str) and session_id
                    and len(session_id) <= MAX_HIDE_ID_LEN
                    and plausible_ts(mtime) is not None
                    and mtime <= time.time() + MAX_HIDE_FUTURE_S):
                self.send_error(400)
                return
            if not hideable_session_id(session_id):
                # Otherwise the file grows without limit on ids nothing will
                # ever match, and every one of them survives for SHOW_WINDOW_S.
                self.send_error(400, "unknown session id")
                return
            with _hidden_lock:
                hidden = dict(load_hidden())
                hidden[session_id] = float(mtime)
                save_hidden(hidden)
        elif route == "/api/unhide":
            with _hidden_lock:
                save_hidden({})
        elif route in ("/api/office/style", "/api/office/view",
                       "/api/office/ack", "/api/office/unack"):
            self.handle_office_post(route, body)
            return
        elif route == "/api/shutdown":
            # `aiwatch.py --stop`: the background instance has no console to
            # press Ctrl+C in.  Same guards as every POST, so no web page can
            # reach it.  Reply first; shutdown() waits for serve_forever to end.
            self.respond(json.dumps({"ok": True}).encode("utf-8"),
                         "application/json; charset=utf-8",
                         headers=(("Cache-Control", "no-store"),))
            print("shutdown requested over HTTP; exiting", flush=True)
            threading.Thread(target=self.server.shutdown, daemon=True,
                             name="http-shutdown").start()
            return
        else:
            self.send_error(404)
            return
        self.respond(json.dumps({"ok": True}).encode("utf-8"),
                     "application/json; charset=utf-8",
                     headers=(("Cache-Control", "no-store"),))

    # ------------------------------------------------------------- replies

    def etag_matches(self, etag):
        header = self.headers.get("If-None-Match")
        if not header:
            return False
        for candidate in header.split(","):
            candidate = candidate.strip()
            if candidate in ("*", etag, "W/" + etag):
                return True
        return False

    def send_not_modified(self, etag):
        # 304 is defined to have no body, so keep-alive stays framed without a
        # Content-Length.
        self.responded = True
        self.send_response(304)
        self.send_header("ETag", etag)
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

    def respond(self, body, ctype, code=200, headers=()):
        self.responded = True
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for key, value in headers:
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def fail_500(self):
        traceback.print_exc(file=sys.stderr)
        if getattr(self, "responded", False):
            # The failure happened while writing a reply that was already on the
            # wire.  A second complete response on the same connection is a
            # protocol violation, not a recovery: close instead.
            self.close_connection = True
            return
        try:
            self.send_error(500, "watchdog internal error")
        except Exception:
            pass  # client already gone / headers already sent

    def log_message(self, *args):
        pass


#: Errors that mean "the client went away", not "the watchdog is broken".
CLIENT_HUNG_UP = (ConnectionResetError, ConnectionAbortedError,
                  BrokenPipeError, TimeoutError)


class WatchdogServer(ThreadingHTTPServer):
    """ThreadingHTTPServer that stays quiet when a client drops a connection.

    HTTP/1.1 keep-alive (Handler.protocol_version) means browsers and pollers
    hold sockets open and close them on their own schedule; socketserver's
    default handle_error prints a full traceback for every one of those, which
    buries real errors under hours of noise.
    """

    #: Windows: SO_REUSEADDR lets a second process bind a port that is already
    #: listening, so two copies started at once by two client apps
    #: both "succeeded", both polled every SSH host and overwrote each other's
    #: endpoint.json.  Without it the second bind fails with WSAEADDRINUSE.
    #: Elsewhere SO_REUSEADDR only skips TIME_WAIT and stays on.
    allow_reuse_address = sys.platform != "win32"

    #: _monotonic() of the last request; the idle-exit watcher reads it.
    last_activity = 0.0

    def note_activity(self):
        self.last_activity = _monotonic()

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], CLIENT_HUNG_UP):
            return
        super().handle_error(request, client_address)


def _raise_keyboard_interrupt(signum, frame):
    # Runs on the main thread between bytecodes, never inside a lock we hold
    # across a signal; the unwinding goes through main()'s finally.
    raise KeyboardInterrupt


def _install_shutdown_signals():
    """Make Ctrl+Break / SIGTERM take the Ctrl+C path.

    On Windows the CRT turns CTRL_BREAK_EVENT (and CTRL_CLOSE_EVENT) into
    SIGBREAK, whose default action ends the process without running `finally`,
    which would leave endpoint.json behind.  Console close still cannot rely on
    this path; see _install_console_close_handler.  Returns
    [(signum, previous_handler)].
    """
    names = ["SIGBREAK"] if hasattr(signal, "SIGBREAK") else ["SIGTERM"]
    saved = []
    for name in names:
        signum = getattr(signal, name)
        try:
            saved.append((signum, signal.signal(signum, _raise_keyboard_interrupt)))
        except (ValueError, OSError):    # not the main thread / unsupported
            pass
    return saved


# Console control events after which Windows terminates the process as soon as
# the handlers return.  The CRT folds them into SIGBREAK, but the resulting
# KeyboardInterrupt only reaches main()'s finally once the main thread wakes up
# (it is usually blocked in serve_forever's select), which is too late: closing
# the console window used to leave endpoint.json behind.  LOGOFF/SHUTDOWN are
# listed for completeness only: a process that has loaded user32/gdi32 (this one
# has) is not sent them and is simply terminated at logoff or shutdown.
_CTRL_CLOSE_EVENT = 2
_CTRL_LOGOFF_EVENT = 5
_CTRL_SHUTDOWN_EVENT = 6
_TERMINATING_CTRL_EVENTS = (_CTRL_CLOSE_EVENT, _CTRL_LOGOFF_EVENT, _CTRL_SHUTDOWN_EVENT)


def _on_console_ctrl(ctrl_type):
    """Native console handler body: clean up synchronously, then pass it on.

    Returns False so the next handler (the CRT's SIGBREAK/SIGINT translation)
    still runs; Ctrl+C and Ctrl+Break keep taking the normal finally path.
    In-flight ssh children share the console and receive the same event.
    """
    try:
        if ctrl_type in _TERMINATING_CTRL_EVENTS:
            remove_endpoint_file()
    except Exception:    # never let a failure here swallow the event
        pass
    return False


def _install_console_close_handler():
    """Windows only: run _on_console_ctrl before the CRT's console handler.

    Handlers run last-registered-first, and the CRT's is in place since
    interpreter start-up.  Returns a callable that unregisters it (a no-op when
    nothing was installed).  Never raises.
    """
    if sys.platform != "win32":
        return lambda: None
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)(_on_console_ctrl)
        if not kernel32.SetConsoleCtrlHandler(callback, True):
            return lambda: None
    except Exception:
        return lambda: None

    def uninstall():
        try:
            kernel32.SetConsoleCtrlHandler(callback, False)
        except Exception:
            pass
    uninstall.callback = callback    # the ctypes thunk must outlive registration
    return uninstall


# ------------------------------------------------------------ Background launch
#
# Programs that read the watchdog may start it themselves when none answers.
# install.json tells them how; --background is
# the windowless mode they start.  install.json sits beside endpoint.json.

#: --background stops after this long without a single HTTP request.  The apps
#: poll every few seconds while they are open, so it only fires once they close.
BACKGROUND_IDLE_EXIT_S = 600
#: How often the idle-exit watcher looks at the clock.
IDLE_CHECK_S = 5
#: aiwatch.log moves to aiwatch.log.1 at start-up once it is this big.
BACKGROUND_LOG_MAX_BYTES = 1024 * 1024
#: How long the "is one already running?" probe waits for an answer.
ALIVE_PROBE_TIMEOUT_S = 2.0
#: How long --stop waits for the running copy to finish its cleanup.
STOP_WAIT_S = 20.0
DEFAULT_PORT = 8787

#: Loopback only, and never through an HTTP proxy from the environment.
_local_http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_install_errors = set()


def _install_warn(what, exc):
    key = (what, type(exc).__name__)
    if key not in _install_errors:
        _install_errors.add(key)
        print("install.json %s failed (%s: %s); continuing"
              % (what, type(exc).__name__, exc), file=sys.stderr)


def background_launch_command():
    """argv that starts this copy as a windowless background server.

    Always an absolute python.exe / pythonw.exe (never py.exe, a .bat or a
    shell line), so the apps can validate it and run it without a shell.
    Never used in embedded mode: there sys.executable is the host program and
    this file may not even exist (self_launch_command() is used instead).
    """
    if embedded_mode():
        raise RuntimeError("embedded mode: install.json is not written and this "
                           "copy is started with %s, not python.exe" % ENV_SELF_LAUNCH)
    exe = Path(sys.executable).resolve()
    if sys.platform == "win32":
        quiet = exe.with_name("pythonw.exe")
        if quiet.is_file():
            exe = quiet               # no console window flashes up
    return [str(exe), "-X", "utf8", str(Path(__file__).resolve()),
            "--serve", "--background"]


def read_install_file():
    """install.json as a dict, or None when it is missing or unreadable."""
    try:
        data = json.loads((aiwatch_home() / "install.json")
                          .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


#: How long write_install_file() keeps retrying a rename a reader is blocking.
INSTALL_REPLACE_RETRY_S = 1.0


def _schema_int(data):
    """install.json's schema as an int, or None.  bool (True == 1 in Python),
    floats and strings are not schema numbers: they are unrecognisable."""
    schema = data.get("schema") if isinstance(data, dict) else None
    return schema if type(schema) is int else None


def is_schema_1(data):
    return _schema_int(data) == 1


def newer_install_schema(data):
    """True when install.json was written by a newer Watchdog (integer schema > 1)."""
    schema = _schema_int(data)
    return schema is not None and schema > 1


def write_install_file(autostart=None, relocate=True, overwrite_newer=False):
    """Atomically publish how to start this copy.  Never raises.

    autostart=None keeps whatever the previous file said (default on), so a
    user's "off" survives every later start.  relocate=False (--autostart
    alone) only flips the switch of a usable existing registration: launch, cwd
    and any other field stay as they are, so the registration is not moved to
    the copy that happened to run the command.  With no usable file there is
    nothing to move, and this copy is registered.  A file with a newer integer
    schema belongs to a newer Watchdog and is never overwritten unless
    overwrite_newer (only --register, the user's explicit request).  Returns
    True on success.  Embedded mode never writes it: the registration belongs
    to the copy the user installed, and a host knows how to start its own.
    """
    if embedded_mode():
        key = ("embedded",)
        if key not in _install_errors:
            _install_errors.add(key)
            print("install.json is left alone: this copy is embedded (%s=1)"
                  % ENV_EMBEDDED, file=sys.stderr)
        return False
    tmp = None
    try:
        previous = read_install_file()
        if newer_install_schema(previous) and not overwrite_newer:
            key = ("newer-schema", previous.get("schema"))
            if key not in _install_errors:
                _install_errors.add(key)
                print("install.json was written by a newer Watchdog (schema %d); "
                      "not overwritten.  To change the registration run "
                      "--register." % previous.get("schema"), file=sys.stderr)
            return False
        if autostart is None:
            autostart = (previous or {}).get("autostart") is not False
        home = aiwatch_home()
        home.mkdir(parents=True, exist_ok=True)
        if not relocate and previous is not None and is_schema_1(previous):
            payload = dict(previous, autostart=bool(autostart),
                           updated_at=round(time.time(), 3))
        else:
            payload = {"schema": 1, "launch": background_launch_command(),
                       "cwd": str(_SCRIPT_DIR), "autostart": bool(autostart),
                       "updated_at": round(time.time(), 3)}
        final = home / "install.json"
        tmp = home / ("install.json.%d.tmp" % os.getpid())
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        # A reader that has install.json open makes the rename fail on Windows.
        deadline = time.monotonic() + INSTALL_REPLACE_RETRY_S
        while True:
            try:
                os.replace(tmp, final)
                return True
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)
    except Exception as exc:   # must never stop the service
        _install_warn("write", exc)
        if tmp is not None:
            try:
                tmp.unlink()
            except OSError:
                pass
        return False


def install_file_problem(data):
    """Why install.json cannot start a watchdog, or None when it can.

    The checks client apps are expected to make before using install.json, so that
    `--start` and every --serve judge a registration the way the apps will.
    """
    if not isinstance(data, dict):
        return "is missing or unreadable"
    if not is_schema_1(data):
        return "has an unknown schema"
    launch = data.get("launch")
    if not (isinstance(launch, list) and launch
            and all(isinstance(a, str) for a in launch)):
        return "has no usable launch list"
    exe = launch[0]
    if not (os.path.isabs(exe) and os.path.isfile(exe)):
        return "names an interpreter that does not exist: %s" % exe
    if (sys.platform == "win32"
            and os.path.basename(exe).lower() not in ("python.exe", "pythonw.exe")):
        return "names an interpreter that is not python(w).exe: %s" % exe
    scripts = [a for a in launch[1:] if a.lower().endswith("aiwatch.py")]
    if not scripts:
        return "names no aiwatch.py"
    for script in scripts:
        if not (os.path.isabs(script) and os.path.isfile(script)):
            return "names an aiwatch.py that does not exist: %s" % script
    if "--serve" not in launch or "--background" not in launch:
        return "lacks --serve --background"
    cwd = data.get("cwd")
    if not (isinstance(cwd, str) and os.path.isabs(cwd) and os.path.isdir(cwd)):
        return "names a working directory that does not exist: %s" % cwd
    return None


def _spawn_detached(argv, cwd, env=None):
    """Start argv with no window, outside our process group (and job, if allowed)."""
    import subprocess
    kwargs = dict(cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                  stderr=subprocess.DEVNULL, close_fds=True)
    if env is not None:
        kwargs["env"] = env
    if sys.platform != "win32":
        return subprocess.Popen(argv, start_new_session=True, **kwargs)
    base = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_NO_WINDOW)
    try:
        # Survive a launcher that runs inside a kill-on-close job object.
        return subprocess.Popen(argv, creationflags=base
                                | subprocess.CREATE_BREAKAWAY_FROM_JOB, **kwargs)
    except OSError:
        return subprocess.Popen(argv, creationflags=base, **kwargs)


def start_registered_watchdog(port):
    """--start: run the copy install.json names, unless one already answers."""
    running = running_watchdog_url(port)
    if running is not None:
        print("the watchdog at %s is already running" % running)
        return 0
    data = read_install_file()
    problem = install_file_problem(data)
    if problem:
        print("cannot start: %s %s; run `aiwatch.py --register` in the copy to use"
              % (aiwatch_home() / "install.json", problem), file=sys.stderr)
        return 1
    try:
        _spawn_detached(data["launch"], data["cwd"])
    except OSError as exc:
        print("cannot start %s (%s)" % (data["launch"][0], exc), file=sys.stderr)
        return 1
    return _await_started(port, data["cwd"])


def _await_started(port, where):
    """After a spawn: 0 once a watchdog answers, 1 if none does in STOP_WAIT_S."""
    deadline = _monotonic() + STOP_WAIT_S
    while _monotonic() < deadline:
        url = running_watchdog_url(port)
        if url is not None:
            print("started the watchdog in %s: %s" % (where, url))
            return 0
        time.sleep(0.25)
    print("started %s but nothing answers yet; see %s"
          % (where, aiwatch_home() / "aiwatch.log"), file=sys.stderr)
    return 1


def self_launch_command():
    """(argv, None) from AIWATCH_SELF_LAUNCH, else (None, why it is unusable).

    The only way an embedded copy ever starts itself: the host's own program
    and arguments, to which the aiwatch arguments are appended.  Deliberately
    no fallback to sys.executable or a python(w).exe next to it: in a frozen
    host that is the host itself, and this module may not exist as a file.
    """
    raw = os.environ.get(ENV_SELF_LAUNCH)
    if not raw:
        return None, ("%s is not set: an embedded watchdog is only ever started "
                      "the way its host starts it, never with python.exe"
                      % ENV_SELF_LAUNCH)
    try:
        value = json.loads(raw)
    except ValueError:
        return None, "%s is not valid JSON" % ENV_SELF_LAUNCH
    if not (isinstance(value, list) and value
            and all(isinstance(a, str) and a for a in value)):
        return None, ("%s must be a JSON array of non-empty strings"
                      % ENV_SELF_LAUNCH)
    if not (os.path.isabs(value[0]) and os.path.isfile(value[0])):
        return None, ("%s[0] is not an existing absolute path: %s"
                      % (ENV_SELF_LAUNCH, value[0]))
    return value, None


#: Variables a frozen (PyInstaller) parent leaves in its environment.  A child
#: that inherits them can mistake itself for a sub-process of the parent's
#: bundle; PYINSTALLER_RESET_ENVIRONMENT asks the bootloader to start afresh.
_FROZEN_ENV_PREFIXES = ("_PYI_", "_MEI")
_FROZEN_ENV_NAMES = ("PYTHONHOME", "PYTHONPATH")


def _self_launch_env():
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith(_FROZEN_ENV_PREFIXES)
           and k.upper() not in _FROZEN_ENV_NAMES}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def start_embedded_watchdog(port):
    """--start in embedded mode: run AIWATCH_SELF_LAUNCH + --serve --background.

    install.json is never consulted: it names the copy the user installed, not
    this one.  Same "one that answers wins" rule as start_registered_watchdog().
    """
    running = running_watchdog_url(port)
    if running is not None:
        print("the watchdog at %s is already running" % running)
        return 0
    launch, problem = self_launch_command()
    if problem:
        return _startup_failed(EXIT_SELF_LAUNCH, problem, port or None)
    argv = list(launch) + ["--serve", "--background"]
    if port:
        argv += ["--port", str(port)]
    home = aiwatch_home()
    try:
        home.mkdir(parents=True, exist_ok=True)
        cwd = str(home)
    except OSError:
        cwd = None
    try:
        _spawn_detached(argv, cwd, env=_self_launch_env())
    except OSError as exc:
        return _startup_failed(EXIT_SELF_LAUNCH, "cannot start %s (%s)"
                               % (launch[0], exc), port or None)
    return _await_started(port, launch[0])


def _is_loopback_url(url):
    return isinstance(url, str) and re.match(
        r"^http://(127\.0\.0\.1|localhost):\d{1,5}/?$", url) is not None


def watchdog_alive(url, timeout=None):
    """True when url answers /api/events the way a watchdog does."""
    timeout = ALIVE_PROBE_TIMEOUT_S if timeout is None else timeout
    try:
        with _local_http.open(url.rstrip("/") + "/api/events",
                              timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return False
    return (isinstance(data, dict) and type(data.get("schema")) is int
            and data.get("schema") == 1 and bool(data.get("boot_id")))


def _endpoint_record():
    try:
        data = json.loads((aiwatch_home() / "endpoint.json")
                          .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def running_watchdog_url(port):
    """URL of a watchdog already answering on this machine, or None.

    endpoint.json first (it may name another port), then the port we would use.
    """
    candidates = []
    url = _endpoint_record().get("url")
    if _is_loopback_url(url):
        candidates.append(url)
    if port:
        candidates.append("http://127.0.0.1:%d" % port)
    candidates = list(dict.fromkeys(candidates))
    if len(candidates) < 2:
        return candidates[0] if candidates and watchdog_alive(candidates[0]) else None
    # Side by side: Windows takes ~2 s to report a refused loopback connect, and
    # a stale endpoint.json naming another port used to cost that twice before
    # a --background copy could start (pushing its started_at past what a host
    # accepts as "this process just started").  The order still decides.
    answers = [False] * len(candidates)

    def probe(index, target):
        answers[index] = watchdog_alive(target)
    threads = [threading.Thread(target=probe, args=(i, u), daemon=True,
                                name="alive-probe") for i, u in enumerate(candidates)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    for url, alive in zip(candidates, answers):
        if alive:
            return url
    return None


def stop_running_watchdog(port):
    """--stop: ask the running copy to exit and wait for its cleanup.  0 = ok."""
    url = running_watchdog_url(port)
    if url is None:
        print("no watchdog is running")
        return 0
    pid = _endpoint_record().get("pid")
    request = urllib.request.Request(
        url.rstrip("/") + "/api/shutdown", data=b"{}", method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with _local_http.open(request, timeout=5) as resp:
            resp.read()
    except Exception as exc:
        print("could not stop the watchdog at %s (%s)" % (url, exc), file=sys.stderr)
        return 1
    deadline = _monotonic() + STOP_WAIT_S
    while _monotonic() < deadline:
        # endpoint.json is removed at the very end of the cleanup
        if (not watchdog_alive(url, timeout=1.0)
                and (pid is None or _endpoint_record().get("pid") != pid)):
            print("stopped the watchdog at %s" % url)
            return 0
        time.sleep(0.25)
    print("the watchdog at %s was asked to stop but is still running" % url,
          file=sys.stderr)
    return 1


def idle_watcher(server, idle_s, stop):
    """--idle-exit: shut the server down after idle_s seconds without a request."""
    while not stop.wait(IDLE_CHECK_S):
        idle = _monotonic() - server.last_activity
        if idle >= idle_s:
            print("no request for %ds; exiting (--idle-exit)" % idle, flush=True)
            server.shutdown()        # serve_forever returns; main()'s finally runs
            return


def _open_append_atomic(path):
    """Open `path` for text appending so that concurrent writers never lose lines.

    The C runtime's O_APPEND on Windows is "seek to the end, then write": two
    processes can pick the same end and overwrite each other (several copies
    starting together lost log lines in most rounds).  Asking Windows for
    FILE_APPEND_DATA *without* FILE_WRITE_DATA makes every write land at the
    current end of file atomically, whatever offset the writer thinks it is at.
    Share mode allows readers, other writers and rename/delete (rotation).
    """
    if sys.platform != "win32":
        return open(path, "a", encoding="utf-8", buffering=1)
    import ctypes
    import msvcrt
    from ctypes import wintypes
    FILE_APPEND_DATA, SHARE_ALL, OPEN_ALWAYS = 0x4, 0x1 | 0x2 | 0x4, 4
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                     wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                                     wintypes.HANDLE]
    handle = kernel32.CreateFileW(str(path), FILE_APPEND_DATA, SHARE_ALL, None,
                                  OPEN_ALWAYS, 0x80, None)
    if handle in (None, wintypes.HANDLE(-1).value):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_APPEND | os.O_WRONLY)
    except OSError:
        kernel32.CloseHandle(handle)
        raise
    return os.fdopen(fd, "a", encoding="utf-8", buffering=1)


def _rotate_background_log(home, path):
    """aiwatch.log -> aiwatch.log.1 once it is big enough.  Best effort, and
    serialised by a lock file so two copies starting together cannot rotate
    twice (the second would shove the brand-new log into .1)."""
    lock = home / "aiwatch.log.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:                                  # a crashed copy left it behind
            if time.time() - lock.stat().st_mtime > 30:
                lock.unlink()
        except OSError:
            pass
        return
    except OSError:
        return
    try:
        os.close(fd)
        # Re-checked under the lock: whoever held it before us may have rotated.
        if path.stat().st_size >= BACKGROUND_LOG_MAX_BYTES:
            os.replace(path, home / "aiwatch.log.1")
    except OSError:
        pass
    finally:
        try:
            lock.unlink()
        except OSError:
            pass


def _open_background_log():
    """aiwatch.log beside endpoint.json, for a copy that has no console.

    Returns an open text file, or None (prints then go nowhere: pythonw has no
    stdout).  Rotation is best effort: another copy may hold the file open.
    """
    try:
        home = aiwatch_home()
        home.mkdir(parents=True, exist_ok=True)
        path = home / "aiwatch.log"
        try:
            if path.stat().st_size >= BACKGROUND_LOG_MAX_BYTES:
                _rotate_background_log(home, path)
        except OSError:
            pass
        return _open_append_atomic(path)
    except OSError:
        return None


# ------------------------------------------------------------------ Exit codes
#
# Exit codes of the service commands.  0 and 2 keep the meaning they always had.

EXIT_OK = 0
#: --register / --autostart / --stop / --start (--restart) did not succeed.
EXIT_FAILED = 1
#: --serve could not bind its port.  With --background only when a watchdog
#: answers on that port: another copy won a simultaneous start, not a failure.
EXIT_PORT_TAKEN = 2
#: --background: the port is held by something that does not answer like a
#: watchdog (or cannot be bound at all).
EXIT_PORT_UNAVAILABLE = 3
#: --background: the resource directory lacks a page or a scanner source.
EXIT_RESOURCES_MISSING = 4
#: --background: the data directory cannot be created or written.
EXIT_DATA_DIR_UNWRITABLE = 5
#: Embedded mode: AIWATCH_SELF_LAUNCH is missing or unusable (--background,
#: --start, --restart).
EXIT_SELF_LAUNCH = 6
#: --background: any other error before the server started.
EXIT_STARTUP_ERROR = 7

#: Why the last start failed, in the data directory; removed by a start that
#: succeeds.  {"schema": 1, "at": float, "code": int, "message": str,
#: "watchdog_version": str, "pid": int (the process that wrote it),
#: "port": int or null (the port the writer tried to use; null when it had
#: none, e.g. a command-line error)}.
#: A running service removes an error written by another process for ITS OWN
#: port (see sweep_stale_last_error); an error for another port, or without a
#: port, is somebody else's and stays.
LAST_ERROR_NAME = "last_error.json"
LAST_ERROR_SCHEMA = 1
#: message is cut to this many characters, so the file stays far below the
#: 64 KB a host is prepared to read.
LAST_ERROR_MESSAGE_MAX = 2000
#: After a lost bind, how long to keep asking whether a watchdog holds the
#: port: the copy that won a simultaneous start answers after its first scan.
PORT_OWNER_PROBE_S = 6.0
#: How often a running service removes a last_error.json written by another
#: process (a start that lost the race and gave up after PORT_OWNER_PROBE_S).
LAST_ERROR_SWEEP_S = 2.0

#: The log file a --background copy writes its stdout/stderr to, while it runs.
_background_log = None


def _replace_retrying(tmp, final, budget=1.0):
    """os.replace, retried for a moment while a reader holds `final` open."""
    deadline = time.monotonic() + budget
    while True:
        try:
            os.replace(tmp, final)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)


def write_last_error(code, message, port=None):
    """Atomically record why a start failed.  Never raises; True when written.

    port: the port the failed start tried to use, or None when it had none."""
    tmp = None
    try:
        home = aiwatch_home()
        home.mkdir(parents=True, exist_ok=True)
        payload = {"schema": LAST_ERROR_SCHEMA, "at": round(time.time(), 3),
                   "code": int(code),
                   "message": str(message)[:LAST_ERROR_MESSAGE_MAX],
                   "watchdog_version": __version__, "pid": os.getpid(),
                   "port": port if isinstance(port, int)
                   and not isinstance(port, bool) else None}
        tmp = home / ("%s.%d.tmp" % (LAST_ERROR_NAME, os.getpid()))
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        _replace_retrying(tmp, home / LAST_ERROR_NAME)
        return True
    except Exception:
        if tmp is not None:
            try:
                tmp.unlink()
            except OSError:
                pass
        return False


def clear_last_error(port=None):
    """A start on `port` succeeded: remove the failure record that describes it.

    Same rule as sweep_stale_last_error(): a record for another port, or one
    without a port, belongs to somebody else and stays; one that cannot be read
    goes.  Without a port nothing is known to match, so only unreadable files go."""
    path = aiwatch_home() / LAST_ERROR_NAME
    try:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("not an object")
        except (ValueError, UnicodeDecodeError):
            data = None
        if data is not None:
            theirs = data.get("port")
            if (port is None or isinstance(theirs, bool)
                    or not isinstance(theirs, int) or theirs != port):
                return
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        print("%s could not be removed (%s: %s); continuing"
              % (LAST_ERROR_NAME, type(exc).__name__, exc), file=sys.stderr)


def sweep_stale_last_error(port):
    """Service side: remove a last_error.json that this service contradicts.

    Removed: a file this process did not write whose "port" equals `port` (a
    start that lost to this service), and a file that cannot be read.  Kept:
    this process's own file, one for another port (two services may share the
    data directory) and one with "port": null.  Returns True when a file was
    removed."""
    path = aiwatch_home() / LAST_ERROR_NAME
    try:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("not an object")
        except (ValueError, UnicodeDecodeError):
            data = None                  # unreadable or foreign: stale as well
        if data is not None:
            if data.get("pid") == os.getpid():
                return False
            theirs = data.get("port")
            if (isinstance(theirs, bool) or not isinstance(theirs, int)
                    or theirs != port):
                return False
        path.unlink()
        return True
    except OSError:
        return False


def _last_error_sweeper(stop, port, interval=None):
    while not stop.wait(LAST_ERROR_SWEEP_S if interval is None else interval):
        sweep_stale_last_error(port)


def _append_log_line(line):
    """One line in aiwatch.log from a process that does not log there anyway."""
    log = _open_background_log()
    if log is None:
        return
    try:
        log.write("--- %s pid %d: %s\n" % (datetime.now().isoformat(timespec="seconds"),
                                          os.getpid(), line))
    except (OSError, ValueError):
        pass
    finally:
        try:
            log.close()
        except (OSError, ValueError):
            pass


def _startup_failed(code, message, port=None):
    """Report a start that did not happen and return its exit code.

    One line in aiwatch.log (a --background copy already sends stderr there)
    and last_error.json, so a host program without a console can say why.
    With the data directory unwritable neither can be written: the exit code
    is then all there is.
    """
    message = " ".join(str(message).split())[:LAST_ERROR_MESSAGE_MAX]   # one line
    line = "startup failed (exit %d): %s" % (code, message)
    print(line, file=sys.stderr)
    log = _background_log
    if log is None or sys.stderr is not log:
        _append_log_line(line)
    write_last_error(code, message, port)
    return code


def resource_problem():
    """Why the resource directory cannot serve, or None.

    A --background copy refuses to start without every page in STATIC_ROUTES
    and both remote scanner sources (remotes.json is re-read every round, so
    they can be needed at any time)."""
    root = resource_dir()
    if not root.is_absolute():
        return "%s must be an absolute path: %s" % (ENV_RESOURCES, root)
    missing = [name for name in sorted(set(STATIC_ROUTES.values()))
               if not static_dir().joinpath(*name.split("/")).is_file()]
    missing += [path.name for path in (scanner_core_file(), remote_scanner_file())
                if not path.is_file()]
    if missing:
        return "the resource directory %s lacks %s" % (root, ", ".join(missing))
    return None


def data_dir_problem():
    """Why the data directory cannot be written, or None."""
    home = aiwatch_home()
    probe = home / (".write-test.%d.tmp" % os.getpid())
    try:
        home.mkdir(parents=True, exist_ok=True)
        probe.write_bytes(b"")
        probe.unlink()
    except OSError as exc:
        return "the data directory %s is not writable (%s: %s)" % (
            home, type(exc).__name__, exc)
    return None


def _background_start_problem():
    """(exit code, reason) for what keeps a --background copy from working.

    Checked after the "is one already running?" probe and before the bind, so
    a copy that cannot work never takes the port from one that could.
    """
    problem = data_dir_problem()
    if problem:
        return EXIT_DATA_DIR_UNWRITABLE, problem
    problem = resource_problem()
    if problem:
        return EXIT_RESOURCES_MISSING, problem
    if embedded_mode():
        # Not needed to serve, but an embedded copy that could not be started
        # again (--start, --restart) is a host bug best found at the first start.
        _launch, problem = self_launch_command()
        if problem:
            return EXIT_SELF_LAUNCH, problem
    return None


def _port_answers_as_watchdog(port, budget=None):
    """True once http://127.0.0.1:port answers /api/events like a watchdog."""
    if not port:
        return False
    budget = PORT_OWNER_PROBE_S if budget is None else budget
    url = "http://127.0.0.1:%d" % port
    deadline = _monotonic() + budget
    while True:
        if watchdog_alive(url):
            return True
        if _monotonic() >= deadline:
            return False
        time.sleep(0.25)


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        # An embedding host has no console: leave the reason where it looks.
        # The exit code stays 2.
        if embedded_mode():
            write_last_error(EXIT_PORT_TAKEN, "bad command line: " + message)
        super().error(message)


def _build_parser():
    ap = _Parser(prog="aiwatch.py", description="AI Agent Watchdog")
    ap.add_argument("--once", action="store_true", help="print one snapshot and exit")
    ap.add_argument("--serve", action="store_true", help="run web dashboard")
    ap.add_argument("--port", type=int, default=None,
                    help="port to serve on (default 8787); with --stop, only "
                         "this port is tried besides endpoint.json")
    ap.add_argument("--events-log", nargs="?", const=str(aiwatch_home() / "events.jsonl"),
                    default=None, metavar="PATH",
                    help="--serve only: append state events to a JSONL file "
                         "(default PATH: events.jsonl in the data directory, "
                         "AIWATCH_HOME or ~/.aiwatch)")
    ap.add_argument("--background", action="store_true",
                    help="--serve the way client apps start it: exit "
                         "at once if a watchdog already answers, log to aiwatch.log "
                         "beside endpoint.json, stop after --idle-exit (default %ds)"
                         % BACKGROUND_IDLE_EXIT_S)
    ap.add_argument("--idle-exit", type=float, default=None, metavar="SECONDS",
                    help="--serve only: stop after SECONDS without a request "
                         "(0 = never; default never, %d with --background)"
                         % BACKGROUND_IDLE_EXIT_S)
    ap.add_argument("--register", action="store_true",
                    help="write install.json so the apps can start this copy, then exit")
    ap.add_argument("--autostart", choices=("on", "off"),
                    help="let the apps start the watchdog (on) or not (off), then exit")
    ap.add_argument("--stop", action="store_true",
                    help="ask the running watchdog to exit, then exit")
    ap.add_argument("--start", action="store_true",
                    help="start the copy install.json names in the background, unless "
                         "one already runs, then exit")
    ap.add_argument("--restart", action="store_true",
                    help="--stop, then --start (e.g. after updating the files)")
    ap.add_argument("--version", action="version", version=__version__,
                    help="print the release (%s) and exit" % __version__)
    return ap


def _manage(args):
    """--register / --autostart / --stop / --start / --restart; the exit code."""
    code = EXIT_OK
    if args.register or args.autostart:
        autostart = None if args.autostart is None else args.autostart == "on"
        if embedded_mode():
            print("this copy is embedded (%s=1) and never writes install.json: the "
                  "registration belongs to the copy the user installed; run "
                  "--register in that copy" % ENV_EMBEDDED, file=sys.stderr)
            code = EXIT_FAILED
        elif not args.register and newer_install_schema(read_install_file()):
            print("%s was written by a newer Watchdog (schema %r); not touching "
                  "it.  Update this copy, or run --register to replace it."
                  % (aiwatch_home() / "install.json",
                     read_install_file().get("schema")), file=sys.stderr)
            code = EXIT_FAILED
        elif write_install_file(autostart, relocate=bool(args.register),
                                overwrite_newer=bool(args.register)):
            state = "on" if (read_install_file() or {}).get("autostart") else "off"
            print("%s written (autostart %s)"
                  % (aiwatch_home() / "install.json", state))
        else:
            code = EXIT_FAILED
    # Stopping is destructive: with AIWATCH_HOME pointing elsewhere (tests,
    # a second copy) and no --port, it must not fall back to whatever
    # happens to answer on 8787.
    if args.port is not None:
        probe_port = args.port
    else:
        probe_port = None if os.environ.get(ENV_HOME) else DEFAULT_PORT
    if args.stop:
        code = max(code, stop_running_watchdog(probe_port))
    if args.start and code == 0:
        if embedded_mode():
            code = start_embedded_watchdog(probe_port)
        else:
            code = start_registered_watchdog(probe_port)
    return code


def main(argv=None):
    """The command line, callable from a host program: main(["--serve", ...]).

    argv defaults to sys.argv[1:] (only then is sys.argv read).  Returns 0 when
    it ends normally; every other ending raises SystemExit with the exit code
    (see Exit codes), exactly as the command line would end, and so do --help,
    --version and argparse's usage errors.
    """
    global _background_log
    if argv is not None:
        argv = [str(a) for a in argv]
    args = _build_parser().parse_args(argv)
    shown = sys.argv[1:] if argv is None else argv
    if args.restart:
        args.stop = args.start = True
    if args.register or args.autostart or args.stop or args.start:
        raise SystemExit(_manage(args))
    if args.port is None:
        args.port = DEFAULT_PORT
    if args.background:
        args.serve = True
    if args.once or not args.serve:
        migrate_legacy_data()
        if load_remotes():
            poll_all_remotes()
        print_once()
        return EXIT_OK
    log, saved = None, None
    if args.background:
        log = _open_background_log()
        if log is not None:
            saved = (sys.stdout, sys.stderr)
            sys.stdout = sys.stderr = log
            _background_log = log
            print("--- %s pid %d: %s" % (datetime.now().isoformat(timespec="seconds"),
                                         os.getpid(), " ".join(shown)))
    try:
        code = _serve(args)
    finally:
        _background_log = None
        if saved is not None:
            sys.stdout, sys.stderr = saved
        if log is not None:
            log.close()
    if code:
        raise SystemExit(code)
    return EXIT_OK


def _serve(args):
    """Run the server until it is told to stop; returns the exit code.

    This process is the server: --background never re-launches itself, so the
    pid a host spawned is the pid endpoint.json names.
    """
    global _scanner_thread, EVENTS_LOG_PATH
    if args.background:
        try:
            running = running_watchdog_url(args.port)
            if running is not None:
                # Started by an app that could not reach us a moment ago, or by
                # both apps at once: the other copy serves, this one leaves
                # untouched.
                print("a watchdog already answers at %s; exiting" % running)
                return EXIT_OK
            problem = _background_start_problem()
        except Exception as exc:
            traceback.print_exc()
            return _startup_failed(EXIT_STARTUP_ERROR,
                                   "%s: %s" % (type(exc).__name__, exc),
                                   args.port or None)
        if problem is not None:
            return _startup_failed(*problem, args.port or None)
    idle_s = args.idle_exit if args.idle_exit is not None else (
        BACKGROUND_IDLE_EXIT_S if args.background else 0)
    try:
        # Bind before anything else starts: a copy that loses the port to another
        # must leave without one SSH connection and without touching endpoint.json.
        server = WatchdogServer(("127.0.0.1", args.port), Handler)
    except OSError as exc:
        if not args.background:
            print("cannot listen on 127.0.0.1:%d (%s); is another watchdog running?"
                  % (args.port, exc), file=sys.stderr)
            return EXIT_PORT_TAKEN
        if _port_answers_as_watchdog(args.port):
            print("cannot listen on 127.0.0.1:%d (%s): a watchdog already answers "
                  "there; exiting" % (args.port, exc), file=sys.stderr)
            return EXIT_PORT_TAKEN
        return _startup_failed(
            EXIT_PORT_UNAVAILABLE,
            "cannot listen on 127.0.0.1:%d (%s), and whatever holds the port does "
            "not answer like a watchdog" % (args.port, exc),
            args.port or None)
    clear_last_error(server.server_address[1])
    migrate_legacy_data()
    if args.events_log:
        EVENTS_LOG_PATH = args.events_log
    # The registration is sticky: running some other copy by hand (a USB stick
    # used for development) must not make the apps start that one next time.
    # Only --register moves it, unless the registered copy is gone.  An
    # embedded copy never reads or writes it here (write_install_file()
    # refuses as well): its sys.executable is the host program.
    if not embedded_mode() and install_file_problem(read_install_file()) is not None:
        write_install_file()
    stop = threading.Event()
    threading.Thread(target=_last_error_sweeper,
                     args=(stop, server.server_address[1]), daemon=True,
                     name="last-error-sweeper").start()
    _reopen_inflight()
    threading.Thread(target=remote_poller, args=(stop,), daemon=True).start()
    _scanner_thread = threading.Thread(target=local_scanner, args=(stop,),
                                       daemon=True, name="local-scanner")
    _scanner_thread.start()
    print(f"AI Agent Watchdog dashboard: http://localhost:{args.port}")
    # Handlers first, so a signal can never land in the gap before the cleanup
    # path exists.  The endpoint file is written only after the bind succeeded;
    # port 0 resolves to the real port.
    restore = _install_shutdown_signals()
    uninstall_console_handler = _install_console_close_handler()
    if idle_s > 0:
        server.note_activity()       # the idle clock starts now
        threading.Thread(target=idle_watcher, args=(server, idle_s, stop),
                         daemon=True, name="idle-exit").start()
    try:
        write_endpoint_file(server.server_address[1])
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()        # first: pollers must not start new work from here on
        _kill_inflight_remotes()
        remove_endpoint_file()
        uninstall_console_handler()
        for signum, old in restore:
            try:
                signal.signal(signum, old)
            except (ValueError, OSError):
                pass
        server.server_close()
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
