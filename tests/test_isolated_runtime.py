"""Exercise diagnostic isolation in fresh processes without patching pytest Qt."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def run_probe(code, directory):
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", code, str(directory)],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_all_settings_overloads_and_preimported_aliases_are_contained(tmp_path):
    output = run_probe(r'''
import sys
from pathlib import Path
from PySide6 import QtCore
from app import settings_store
from tools.isolated_runtime import install

base = Path(sys.argv[1])
outside = base / "must-stay-unchanged.ini"
outside.write_text("[General]\nexisting=preserved\n", encoding="utf-8")
before = outside.read_bytes()
native = QtCore.QSettings
runtime = install(base / "owned")
S = QtCore.QSettings
assert settings_store.QSettings is S and S is not native
parent = QtCore.QObject()
constructors = [
    lambda: S(), lambda: S(parent), lambda: S(parent=parent),
    lambda: S(S.Scope.UserScope),
    lambda: S(S.Scope.SystemScope, parent),
    lambda: S(S.Scope.UserScope, parent=parent),
    lambda: S("organization"), lambda: S("organization", "application"),
    lambda: S("organization", "application", parent),
    lambda: S("organization", application="application", parent=parent),
    lambda: S(S.Scope.UserScope, "organization", "application"),
    lambda: S(S.Scope.SystemScope, "organization", application="application", parent=parent),
    lambda: S(S.Format.NativeFormat, S.Scope.UserScope, "organization", "application", parent),
    lambda: S(S.Format.IniFormat, S.Scope.SystemScope, "organization", "application"),
    lambda: S(str(outside), S.Format.IniFormat, parent),
    lambda: S(str(outside), S.Format.NativeFormat, parent=parent),
    lambda: S(str(runtime.root / "explicit.ini"), S.Format.IniFormat),
    lambda: S("properties", "application", objectName="settings-probe", fallbacksEnabled=True),
]
for number, construct in enumerate(constructors):
    instance = construct()
    runtime.assert_settings(instance)
    assert not instance.fallbacksEnabled()
    assert not instance.contains("existing")
    instance.setValue("probe", number)
    instance.sync()
    assert instance.status() == S.Status.NoError
    assert Path(instance.fileName()).exists()
assert instance.objectName() == "settings-probe"
assert outside.read_bytes() == before
one = S("same-org", "same-app", parent=parent)
assert one.parent() is parent
one.setValue("same", "isolated")
one.sync()
assert S(S.Scope.UserScope, "same-org", "same-app").value("same") == "isolated"
assert not S(S.Scope.SystemScope, "same-org", "same-app").contains("same")
assert not S("other-org", "same-app").contains("same")
assert not S("same-org", "other-app").contains("same")
assert S(S.Scope.SystemScope, "same-org", "same-app").scope() == S.Scope.SystemScope
assert one.organizationName() == "same-org" and one.applicationName() == "same-app"
runtime.assert_settings(settings_store.settings())
print("18 constructors contained; outside INI unchanged; aliases and identities preserved")
''', tmp_path)
    assert "18 constructors contained" in output


def test_default_stores_and_reinstallation_remain_inside_owned_root(tmp_path):
    output = run_probe(r'''
import sys
from pathlib import Path
from tools.isolated_runtime import install
from app import settings_store

base = Path(sys.argv[1])
first = install(base / "first")
first.settings_type("org", "app").setValue("first-only", True)
runtime = install(base / "second")
assert settings_store.QSettings is runtime.settings_type
assert not runtime.settings_type("org", "app").contains("first-only")
from app import document_libraries, recovery, tag_colors, tag_index, relocation_journal
paths = [document_libraries._default_store_path(), recovery._default_recovery_dir(),
         tag_colors._default_colors_path(), tag_index._default_index_path(),
         relocation_journal.default_journal_dir()]
assert all(path.resolve().is_relative_to(runtime.root) for path in paths)
assert document_libraries.DocumentLibraryStore().load() == []
assert tag_colors.TagColorStore.load().known_tags() == []
assert tag_index.TagIndex()._data == {}
assert recovery.RecoveryStore().directory == runtime.root / "recovery"
print("Five stores contained; repeated install rebinds existing application aliases")
''', tmp_path)
    assert "Five stores contained" in output


def test_baseline_missing_module_is_optional_but_missing_dependency_is_not(tmp_path):
    run_probe(r'''
import sys
from pathlib import Path
from tools import isolated_runtime

base = Path(sys.argv[1])
original = isolated_runtime.importlib.import_module
def missing_module(name):
    if name == "app.relocation_journal":
        raise ModuleNotFoundError(name=name)
    return original(name)
isolated_runtime.importlib.import_module = missing_module
isolated_runtime.install(base / "baseline")
def missing_dependency(name):
    if name == "app.relocation_journal":
        raise ModuleNotFoundError(name="missing_dependency")
    return original(name)
isolated_runtime.importlib.import_module = missing_dependency
try:
    isolated_runtime.install(base / "invalid")
except ModuleNotFoundError as error:
    assert error.name == "missing_dependency"
else:
    raise AssertionError("Missing application dependency was silently ignored")
''', tmp_path)


@pytest.mark.parametrize("tool", [
    "benchmark_file_switching", "preview_reading_workspace", "benchmark_library_search",
    "benchmark_editor_writing", "benchmark_text_loading", "preview_pdf_reading_tools",
    "verify_session_restart",
])
def test_tool_installs_isolation_before_importing_application_modules(tmp_path, tool):
    # Stop at the isolation boundary: no GUI benchmark or user settings access.
    output = run_probe(r'''
import importlib
import sys
from pathlib import Path
from tools import isolated_runtime

root = Path(sys.argv[1])
def installed(directory, **kwargs):
    assert not any(name == "app" or name.startswith("app.") for name in sys.modules)
    print("Isolation boundary reached before application imports")
    raise SystemExit(0)
isolated_runtime.install = installed
tool_name = TOOL_NAME
tool = importlib.import_module("tools." + tool_name)
sys.argv = [tool_name, "--output", str(root / "output")]
if tool_name == "verify_session_restart":
    tool.child(root, "save", root)
else:
    tool.main()
raise AssertionError("Tool returned without installing isolation")
'''.replace("TOOL_NAME", repr(tool)), tmp_path)
    assert "Isolation boundary reached" in output
