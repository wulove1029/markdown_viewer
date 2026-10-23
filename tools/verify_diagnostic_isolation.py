"""Read-only parent guard for diagnostic subprocesses; never print setting values."""
import argparse
import hashlib
import json
import os
import subprocess
import winreg
from pathlib import Path


def native_digest():
    rows = []
    for hive_name, hive in (("user", winreg.HKEY_CURRENT_USER),
                            ("system", winreg.HKEY_LOCAL_MACHINE)):
        for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            def visit(path):
                try:
                    handle = winreg.OpenKey(hive, path, 0, winreg.KEY_READ | view)
                except FileNotFoundError:
                    return
                with handle:
                    subkeys, values, _modified = winreg.QueryInfoKey(handle)
                    rows.append((hive_name, view, path, "key"))
                    for index in range(values):
                        name, value, kind = winreg.EnumValue(handle, index)
                        if isinstance(value, bytes):
                            value = value.hex()
                        rows.append((hive_name, view, path, name, kind, value))
                    children = [winreg.EnumKey(handle, i) for i in range(subkeys)]
                for child in children:
                    visit(path + "\\" + child)
            visit("Software\\markdown-viewer")
    payload = json.dumps(sorted(rows, key=repr), ensure_ascii=True).encode()
    return hashlib.sha256(payload).hexdigest()


def files_digest():
    rows = []
    roaming = Path(os.environ["APPDATA"])
    # Cover both the stable legacy application path and the canonical Qt name.
    for root in (roaming / "markdown-viewer" / "Markdown Viewer",
                 roaming / "markdown-viewer" / "MarkdownViewer",
                 roaming / "Markdown Viewer"):
        if root.exists():
            for path in sorted(root.rglob("*")):
                if path.is_file() and (path.suffix == ".json" or "recovery" in path.parts):
                    rows.append((str(path), hashlib.sha256(path.read_bytes()).hexdigest()))
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("A diagnostic command is required")
    before, data_before = native_digest(), files_digest()
    completed = subprocess.run(command, check=False)
    after, data_after = native_digest(), files_digest()
    report = {"exit_code": completed.returncode,
                      "native_digest_before": before, "native_digest_after": after,
                      "native_unchanged": before == after,
                      "persistent_data_unchanged": data_before == data_after}
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report), flush=True)
    raise SystemExit(0 if completed.returncode == 0 and before == after
                     and data_before == data_after else 1)


if __name__ == "__main__":
    main()
