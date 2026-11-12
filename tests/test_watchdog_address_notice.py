"""Notice bar for a custom Watchdog address that fell back to another base.

No network: the watcher state is set by hand and settings live in a temp ini.
"""

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QMainWindow

from app import agent_watchdog, settings_store
from app.agent_watchdog import AgentWatcher, connection_text, host_port
from app.agents_panel import AgentsDock
from app.theme import DARK, LIGHT

CUSTOM = "http://127.0.0.1:6868"
DEFAULT = "http://127.0.0.1:8787"


@pytest.fixture
def make(qapp, tmp_path, monkeypatch):
    ini = tmp_path / "settings.ini"
    monkeypatch.setattr(
        settings_store, "settings",
        lambda: QSettings(str(ini), QSettings.Format.IniFormat))
    created = []

    def build(configured, bases, index=0, available=True):
        store = settings_store.settings()
        store.setValue("watchdog/base_url", configured)
        store.sync()
        w = AgentWatcher(endpoint_path=tmp_path / "endpoint.json", use_tray=False)
        w.config = agent_watchdog.WatchdogConfig.load()
        w._bases = list(bases)
        w._base_index = index
        w.state.set_unavailable(not available)
        created.append(w)
        return w

    yield build
    for w in created:
        w.shutdown()


def _dock(w, theme=LIGHT):
    host = QMainWindow()
    dock = AgentsDock(host, theme)
    dock.set_watcher(w)
    return host, dock


def _shown(dock):
    return not dock._notice.isHidden()


def test_hidden_for_default_url(make):
    w = make(DEFAULT, [DEFAULT])
    host, dock = _dock(w)
    assert not _shown(dock)


def test_hidden_when_connected_to_configured_custom(make):
    w = make(CUSTOM, [CUSTOM, DEFAULT], index=0)
    host, dock = _dock(w)
    assert w.fallback_from is None and not _shown(dock)


def test_shown_with_text_on_fallback_and_live_updates(make):
    w = make(CUSTOM, [CUSTOM, DEFAULT], index=0)
    host, dock = _dock(w)
    w._switch_base(1)                      # emits changed
    assert _shown(dock)
    assert dock._notice_label.text() == (
        "你設定的 127.0.0.1:6868 沒有回應，目前顯示的是 127.0.0.1:8787 的 Watchdog。")
    w._switch_base(0)                      # configured one answers again
    assert not _shown(dock)


def test_hides_when_setting_returns_to_default(make):
    w = make(CUSTOM, [CUSTOM, DEFAULT], index=1)
    host, dock = _dock(w)
    assert _shown(dock)
    settings_store.settings().setValue("watchdog/base_url", DEFAULT)
    w.start()                              # re-reads the setting, emits changed
    w._stop_activity()
    assert not _shown(dock)


def test_hidden_when_unavailable(make):
    w = make(CUSTOM, [CUSTOM, DEFAULT], index=1, available=False)
    host, dock = _dock(w)
    assert not _shown(dock)
    w.state.set_unavailable(False)
    w.changed.emit()
    assert _shown(dock)
    w.state.set_unavailable(True)
    w.changed.emit()
    assert not _shown(dock)


def test_connection_text_for_dialog_label(make):
    w = make(CUSTOM, [CUSTOM, DEFAULT], index=1)
    assert connection_text(w) == "目前連線：127.0.0.1:8787"
    w.state.set_unavailable(True)
    assert connection_text(w) == "目前沒有連上 Watchdog"
    assert connection_text(None) == "目前沒有連上 Watchdog"


def test_host_port():
    assert host_port("http://127.0.0.1:8787/") == "127.0.0.1:8787"
    assert host_port("http://[::1]:9") == "[::1]:9"


def test_theme_change_restyles_notice(make):
    w = make(CUSTOM, [CUSTOM, DEFAULT], index=1)
    host, dock = _dock(w, LIGHT)
    assert LIGHT.accent_soft in dock._notice.styleSheet()
    dock.apply_theme(DARK)
    sheet = dock._notice.styleSheet()
    assert DARK.accent_soft in sheet and DARK.text in sheet
    assert LIGHT.accent_soft not in sheet
