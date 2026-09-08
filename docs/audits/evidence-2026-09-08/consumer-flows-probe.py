"""Reproduce consumer-flow audit observations with temporary documents only."""

import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app import global_search
from app.global_search import FileSearchResult, GlobalSearchView, SearchHit
from app.quick_open import QuickOpenDialog
from app.theme import LIGHT
from app.window import MainWindow


def main():
    app = QApplication.instance() or QApplication([])
    global_search.load_excluded_folders = lambda: []
    observations = {}
    with TemporaryDirectory(prefix="markdown-viewer-audit-") as folder:
        root = Path(folder)
        for suffix in [".md", ".markdown", ".txt"]:
            (root / ("document" + suffix)).write_text(
                "consumer audit needle", encoding="utf-8"
            )
        (root / "nested").mkdir()
        (root / "nested" / "nested.md").write_text(
            "consumer audit needle", encoding="utf-8"
        )

        results = global_search.search_markdown_files([root], "consumer audit needle")
        observations["global_search_found"] = sorted(
            result.path.relative_to(root).as_posix() for result in results
        )
        window_stub = SimpleNamespace(
            _panel=SimpleNamespace(recent=SimpleNamespace(paths=lambda: [])),
            _current_file=root / "document.md",
        )
        observations["quick_open_candidates"] = sorted(
            Path(path).relative_to(root).as_posix()
            for _, path in MainWindow._quick_open_candidates(window_stub)
        )

        selected = []
        view = GlobalSearchView(
            roots_provider=lambda: [root],
            on_result_selected=lambda *args: selected.append(args),
        )
        result = FileSearchResult(
            root / "document.md",
            (SearchHit(1, "consumer audit needle", ((0, 8),)),),
        )
        view._render_results("consumer", [result])
        view.show()
        app.processEvents()
        view._list.setCurrentRow(1)
        view._list.setFocus()
        QTest.keyClick(view._list, Qt.Key.Key_Return)
        app.processEvents()
        observations["global_search_opened_after_return"] = len(selected)
        rect = view._list.visualItemRect(view._list.item(1))
        QTest.mouseClick(
            view._list.viewport(), Qt.MouseButton.LeftButton, pos=rect.center()
        )
        app.processEvents()
        observations["global_search_opened_after_mouse_click"] = len(selected)
        view.close()

        quick = QuickOpenDialog(
            [
                ("Meeting.md", str(root / "a" / "Meeting.md")),
                ("Meeting.md", str(root / "b" / "Meeting.md")),
            ],
            LIGHT,
        )
        observations["quick_open_duplicate_visible_rows"] = [
            quick._list.item(index).text() for index in range(quick._list.count())
        ]
        quick.close()

    evidence = {
        "environment": "Qt offscreen; isolated temporary documents; no MainWindow instance",
        "observations": observations,
    }
    output = json.dumps(evidence, ensure_ascii=False, indent=2) + "\n"
    Path(__file__).with_suffix(".json").write_text(output, encoding="utf-8")
    print(output, end="")


if __name__ == "__main__":
    main()
