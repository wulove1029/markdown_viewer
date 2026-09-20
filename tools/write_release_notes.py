"""Write only the current version's release notes for GitHub publishing."""

import argparse
import runpy
from pathlib import Path


def render_release_notes(version: str, notes: list[str]) -> str:
    if not notes or any(not isinstance(note, str) or not note.strip() for note in notes):
        raise ValueError("Release notes must contain non-empty strings")
    return f"## Markdown Viewer {version}\n\n" + "\n".join(f"- {note}" for note in notes) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    metadata = runpy.run_path(str(Path(__file__).resolve().parents[1] / "app/version.py"))
    if args.version.removeprefix("v") != metadata["VERSION"]:
        parser.error("Requested version does not match app/version.py")
    args.output.write_text(
        render_release_notes(metadata["VERSION"], metadata["RELEASE_NOTES"]), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
