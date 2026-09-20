from PySide6.QtCore import QSettings, QStandardPaths

from app.settings_store import APP, DISPLAY_NAME, ORG, legacy_data_path, settings


def test_identity_and_existing_data_paths_are_preserved(qapp):
    old_name, old_org = qapp.applicationName(), qapp.organizationName()
    try:
        qapp.setOrganizationName(ORG)
        qapp.setApplicationName(DISPLAY_NAME)
        previous = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
        qapp.setApplicationName(APP)
        current = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
        assert str(legacy_data_path(current)).replace("\\", "/") == previous
        assert settings().fileName() == QSettings().fileName()
        assert ORG == "markdown-viewer"
        assert APP == "MarkdownViewer"
    finally:
        qapp.setApplicationName(old_name)
        qapp.setOrganizationName(old_org)


def test_settings_factory_reopens_saved_preferences(tmp_path, monkeypatch):
    import app.settings_store as store
    path = str(tmp_path / "prefs.ini")
    monkeypatch.setattr(store, "QSettings", lambda *_: QSettings(path, QSettings.Format.IniFormat))
    first = store.settings()
    first.setValue("graph/group_mode", "folder")
    first.sync()
    second = store.settings()
    assert second.value("graph/group_mode") == "folder"


def test_pdf_page_setup_uses_central_settings_identity(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QDialog

    from app import export_actions
    captured = []
    def fake_settings(org, app):
        captured.append((org, app))
        return QSettings(str(tmp_path / "pdf.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(export_actions, "QSettings", fake_settings)
    monkeypatch.setattr(QDialog, "exec", lambda _: QDialog.DialogCode.Rejected)
    assert export_actions.ask_page_setup(None) is None
    assert captured == [(ORG, APP)]
