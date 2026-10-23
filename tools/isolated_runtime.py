"""Process-local settings and data isolation for disposable diagnostic tools.

QSettings(org, app) ignores setDefaultFormat on Windows. Every constructor in
this subclass therefore uses an explicit INI filename beneath the owned root.
Install this before importing application modules or constructing any widgets.
"""

from __future__ import annotations

import hashlib
import importlib
import sys
from dataclasses import dataclass
from pathlib import Path

from PySide6 import QtCore

_NATIVE_SETTINGS = QtCore.QSettings
_UNSET = object()


def _component(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _arguments(args, kwargs):
    """Normalize all six documented PySide QSettings constructor overloads."""
    values = list(args)
    keywords = dict(kwargs)
    parent = keywords.pop("parent", _UNSET)
    application = keywords.pop("application", _UNSET)
    scope = _NATIVE_SETTINGS.Scope.UserScope
    organization = QtCore.QCoreApplication.organizationName() or "MDVDiagnostic"
    default_application = QtCore.QCoreApplication.applicationName() or "Diagnostic"
    filename = None
    named = False
    if values and isinstance(values[0], _NATIVE_SETTINGS.Format):
        values.pop(0)
        if not values or not isinstance(values[0], _NATIVE_SETTINGS.Scope):
            raise TypeError("QSettings format requires a scope and organization")
        scope = values.pop(0)
        if not values or not isinstance(values[0], str):
            raise TypeError("QSettings format requires an organization")
        organization, named = values.pop(0), True
    elif values and isinstance(values[0], _NATIVE_SETTINGS.Scope):
        scope = values.pop(0)
        if values and isinstance(values[0], str):
            organization, named = values.pop(0), True
    elif values and isinstance(values[0], str):
        first = values.pop(0)
        if values and isinstance(values[0], _NATIVE_SETTINGS.Format):
            values.pop(0)
            filename = first
        else:
            organization, named = first, True
    if named:
        if values and isinstance(values[0], str):
            if application is not _UNSET:
                raise TypeError("QSettings application was supplied twice")
            application = values.pop(0)
        elif application is _UNSET:
            application = ""
    elif application is not _UNSET:
        raise TypeError("QSettings application requires an organization")
    else:
        application = default_application if filename is None else ""
    if values:
        if parent is not _UNSET or len(values) != 1:
            raise TypeError("Unexpected positional QSettings arguments")
        parent = values.pop()
    if parent is _UNSET:
        parent = None
    if parent is not None and not isinstance(parent, QtCore.QObject):
        raise TypeError("QSettings parent must be QObject or None")
    if not isinstance(application, str):
        raise TypeError("QSettings application must be a string")
    # PySide also accepts QObject property keywords, such as objectName.
    # Let the native explicit-file constructor validate those properties.
    return scope, organization, application, parent, filename, keywords


@dataclass(frozen=True)
class IsolatedRuntime:
    root: Path
    settings_type: type

    def assert_settings(self, settings) -> Path:
        path = Path(settings.fileName()).resolve()
        if (settings.format() != _NATIVE_SETTINGS.Format.IniFormat
                or not path.is_relative_to(self.root)):
            raise RuntimeError("Diagnostic settings escaped the isolated directory")
        return path


def install(root: str | Path, *, application: str = "MDVDiagnostic") -> IsolatedRuntime:
    """Redirect every diagnostic settings/store access before loading the app."""
    directory = Path(root).resolve()
    directory.mkdir(parents=True, exist_ok=True)

    class IsolatedSettings(_NATIVE_SETTINGS):
        def __init__(self, *args, **kwargs):
            scope, organization, app_name, parent, filename, properties = _arguments(args, kwargs)
            if filename is not None:
                requested = Path(filename).resolve()
                path = (requested if requested.is_relative_to(directory) else
                        directory / "settings" / "files" / (_component(str(requested)) + ".ini"))
            else:
                scope_name = "system" if scope == self.Scope.SystemScope else "user"
                path = (directory / "settings" / scope_name / _component(organization)
                        / (_component(app_name) + ".ini"))
            path.parent.mkdir(parents=True, exist_ok=True)
            super().__init__(str(path), self.Format.IniFormat, parent, **properties)
            self._diagnostic_scope = scope
            self._diagnostic_organization = organization if filename is None else ""
            self._diagnostic_application = app_name
            self.setFallbacksEnabled(False)
            runtime.assert_settings(self)

        def scope(self):
            return self._diagnostic_scope

        def organizationName(self):
            return self._diagnostic_organization

        def applicationName(self):
            return self._diagnostic_application

    runtime = IsolatedRuntime(directory, IsolatedSettings)
    previous = QtCore.QSettings
    setattr(QtCore, "QSettings", IsolatedSettings)
    # A frozen entry point may already have imported settings_store. Rebind
    # aliases as well; changing QtCore alone would leave a native escape hatch.
    for name, module in tuple(sys.modules.items()):
        if module is None or not (name == "app" or name.startswith("app.")):
            continue
        for key, value in tuple(vars(module).items()):
            if value is previous or value is _NATIVE_SETTINGS:
                setattr(module, key, IsolatedSettings)
    QtCore.QStandardPaths.setTestModeEnabled(True)
    QtCore.QCoreApplication.setOrganizationName("MDVDiagnostic")
    QtCore.QCoreApplication.setApplicationName(application)
    stores = {
        "document_libraries": ("_default_store_path", directory / "libraries.json"),
        "recovery": ("_default_recovery_dir", directory / "recovery"),
        "tag_colors": ("_default_colors_path", directory / "colors.json"),
        "tag_index": ("_default_index_path", directory / "tags.json"),
        "relocation_journal": ("default_journal_dir", directory / "relocations"),
    }
    for module_name, (attribute, path) in stores.items():
        qualified = "app." + module_name
        try:
            module = importlib.import_module(qualified)
        except ModuleNotFoundError as error:
            # Historical source snapshots do not contain every current store.
            # A missing dependency inside an existing module remains an error.
            if error.name == qualified:
                continue
            raise
        setattr(module, attribute, lambda path=path: path)
    return runtime


def configure_webengine(runtime: IsolatedRuntime) -> None:
    """Call after QApplication exists, before constructing any WebEngine page."""
    from PySide6.QtWebEngineCore import QWebEngineProfile

    profile = QWebEngineProfile.defaultProfile()
    profile.setPersistentStoragePath(str(runtime.root / "web-storage"))
    profile.setCachePath(str(runtime.root / "web-cache"))
