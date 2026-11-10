# -*- coding: utf-8 -*-
"""The agent state machine, kept free of HTTP, UI and scanning.

`aiwatch.py` imports this module (and re-exports its names).  It has to stay
stdlib-only and must never import aiwatch.  It is NOT part of the remote script
(aiwatch.py concatenates only scanner_core.py + remote_scan.py), because a remote
host only reports raw transcript facts; the verdict is always computed locally.
"""

ACTIVE_S = 60                  # activity within this = working
STUCK_TOOL_S = 5 * 60          # pending tool with no output this long = stuck
WAITING_TO_IDLE_S = 30 * 60    # waiting for user this long = idle
OFFLINE_S = 4 * 3600           # nothing this long = offline
#: A session whose process we cannot see is only *downgraded* to OFFLINE once it
#: has also been silent this long.  psutil regularly cannot match an agent to its
#: workspace (a Codex started by the VS Code extension reports the IDE's cwd),
#: and a session that vanishes from the attention queue after a minute vanishes
#: exactly when it is most likely to be stuck.
PROC_DEAD_GRACE_S = 15 * 60
#: A turn that ended with background work still out (an async Agent, a
#: run_in_background command) is BACKGROUND, not WAITING: Claude Code wakes the
#: session with a <task-notification> by itself when the work finishes.  Past
#: this age of the oldest unfinished launch we stop believing the wake-up is
#: coming (the session may have been closed under it) and fall back.
BACKGROUND_MAX_S = 2 * 3600
#: A background *command* may never end (dev server, log tail) and then no
#: notification ever arrives, so it is trusted far shorter than an async agent.
BACKGROUND_COMMAND_MAX_S = 15 * 60

STATES = {
    "WORKING":  ("🟢", "執行中"),
    "TOOL":     ("🔵", "跑工具/測試"),
    "WAITING":  ("🟡", "等待你回覆"),
    "BACKGROUND": ("⏳", "背景執行中"),
    "IDLE":     ("🟣", "閒置"),
    "STUCK":    ("🔴", "疑似卡住"),
    "OFFLINE":  ("⚫", "已離線"),
}

#: The two states worth interrupting the user for.  BACKGROUND is deliberately
#: not one of them: nothing is asked of the user until the wake-up arrives.
ATTENTION_STATES = ("WAITING", "STUCK")


def classify(kind, age, proc_alive, agent_age=None, command_age=None):
    """The state of one session.

    `agent_age` / `command_age` are how long ago (seconds) the NEWEST async
    agent / background command that has not reported back was launched, or None
    when there is none / it is unknown.  Any one of them still inside its own
    limit (BACKGROUND_MAX_S, BACKGROUND_COMMAND_MAX_S; both inclusive) turns a
    would-be WAITING or IDLE into BACKGROUND; both OFFLINE rules are checked
    first and always win.
    """
    if age > OFFLINE_S:
        return "OFFLINE"
    # proc_alive is False is a *downgrade signal only*: psutil frequently cannot
    # tie an agent process to the workspace it serves.  proc_alive is None
    # (psutil missing / not applicable) must never affect the verdict.
    if proc_alive is False and age > PROC_DEAD_GRACE_S:
        return "OFFLINE"
    if age <= ACTIVE_S:
        return "TOOL" if kind in ("tool", "tool_result") else "WORKING"
    if kind == "thinking":
        # long reasoning legitimately goes minutes without writing
        return "STUCK" if age > 2 * STUCK_TOOL_S else "WORKING"
    if kind in ("tool", "tool_result"):
        return "STUCK" if age > STUCK_TOOL_S else "WORKING"
    if kind == "assistant_text":
        if ((agent_age is not None and agent_age <= BACKGROUND_MAX_S)
                or (command_age is not None and command_age <= BACKGROUND_COMMAND_MAX_S)):
            return "BACKGROUND"
        return "WAITING" if age <= WAITING_TO_IDLE_S else "IDLE"
    if kind == "user_input":
        # user spoke but the agent produced nothing since
        return "STUCK" if age > STUCK_TOOL_S else "WORKING"
    return "IDLE"


def attention_queue(sessions):
    """The rows that go into 「需要你注意」.

    `not s["stale"]` is a second line of defence and is kept on purpose.  Today
    it can never fire: snapshot() forces every stale row to OFFLINE before this
    runs, so a stale row cannot be WAITING or STUCK.  But this is the one list
    the user acts on, and the failure it guards against — a card demanding
    attention because an SSH link died, not because anything is wrong on the
    other side — is worse than a card that is quietly missing.  It is a named
    function rather than a comprehension inside snapshot() so the rule can be
    tested directly instead of only through a path that cannot reach it.
    """
    return [s for s in sessions
            if s["state"] in ATTENTION_STATES and not s["stale"]]
