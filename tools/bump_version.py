from __future__ import annotations

from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]


def replace(pattern: str, repl: str, path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if not re.search(pattern, text):
        raise SystemExit(f"No version field matched in {path}")
    updated = re.sub(pattern, repl, text)
    path.write_text(updated, encoding="utf-8")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: py -3 tools/bump_version.py 1.2.3")

    version = sys.argv[1].strip().lstrip("v")
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise SystemExit("Version must use semantic format like 1.2.3")

    replace(r'VERSION = "[^"]+"', f'VERSION = "{version}"', ROOT / "app" / "version.py")
    replace(r'#define MyAppVersion "[^"]+"', f'#define MyAppVersion "{version}"', ROOT / "installer.iss")
    print(f"Updated version to {version}")


if __name__ == "__main__":
    main()
