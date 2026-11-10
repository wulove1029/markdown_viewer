"""Conservative filters for optional packaged resources."""


def filter_webengine_locales(entries):
    """Keep Traditional/Simplified Chinese and the English fallback only."""
    keep = {"zh-TW.pak", "zh-CN.pak", "en-US.pak"}
    result = []
    for entry in entries:
        destination = entry[0].replace("\\", "/")
        if ("/qtwebengine_locales/" in destination
                and destination.endswith(".pak")
                and destination.rsplit("/", 1)[-1] not in keep):
            continue
        result.append(entry)
    return result


# --- optional bundled Agent Watchdog ---------------------------------------
#
# The directory is optional.  When it is absent every function below returns
# "nothing", so the build is exactly what it was without this feature.

AGENT_BUNDLE_DIR = "agent_watchdog"
AGENT_BUNDLE_SCRIPT = "aiwatch.py"
#: Build-time override: a directory outside the repository that holds aiwatch.py.
AGENT_BUNDLE_ENV = "MDV_AGENT_WATCHDOG_DIR"
#: Allowlist. A working copy accumulates private per-machine state (endpoint,
#: registration, host lists, logs, virtual environments ...), so nothing is
#: shipped unless it is named here: the program files at the top level and
#: whatever lies under the static folder.
_AGENT_TOP_SUFFIXES = {".py", ".html"}
_AGENT_STATIC_DIR = "static"
#: Refused even inside the allowlist (compared case-insensitively), together
#: with every file or folder whose name starts with a dot.
_AGENT_SKIP_DIRS = {"__pycache__", "tests"}


def _agent_hidden(name):
    return name.startswith(".") or name.lower() in _AGENT_SKIP_DIRS


def agent_bundle_source(root, environ=None):
    """Directory holding the Watchdog to bundle, or None when there is none."""
    import os
    from pathlib import Path

    env = os.environ if environ is None else environ
    override = str(env.get(AGENT_BUNDLE_ENV, "")).strip()
    source = Path(override) if override else Path(root) / AGENT_BUNDLE_DIR
    return source if (source / AGENT_BUNDLE_SCRIPT).is_file() else None


def _agent_bundle_files(source):
    import os
    from pathlib import Path

    source = Path(source)
    for entry in sorted(source.iterdir(), key=lambda p: p.name):
        if (entry.is_file() and not _agent_hidden(entry.name)
                and entry.suffix.lower() in _AGENT_TOP_SUFFIXES):
            yield entry
    static = source / _AGENT_STATIC_DIR
    if not static.is_dir():
        return
    for folder, dirs, files in os.walk(static):
        dirs[:] = sorted(d for d in dirs if not _agent_hidden(d))
        for name in sorted(files):
            if not _agent_hidden(name):
                yield Path(folder) / name


def agent_bundle_datas(source):
    """PyInstaller ``datas`` entries that copy *source* to AGENT_BUNDLE_DIR."""
    from pathlib import Path

    if source is None:
        return []
    source = Path(source)
    entries = []
    for path in _agent_bundle_files(source):
        relative = path.relative_to(source).parent
        target = (Path(AGENT_BUNDLE_DIR) / relative).as_posix()
        entries.append((str(path), target))
    return entries


def stdlib_imports(source, excludes=()):
    """Standard-library modules imported by the .py files shipped from *source*.

    The bundled scripts are shipped as data and run by the application's own
    interpreter, so PyInstaller never sees their imports: a module the
    application itself does not use (http.server, sqlite3, winreg ...) would be
    missing at run time.  Third-party and local modules are left out; a file
    that does not parse is skipped.
    """
    import ast
    import importlib.util
    import sys
    from pathlib import Path

    if source is None:
        return []
    stdlib = sys.stdlib_module_names
    excluded = set(excludes)
    found = set()

    def add(name):
        top = name.split(".", 1)[0]
        if top in stdlib and top not in excluded and name not in excluded:
            found.add(name)

    def is_module(name):
        try:
            return importlib.util.find_spec(name) is not None
        except (ImportError, AttributeError, ValueError):
            return False

    for path in _agent_bundle_files(Path(source)):
        if path.suffix.lower() != ".py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        except (OSError, SyntaxError, ValueError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    add(alias.name)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                add(node.module)
                if node.module.split(".", 1)[0] not in stdlib:
                    continue
                for alias in node.names:
                    # "from ctypes import wintypes" names a submodule.
                    candidate = f"{node.module}.{alias.name}"
                    if alias.name != "*" and is_module(candidate):
                        add(candidate)
    return sorted(found)
