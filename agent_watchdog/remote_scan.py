# -*- coding: utf-8 -*-
"""Remote-side entry point for AI Agent Watchdog.

aiwatch concatenates scanner_core.py with this file and pipes the result into
`python3 -` on the remote host (see aiwatch.remote_script), so all the parsing
lives in scanner_core and this file only reads argv and prints JSON.
Stdlib only.
"""
import json
import sys
import time
from pathlib import Path

# Keep this import on one line: aiwatch.remote_script() drops it by prefix when
# it concatenates scanner_core.py with this file for the remote host.
from scanner_core import REMOTE_BEGIN_MARK, REMOTE_END_MARK, SHOW_WINDOW_S, scan_claude_dir, scan_codex_dir  # noqa: E501

#: Bumped if the shape of the object between the markers ever changes.
REMOTE_PROTOCOL = 1


def main():
    window = SHOW_WINDOW_S
    if len(sys.argv) > 1:
        try:
            window = float(sys.argv[1])
        except ValueError:
            pass
    now = time.time()
    home = Path.home()
    sessions = scan_codex_dir(home / ".codex" / "sessions", now, window)
    sessions.extend(scan_claude_dir(home / ".claude" / "projects", now, window))
    # "now" is read again *after* the scan: the local side subtracts it from its
    # own clock to get the offset between the two machines, and a reading taken
    # before a multi-second scan would charge that scan to the clock skew.
    payload = {"protocol": REMOTE_PROTOCOL, "now": time.time(),
               "scan_at": now, "sessions": sessions}
    # Markers on their own lines, so whatever the login shell printed before or
    # after this can be discarded without guessing where the JSON starts.
    sys.stdout.write("%s\n%s\n%s\n" % (REMOTE_BEGIN_MARK,
                                       json.dumps(payload, ensure_ascii=False),
                                       REMOTE_END_MARK))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
