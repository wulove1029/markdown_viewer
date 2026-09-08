"""Fresh verifier probes for relocation parser and transaction failure edges."""

import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from markdown_it import MarkdownIt
from app import file_ops
from app.document_relocation import rebase_markdown_links


def main():
    results = {}
    with tempfile.TemporaryDirectory(prefix="mdv-relocation-review-") as temporary:
        root = Path(temporary)
        old = root / "note.md"
        archive = root / "archive"
        archive.mkdir()
        new = archive / old.name
        text = "Unmatched backtick `\n\n[real](assets/a.png)\n\nAnother `\n"
        converted = rebase_markdown_links(text, old, new)
        rendered = MarkdownIt("commonmark").render(text)
        results["code_span_across_paragraphs"] = {
            "markdown_renders_local_link": '<a href="assets/a.png">' in rendered,
            "rebased_link": "[real](../assets/a.png)" in converted,
            "result": converted,
        }

        old.write_bytes(b"original source")
        signature = file_ops._signature

        def fail_after_publish(path):
            if path == new and path.exists():
                raise PermissionError("injected stat failure after publication")
            return signature(path)

        with patch.object(file_ops, "_signature", fail_after_publish):
            try:
                file_ops.rename_document(old, new)
            except OSError as exc:
                message = str(exc)
            else:
                message = "unexpected success"
        results["stat_failure_after_publish"] = {
            "source_exists": old.exists(),
            "source_bytes": old.read_bytes().decode() if old.exists() else None,
            "destination_exists": new.exists(),
            "destination_bytes": new.read_bytes().decode() if new.exists() else None,
            "error": message,
            "error_names_destination": str(new) in message,
        }
    output = Path(__file__).with_suffix(".json")
    output.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
