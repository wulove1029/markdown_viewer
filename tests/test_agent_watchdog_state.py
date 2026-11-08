"""Pure-logic tests for the Watchdog state machine (no Qt, injected clock)."""

import pytest

from app.agent_watchdog_state import (
    BASELINE,
    INVALID,
    MIXED,
    OK,
    RESYNC,
    Backoff,
    WatchdogState,
    dump_last_toast,
    is_loopback_url,
    load_last_toast,
)

BOOT = "591cbaa4eeb947ec838b3d9659bb7913"


class Clock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def ev(seq, lid, to, frm="WORKING", project=None, agent="Claude"):
    return {
        "seq": seq, "ts": 1794076831.692, "logical_id": lid, "agent": agent,
        "host": "local", "project": project or lid.split("/")[-1],
        "from": frm, "to": to, "title": None,
    }


def payload(events=(), latest=None, boot=BOOT, truncated=False):
    events = list(events)
    if latest is None:
        latest = max([e["seq"] for e in events], default=0)
    return {"schema": 1, "boot_id": boot, "latest_seq": latest,
            "oldest_seq": 1, "truncated": truncated, "events": events,
            "machine": {"id": "ab" * 8, "name": "PC"}}  # unknown key tolerated


def status(*items):
    return {"generated_at": 1, "version": 7, "sessions": [], "hidden": 0,
            "attention": [{"logical_id": i, "state": s, "project": i,
                           "agent": "Claude", "host": "local"}
                          for i, s in items]}


def make(clock=None, **kw):
    clock = clock or Clock()
    state = WatchdogState(clock=clock, **kw)
    state.apply_events(payload(latest=5))  # baseline
    return state, clock


def test_baseline_records_cursor_and_requests_status():
    state = WatchdogState(clock=Clock())
    assert state.apply_events(payload(latest=5)) == BASELINE
    assert (state.boot_id, state.seq) == (BOOT, 5)
    assert state.attention == {}


@pytest.mark.parametrize("events,expected_waiting,expected_stuck", [
    ([ev(6, "a", "WAITING")], 1, 0),
    ([ev(6, "a", "WAITING"), ev(7, "b", "STUCK")], 1, 1),
    ([ev(6, "a", "WAITING"), ev(7, "a", "WORKING", "WAITING")], 0, 0),
    ([ev(7, "a", "WORKING", "WAITING"), ev(6, "a", "WAITING")], 0, 0),  # by seq
    ([ev(6, "a", "TOOL")], 0, 0),
])
def test_normal_events_maintain_a_set(events, expected_waiting, expected_stuck):
    state, _ = make()
    assert state.apply_events(payload(events)) == OK
    assert state.badge_text().count("等待中") == expected_waiting
    assert state.badge_text().count("可能卡住") == expected_stuck


def test_set_not_counter_replayed_event_is_idempotent():
    state, _ = make()
    state.apply_events(payload([ev(6, "a", "WAITING")]))
    state.apply_events(payload([ev(6, "a", "WAITING")], latest=6))
    assert list(state.attention) == ["a"]
    assert state.badge_text() == "🤖 1 等待中"


def test_truncated_rebuilds_from_status():
    state, _ = make()
    state.apply_events(payload([ev(6, "old", "WAITING")]))
    assert state.apply_events(payload(latest=99, truncated=True)) == RESYNC
    state.apply_status(status(("x", "WAITING"), ("y", "STUCK"), ("z", "IDLE")))
    assert sorted(state.attention) == ["x", "y"]
    assert state.badge_text() == "🤖 1 等待中 · 1 可能卡住"


def test_boot_id_change_resyncs_and_resets_cursor():
    state, _ = make()
    state.apply_events(payload([ev(6, "old", "WAITING")]))
    assert state.apply_events(payload(latest=2, boot="f" * 32)) == RESYNC
    assert state.boot_id == "f" * 32 and state.seq == 2
    state.apply_status(status())
    assert state.attention == {}


def test_to_null_removes_session():
    state, _ = make()
    state.apply_events(payload([ev(6, "a", "WAITING")]))
    state.apply_events(payload([ev(7, "a", None, "WAITING")], latest=7))
    assert state.attention == {}
    assert state.badge_text() == "🤖 0"


def test_reconciliation_corrects_drift():
    state, _ = make()
    state.apply_events(payload([ev(6, "ghost", "WAITING")]))
    state.apply_status(status(("real", "STUCK")))
    assert list(state.attention) == ["real"]


def test_badge_levels_and_texts():
    state = WatchdogState(clock=Clock())
    assert state.badge_text() == "🤖 未啟動" and state.badge_level() == "off"
    state.apply_events(payload(latest=1))
    assert state.badge_text() == "🤖 0" and state.badge_level() == "idle"
    state.apply_status(status(("a", "WAITING"), ("b", "WAITING")))
    assert state.badge_text() == "🤖 2 等待中"
    assert "等待中：a · Claude · local" in state.tooltip()


def test_debounce_25s_then_toast():
    state, clock = make()
    state.apply_events(payload([ev(6, "a", "WAITING")]))
    clock.advance(24)
    assert state.due_toasts() == []
    clock.advance(2)
    (toast,) = state.due_toasts()
    assert toast.kind == "WAITING" and "在等你回覆" in toast.message


def test_new_event_during_debounce_cancels():
    state, clock = make()
    state.apply_events(payload([ev(6, "a", "WAITING")]))
    clock.advance(10)
    state.apply_events(payload([ev(7, "a", "WORKING", "WAITING")], latest=7))
    clock.advance(60)
    assert state.due_toasts() == []


def test_new_waiting_event_restarts_the_window():
    state, clock = make()
    state.apply_events(payload([ev(6, "a", "WAITING")]))
    clock.advance(20)
    state.apply_events(payload([ev(7, "a", "WAITING", "WAITING")], latest=7))
    clock.advance(20)
    assert state.due_toasts() == []
    clock.advance(6)
    assert len(state.due_toasts()) == 1


def test_multiple_ids_merge_into_one_toast():
    state, clock = make()
    state.apply_events(payload([ev(6, "d:/a", "WAITING", project="A"),
                                ev(7, "d:/b", "WAITING", project="B")]))
    clock.advance(26)
    (toast,) = state.due_toasts()
    assert toast.message.startswith("2 個 agent 在等你：")
    assert "A（Claude）" in toast.message and "B（Claude）" in toast.message


def test_cooldown_5_minutes_survives_settings_roundtrip():
    state, clock = make()
    state.apply_events(payload([ev(6, "a", "WAITING")]))
    clock.advance(26)
    assert len(state.due_toasts()) == 1
    raw = dump_last_toast(state.last_toast)

    clock.advance(30)
    restarted = WatchdogState(clock=clock, last_toast=load_last_toast(raw))
    restarted.apply_events(payload(latest=5))
    restarted.apply_events(payload([ev(8, "a", "WAITING")], latest=8))
    clock.advance(26)
    assert restarted.due_toasts() == []  # still inside the 300 s cooldown

    clock.advance(300)
    restarted.apply_events(payload([ev(9, "a", "WAITING")], latest=9))
    clock.advance(26)
    assert len(restarted.due_toasts()) == 1


def test_from_null_never_toasts_but_counts_in_badge():
    state, clock = make()
    state.apply_events(payload([ev(6, "a", "WAITING", frm=None)]))
    clock.advance(120)
    assert state.due_toasts() == []
    assert state.badge_text() == "🤖 1 等待中"


def test_waiting_toast_off_still_toasts_stuck():
    state, clock = make(toast_waiting=False)
    state.apply_events(payload([ev(6, "a", "WAITING"), ev(7, "b", "STUCK")]))
    clock.advance(26)
    (toast,) = state.due_toasts()
    assert toast.kind == "STUCK" and "可能卡住" in toast.message
    assert "卡住了" not in toast.message


def test_stuck_toast_off():
    state, clock = make(toast_stuck=False)
    state.apply_events(payload([ev(6, "b", "STUCK")]))
    clock.advance(26)
    assert state.due_toasts() == []


def test_legacy_watchdog_badge_only():
    state = WatchdogState(clock=Clock())
    state.set_legacy(True)
    state.apply_status(status(("a", "WAITING")))
    assert state.badge_text() == "🤖 1 等待中"
    state.apply_events(payload([ev(6, "a", "WAITING")]))
    assert state.due_toasts() == []


def test_unavailable_keeps_pending_and_toasts_after_recovery():
    state, clock = make()
    state.apply_events(payload([ev(6, "a", "WAITING")], latest=6))
    clock.advance(20)
    state.set_unavailable(True)
    clock.advance(40)
    assert state.due_toasts() == []          # held back while down
    state.apply_events(payload(latest=6))    # recovered, same boot
    (toast,) = state.due_toasts()            # still WAITING: toast as usual
    assert toast.kind == "WAITING"


def test_recovery_drops_pending_when_session_no_longer_needs_you():
    state, clock = make()
    state.apply_events(payload([ev(6, "a", "WAITING")], latest=6))
    state.set_unavailable(True)
    clock.advance(60)
    state.apply_status(status())             # reconciliation: nothing waiting
    assert state.due_toasts() == []


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(schema=2),
    lambda p: p.pop("schema"),
    lambda p: p.update(schema=True),
    lambda p: p.update(truncated="false"),
    lambda p: p.update(truncated=0),
    lambda p: p.update(latest_seq=True),
    lambda p: p.update(latest_seq="7"),
    lambda p: p.update(latest_seq=-1),
    lambda p: p.update(boot_id=None),
    lambda p: p.update(events="nope"),
    lambda p: p.update(events=[ev(6, "a", "WAITING"), ev("7", "b", "STUCK")]),
    lambda p: p.update(events=[ev(6, "a", "WAITING"), ev(None, "b", "STUCK")]),
    lambda p: p.update(events=[ev(6, "a", "WAITING"), "junk"]),
])
def test_malformed_events_payload_is_invalid_and_changes_nothing(mutate):
    state, clock = make()
    bad = payload([ev(6, "a", "WAITING")], latest=6)
    mutate(bad)
    assert state.apply_events(bad) == INVALID
    assert (state.boot_id, state.seq) == (BOOT, 5)
    assert state.attention == {} and state.next_due_in() is None


def test_bool_seq_on_a_baseline_is_invalid():
    state = WatchdogState(clock=Clock())
    assert state.apply_events(payload(latest=True)) == INVALID
    assert state.boot_id is None


def test_seq_only_moves_forward_within_a_boot():
    state, clock = make()
    state.apply_events(payload([ev(6, "a", "WAITING")], latest=6))
    state.apply_events(payload([ev(7, "b", "STUCK")], latest=7))
    assert state.seq == 7
    clock.advance(10)
    state.apply_events(payload([ev(6, "a", "WAITING")], latest=6))  # late reply
    assert state.seq == 7
    assert state.attention["b"]["state"] == "STUCK"


def test_clear_pending_drops_debounced_toasts():
    state, clock = make()
    state.apply_events(payload([ev(6, "a", "WAITING")]))
    state.clear_pending()
    clock.advance(60)
    assert state.due_toasts() == []


def test_waiting_and_stuck_due_together_make_one_toast():
    state, clock = make()
    state.apply_events(payload([ev(6, "a", "WAITING", project="A"),
                                ev(7, "b", "WAITING", project="B"),
                                ev(8, "c", "STUCK", project="C")]))
    clock.advance(26)
    (toast,) = state.due_toasts()
    assert toast.kind == MIXED
    assert "2 個在等你、1 個可能卡住" in toast.message
    assert set(toast.ids) == {"a", "b", "c"}
    assert state.last_toast_dirty and set(state.last_toast) == {"a", "b", "c"}


def test_toast_due_within_merge_window_rides_along():
    state, clock = make()
    state.apply_events(payload([ev(6, "a", "WAITING", project="A")], latest=6))
    clock.advance(3)
    state.apply_events(payload([ev(7, "b", "WAITING", project="B")], latest=7))
    clock.advance(23)  # a is due, b is due in 3 s (inside the window)
    (toast,) = state.due_toasts()
    assert set(toast.ids) == {"a", "b"}


def test_toast_far_from_the_first_is_not_pulled_in():
    state, clock = make()
    state.apply_events(payload([ev(6, "a", "WAITING")], latest=6))
    clock.advance(10)
    state.apply_events(payload([ev(7, "b", "WAITING")], latest=7))
    clock.advance(16)  # a due; b due in 9 s
    (toast,) = state.due_toasts()
    assert toast.ids == ("a",)
    clock.advance(10)
    (toast,) = state.due_toasts()
    assert toast.ids == ("b",)


def test_backoff_sequence_and_reset():
    b = Backoff()
    assert [b.next_delay() for _ in range(6)] == [5, 10, 30, 60, 60, 60]
    b.reset()
    assert b.next_delay() == 5


@pytest.mark.parametrize("url,ok", [
    ("http://127.0.0.1:8787", True),
    ("http://[::1]:8787", True),
    ("http://127.5.5.5:1", True),
    ("http://localhost:8787", False),
    ("http://192.168.1.2:8787", False),
    ("http://example.com", False),
    ("file:///etc/passwd", False),
    ("http://127.0.0.1.evil.com", False),
    ("http://127.0.0.1@evil.com", False),
    ("", False),
])
def test_loopback_only(url, ok):
    assert is_loopback_url(url) is ok


@pytest.mark.parametrize("raw", [None, "", "not json", "[1]", 5])
def test_load_last_toast_tolerates_garbage(raw):
    assert load_last_toast(raw) == {}
