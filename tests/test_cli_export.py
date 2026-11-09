"""Headless CLI export (F1b): runs main.py in a real subprocess.

The child process drops QT_QPA_PLATFORM (conftest sets it for the in-process Qt
tests) so most exports use the real platform plugin; the offscreen tests put it
back on purpose because callers run the CLI that way too. Nothing here shows a
window; a GUI launch would block until the subprocess timeout.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest

from app.version import VERSION

ROOT = Path(__file__).resolve().parent.parent
MAIN = ROOT / "main.py"

MARKDOWN = """# CLI Title

Hello **world** with inline math $E=mc^2$.

| a | b |
|---|---|
| 1 | 2 |

```python
print("hi")
```

```mermaid
graph TD; A-->B;
```

$$\\int_0^1 x^2 dx$$

![red](img/red.png)
"""


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("QT_QPA_PLATFORM", None)
    env.pop("RUN_WEBENGINE_TESTS", None)
    return env


def _offscreen_env() -> dict[str, str]:
    env = _env()
    env["QT_QPA_PLATFORM"] = "offscreen"
    return env


def _run(args, timeout=240, env=None, **kwargs):
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(MAIN), *map(str, args)],
        cwd=ROOT, env=env if env is not None else _env(), capture_output=True,
        text=True, encoding="utf-8", timeout=timeout, **kwargs,
    )


def _read_json(path: Path) -> dict:
    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "result json must have no BOM"
    return json.loads(raw.decode("utf-8"))


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    folder = tmp_path_factory.mktemp("cli_export")
    (folder / "img").mkdir()
    from PySide6.QtGui import QColor, QImage

    image = QImage(40, 30, QImage.Format.Format_RGB32)
    image.fill(QColor("#cc3333"))
    assert image.save(str(folder / "img" / "red.png"))
    md = folder / "doc.md"
    md.write_text(MARKDOWN, encoding="utf-8")
    return md


def _assert_ok(result, rj: Path, fmt: str, md: Path, out: Path):
    assert result.returncode == 0, result.stderr + result.stdout
    assert out.is_file() and out.stat().st_size > 0
    data = _read_json(rj)
    assert data["schema"] == 1 and data["ok"] is True and data["exitCode"] == 0
    assert data["format"] == fmt
    assert Path(data["input"]) == md and Path(data["output"]) == out
    assert data["error"] is None and isinstance(data["warnings"], list)
    assert isinstance(data["elapsedMs"], int) and data["version"] == VERSION
    assert not list(out.parent.glob(f".{out.name}.*.tmp")), "temp file left behind"


@pytest.mark.parametrize("fmt", ["docx", "pptx", "pdf", "html"])
def test_export_each_format(sample, tmp_path, fmt):
    out = tmp_path / f"out.{fmt}"
    rj = tmp_path / "result.json"
    log = tmp_path / "run.log"
    result = _run(["--export", fmt, sample, out, "--result-json", rj, "--log", log])
    _assert_ok(result, rj, fmt, sample, out)
    assert not log.read_bytes().startswith(b"\xef\xbb\xbf")
    assert "export" in log.read_text(encoding="utf-8")
    if fmt in ("docx", "pptx"):
        with zipfile.ZipFile(out) as archive:
            names = archive.namelist()
        media = [n for n in names if "/media/" in n]
        # mermaid + display math + the relative-path image
        assert len(media) >= 3, names
        assert any(n.endswith(".png") for n in media)
    elif fmt == "pdf":
        assert out.read_bytes().startswith(b"%PDF")
    else:
        text = out.read_text(encoding="utf-8")
        assert "CLI Title" in text and "<base href=" in text


CJK_MARKDOWN = """# 部首測試

一、二、目

- 第一項
- 第二項
  - 巢狀項目

**粗體目錄**
"""


# The same CLI with the ToUnicode repair switched off: Chromium's raw output.
_RAW_PDF_EXPORT = (
    "import sys\n"
    "import app.renderer\n"
    "app.renderer.repair_radical_tounicode = lambda path: 0\n"
    "from app.cli_export import main\n"
    "raise SystemExit(main(sys.argv[1:]))\n"
)


def _tounicode_text(pdf: Path) -> str:
    """Text as extractors that ignore /ActualText (pypdf and others) see it."""
    import fitz

    flags = fitz.TEXTFLAGS_TEXT | fitz.TEXT_IGNORE_ACTUALTEXT
    with fitz.open(pdf) as document:
        return "".join(page.get_text(flags=flags) for page in document)


def _radicals(text: str) -> list[str]:
    return [c for c in text if 0x2E80 <= ord(c) <= 0x2FDF]


def test_pdf_text_layer_keeps_unified_ideographs(tmp_path):
    """Fonts like Microsoft JhengHei share one glyph between 一 (U+4E00) and
    the Kangxi radical ⼀ (U+2F00); the PDF's ToUnicode must not say ⼀."""
    md = tmp_path / "cjk.md"
    md.write_text(CJK_MARKDOWN, encoding="utf-8")
    raw = tmp_path / "raw.pdf"
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", _RAW_PDF_EXPORT,
         "--export", "pdf", str(md), str(raw)],
        cwd=ROOT, env=_env(), capture_output=True, text=True, encoding="utf-8",
        timeout=240,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    if not _radicals(_tounicode_text(raw)):
        pytest.skip(
            "no installed CJK font shares glyphs between ideographs and radicals "
            "(e.g. Microsoft JhengHei), so Chromium's raw PDF has nothing to repair"
        )

    out = tmp_path / "cjk.pdf"
    result = _run(["--export", "pdf", md, out])
    assert result.returncode == 0, result.stderr + result.stdout
    text = _tounicode_text(out)
    assert _radicals(text) == [], text
    for word in ("一、二、目", "第一項", "第二項", "巢狀項目", "粗體目錄"):
        assert word in text, text


@pytest.mark.parametrize("fmt", ["pdf", "html"])
def test_offscreen_export_exits_zero(sample, tmp_path, fmt):
    """Callers judge success by the exit code. The export's WebEngine view used
    to outlive the job and crash QApplication teardown under the offscreen
    platform (0xC0000005) after the file had already been written."""
    out = tmp_path / f"out.{fmt}"
    rj = tmp_path / "result.json"
    result = _run(["--export", fmt, sample, out, "--result-json", rj],
                  env=_offscreen_env())
    _assert_ok(result, rj, fmt, sample, out)
    if fmt == "pdf":
        assert out.read_bytes().startswith(b"%PDF")
    else:
        assert "CLI Title" in out.read_text(encoding="utf-8")


def test_offscreen_unwritable_output_exit_4(sample, tmp_path):
    blocker = tmp_path / "not_a_folder"
    blocker.write_text("x", encoding="utf-8")
    out = blocker / "out.pdf"
    rj = tmp_path / "r.json"
    result = _run(["--export", "pdf", sample, out, "--result-json", rj],
                  env=_offscreen_env())
    assert result.returncode == 4, result.stderr + result.stdout
    data = _read_json(rj)
    assert data["ok"] is False and data["exitCode"] == 4 and data["error"]
    assert blocker.read_text(encoding="utf-8") == "x"


def test_output_in_other_folder_is_created(sample, tmp_path):
    out = tmp_path / "nested" / "dir" / "out.html"
    result = _run(["--export", "html", sample, out])
    assert result.returncode == 0, result.stderr
    assert out.is_file()


def test_unsupported_format_exit_2(sample, tmp_path):
    rj = tmp_path / "r.json"
    result = _run(["--export", "rtf", sample, tmp_path / "o.rtf", "--result-json", rj])
    assert result.returncode == 2
    data = _read_json(rj)
    assert data["ok"] is False and data["exitCode"] == 2 and data["error"]


def test_missing_arguments_exit_2(tmp_path):
    assert _run(["--export", "docx"]).returncode == 2
    assert _run(["--export"]).returncode == 2
    assert _run(["--export", "docx", "a.md", "b.docx", "--bogus"]).returncode == 2


def test_missing_input_exit_3(tmp_path):
    rj = tmp_path / "r.json"
    result = _run(["--export", "docx", tmp_path / "nope.md", tmp_path / "o.docx",
                   "--result-json", rj])
    assert result.returncode == 3
    assert _read_json(rj)["exitCode"] == 3
    assert not (tmp_path / "o.docx").exists()


def test_non_markdown_input_exit_3(tmp_path):
    txt = tmp_path / "a.txt"
    txt.write_text("x", encoding="utf-8")
    assert _run(["--export", "html", txt, tmp_path / "o.html"]).returncode == 3


def test_output_equals_input_exit_3(sample, tmp_path):
    before = sample.read_bytes()
    result = _run(["--export", "html", sample, sample])
    assert result.returncode == 3
    assert sample.read_bytes() == before
    # case-insensitive spelling of the same file on Windows
    result = _run(["--export", "html", sample, str(sample).upper()])
    assert result.returncode == 3
    assert sample.read_bytes() == before


def test_unknown_option_exit_2_without_window(tmp_path):
    started = time.monotonic()
    result = _run(["--foo"], timeout=30)  # a GUI would block until the timeout
    assert result.returncode == 2
    assert time.monotonic() - started < 25
    result = _run(["--foo", str(tmp_path / "x.md")], timeout=30)
    assert result.returncode == 2


def test_version(tmp_path):
    rj = tmp_path / "v.json"
    result = _run(["--version", "--result-json", rj])
    assert result.returncode == 0
    assert _read_json(rj) == {"schema": 1, "version": VERSION}
    assert VERSION in result.stdout


def test_capabilities(tmp_path):
    rj = tmp_path / "c.json"
    result = _run(["--capabilities", "--result-json", rj])
    assert result.returncode == 0
    data = _read_json(rj)
    assert data["schema"] == 1 and data["version"] == VERSION
    assert data["formats"] == ["docx", "pptx", "pdf", "html"]
    assert data["batch"] is True
    assert set(data["exitCodes"]) == {"0", "2", "3", "4"}
    assert json.loads(result.stdout) == data


def test_export_never_forwards_to_running_instance(qapp, sample, tmp_path):
    from PySide6.QtNetwork import QLocalServer

    name = "MarkdownViewer-single-instance"
    QLocalServer.removeServer(name)
    server = QLocalServer()
    assert server.listen(name), server.errorString()
    connections: list[int] = []
    server.newConnection.connect(lambda: connections.append(1))
    out = tmp_path / "o.html"
    rj = tmp_path / "r.json"
    try:
        proc = subprocess.Popen(
            [sys.executable, "-X", "utf8", str(MAIN), "--export", "html",
             str(sample), str(out), "--result-json", str(rj)],
            cwd=ROOT, env=_env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        deadline = time.monotonic() + 240
        while proc.poll() is None and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.02)
        assert proc.poll() == 0
        for _ in range(20):
            qapp.processEvents()
            time.sleep(0.01)
        assert connections == []
        assert not server.hasPendingConnections()
        assert out.is_file() and _read_json(rj)["ok"] is True
    finally:
        if proc.poll() is None:
            proc.kill()
        server.close()
        QLocalServer.removeServer(name)


def test_batch_export(sample, tmp_path):
    manifest = tmp_path / "m.json"
    jobs = [
        {"format": "html", "input": str(sample), "output": str(tmp_path / "b.html")},
        {"format": "docx", "input": str(sample), "output": str(tmp_path / "b.docx")},
        {"format": "pdf", "input": str(tmp_path / "missing.md"),
         "output": str(tmp_path / "b.pdf")},
    ]
    manifest.write_text(json.dumps({"jobs": jobs}), encoding="utf-8")
    rj = tmp_path / "r.json"
    result = _run(["--export-batch", manifest, "--result-json", rj])
    assert result.returncode == 4
    data = _read_json(rj)
    assert data["ok"] is False and len(data["results"]) == 3
    assert [r["exitCode"] for r in data["results"]] == [0, 0, 3]
    assert (tmp_path / "b.html").is_file() and (tmp_path / "b.docx").is_file()

    good = tmp_path / "g.json"
    good.write_text(json.dumps({"jobs": jobs[:2]}), encoding="utf-8")
    result = _run(["--export-batch", good, "--result-json", rj])
    assert result.returncode == 0
    assert _read_json(rj)["ok"] is True


def test_batch_bad_manifest_exit_2(tmp_path):
    manifest = tmp_path / "m.json"
    manifest.write_text("{not json", encoding="utf-8")
    assert _run(["--export-batch", manifest]).returncode == 2
    assert _run(["--export-batch", tmp_path / "none.json"]).returncode == 3
