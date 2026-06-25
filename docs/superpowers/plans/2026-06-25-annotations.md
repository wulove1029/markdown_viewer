# Annotations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a highlight/note/tag annotation layer over the rendered Markdown document, persisted in a non-destructive sidecar file next to each `.md`.

**Architecture:** Pure-Python data layer (`annotations.py`, `tag_index.py`) is unit-tested with pytest. A `QWebChannel` bridge (`annotation_bridge.py`) connects in-page JavaScript (`assets/annotations.js`) to Python. The JS captures selections, anchors them with a TextQuoteSelector, and renders `<mark>` elements; Python persists to `<md>.notes.json`. Qt UI adds a "標註" tab and a recent-list tag filter.

**Tech Stack:** Python 3.11+, PyQt6, PyQt6-WebEngine, QWebChannel, pytest (offscreen for WebEngine tests), vanilla JS.

**Spec:** `docs/superpowers/specs/2026-06-25-annotations-design.md`

---

## File Structure

**New files**
- `app/annotations.py` — `Annotation`, `DocumentAnnotations`, `AnnotationStore` (sidecar load/save).
- `app/tag_index.py` — `TagIndex` (AppData tag cache for cross-file filtering).
- `app/annotation_bridge.py` — `AnnotationBridge(QObject)` QWebChannel slots/signals.
- `assets/annotations.js` — selection toolbar, anchoring, mark rendering, channel wiring.
- `tests/conftest.py` — offscreen Qt + `qapp` fixture.
- `tests/test_annotations.py`, `tests/test_tag_index.py`, `tests/test_annotation_bridge.py`.
- `requirements-dev.txt` — pytest.

**Modified files**
- `app/renderer.py` — web channel, JS injection on load, annotation render/remove/scroll methods.
- `app/theme.py` — add `highlighter` icon.
- `app/ribbon.py` — 4th tab button.
- `app/left_panel.py` — add `AnnotationsPanel` tab.
- `app/annotations_panel.py` — NEW panel widget (kept separate so `left_panel.py` stays small).
- `app/recent_files.py` — tag filter.
- `app/window.py` — wiring: load sidecar on open, bridge signals → persist + panel.
- `assets/obsidian-light.css` — `mark.annot` styles.
- `README.md` — Annotations section.

**Convention:** ids are lowercase uuid4 hex (no dashes), generated in JS at creation time and reused by Python verbatim.

---

## Task 1: Test scaffolding

**Files:**
- Create: `requirements-dev.txt`, `tests/__init__.py`, `tests/conftest.py`

- [ ] **Step 1: Add dev requirements**

`requirements-dev.txt`:
```
pytest>=8.0
```

- [ ] **Step 2: Create the test package + offscreen conftest**

`tests/__init__.py`: (empty file)

`tests/conftest.py`:
```python
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app
```

- [ ] **Step 3: Verify pytest collects nothing yet (no error)**

Run: `python -m pytest -q`
Expected: `no tests ran` (exit code 5) — confirms collection works.

- [ ] **Step 4: Commit**

```bash
git add requirements-dev.txt tests/__init__.py tests/conftest.py
git commit -m "test: add pytest scaffolding with offscreen Qt"
```

---

## Task 2: Annotation model + sidecar store

**Files:**
- Create: `app/annotations.py`
- Test: `tests/test_annotations.py`

- [ ] **Step 1: Write failing tests**

`tests/test_annotations.py`:
```python
import json
from pathlib import Path

from app.annotations import Annotation, AnnotationStore, DocumentAnnotations


def test_sidecar_path_appends_notes_json():
    assert AnnotationStore.sidecar_path("a/b/foo.md") == Path("a/b/foo.md.notes.json")


def test_save_then_load_roundtrip(tmp_path):
    md = tmp_path / "doc.md"
    md.write_text("# hi", encoding="utf-8")
    ann = Annotation.new(exact="hello", prefix="say ", suffix=" world",
                         textPosition=4, color="#ffd54f", note="n", tags=["重要"])
    doc = DocumentAnnotations(doc_tags=["待讀"], annotations=[ann])
    AnnotationStore.save(md, doc)

    loaded = AnnotationStore.load(md)
    assert loaded.doc_tags == ["待讀"]
    assert len(loaded.annotations) == 1
    a = loaded.annotations[0]
    assert a.id == ann.id
    assert a.exact == "hello"
    assert a.tags == ["重要"]


def test_load_missing_returns_empty(tmp_path):
    doc = AnnotationStore.load(tmp_path / "nope.md")
    assert doc.doc_tags == [] and doc.annotations == []


def test_corrupt_sidecar_is_backed_up(tmp_path):
    md = tmp_path / "doc.md"
    side = AnnotationStore.sidecar_path(md)
    side.write_text("{not json", encoding="utf-8")
    doc = AnnotationStore.load(md)
    assert doc.annotations == []
    assert side.with_suffix(side.suffix + ".bak").exists()


def test_save_is_atomic_and_utf8(tmp_path):
    md = tmp_path / "doc.md"
    AnnotationStore.save(md, DocumentAnnotations(doc_tags=["中文"]))
    raw = json.loads(AnnotationStore.sidecar_path(md).read_text(encoding="utf-8"))
    assert raw["schema"] == 1
    assert raw["doc_tags"] == ["中文"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_annotations.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.annotations'`

- [ ] **Step 3: Implement the model + store**

`app/annotations.py`:
```python
"""Annotation data model and sidecar (.notes.json) persistence."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

SCHEMA_VERSION = 1
DEFAULT_COLOR = "#ffd54f"


@dataclass
class Annotation:
    id: str
    exact: str
    prefix: str = ""
    suffix: str = ""
    textPosition: int = 0
    color: str = DEFAULT_COLOR
    note: str = ""
    tags: list[str] = field(default_factory=list)
    created: str = ""
    updated: str = ""

    @staticmethod
    def new(exact, prefix="", suffix="", textPosition=0,
            color=DEFAULT_COLOR, note="", tags=None):
        now = datetime.now().isoformat(timespec="seconds")
        return Annotation(
            id=uuid.uuid4().hex, exact=exact, prefix=prefix, suffix=suffix,
            textPosition=int(textPosition), color=color, note=note,
            tags=list(tags or []), created=now, updated=now,
        )

    def to_dict(self):
        return asdict(self)

    @staticmethod
    def from_dict(d):
        return Annotation(
            id=d.get("id") or uuid.uuid4().hex,
            exact=d.get("exact", ""),
            prefix=d.get("prefix", ""),
            suffix=d.get("suffix", ""),
            textPosition=int(d.get("textPosition", 0)),
            color=d.get("color", DEFAULT_COLOR),
            note=d.get("note", ""),
            tags=list(d.get("tags", [])),
            created=d.get("created", ""),
            updated=d.get("updated", ""),
        )


@dataclass
class DocumentAnnotations:
    doc_tags: list[str] = field(default_factory=list)
    annotations: list[Annotation] = field(default_factory=list)


class AnnotationStore:
    @staticmethod
    def sidecar_path(md_path) -> Path:
        p = Path(md_path)
        return p.with_name(p.name + ".notes.json")

    @classmethod
    def load(cls, md_path) -> DocumentAnnotations:
        path = cls.sidecar_path(md_path)
        if not path.exists():
            return DocumentAnnotations()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            try:
                path.replace(path.with_suffix(path.suffix + ".bak"))
            except OSError:
                pass
            return DocumentAnnotations()
        anns = [Annotation.from_dict(a) for a in data.get("annotations", [])]
        return DocumentAnnotations(
            doc_tags=list(data.get("doc_tags", [])), annotations=anns
        )

    @classmethod
    def save(cls, md_path, doc: DocumentAnnotations) -> None:
        path = cls.sidecar_path(md_path)
        payload = {
            "schema": SCHEMA_VERSION,
            "doc_tags": list(doc.doc_tags),
            "annotations": [a.to_dict() for a in doc.annotations],
        }
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_annotations.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add app/annotations.py tests/test_annotations.py
git commit -m "feat: annotation model and sidecar persistence"
```

---

## Task 3: Tag index (cross-file cache)

**Files:**
- Create: `app/tag_index.py`
- Test: `tests/test_tag_index.py`

- [ ] **Step 1: Write failing tests**

`tests/test_tag_index.py`:
```python
from app.annotations import Annotation, DocumentAnnotations
from app.tag_index import TagIndex


def _doc(doc_tags, annot_tags):
    anns = [Annotation.new(exact="x", tags=annot_tags)] if annot_tags else []
    return DocumentAnnotations(doc_tags=doc_tags, annotations=anns)


def test_update_and_query(tmp_path):
    idx = TagIndex(path=tmp_path / "idx.json")
    idx.update(tmp_path / "a.md", _doc(["PD"], ["重要"]))
    idx.update(tmp_path / "b.md", _doc(["PD", "待讀"], []))
    assert "PD" in idx.all_tags()
    assert "重要" in idx.all_tags()
    pd_files = idx.files_with_tag("PD")
    assert len(pd_files) == 2


def test_empty_doc_removes_entry(tmp_path):
    idx = TagIndex(path=tmp_path / "idx.json")
    md = tmp_path / "a.md"
    idx.update(md, _doc(["PD"], []))
    idx.update(md, _doc([], []))
    assert idx.all_tags() == []


def test_persists_across_instances(tmp_path):
    p = tmp_path / "idx.json"
    TagIndex(path=p).update(tmp_path / "a.md", _doc(["PD"], []))
    assert "PD" in TagIndex(path=p).all_tags()


def test_prune_removes_missing_files(tmp_path):
    idx = TagIndex(path=tmp_path / "idx.json")
    idx.update(tmp_path / "ghost.md", _doc(["PD"], []))
    idx.prune()
    assert idx.all_tags() == []
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_tag_index.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.tag_index'`

- [ ] **Step 3: Implement**

`app/tag_index.py`:
```python
"""Central tag cache for cross-file tag filtering (stored under AppData)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from PyQt6.QtCore import QStandardPaths


def _default_index_path() -> Path:
    base = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppDataLocation
    )
    return Path(base or ".") / "markdown-viewer" / "tag_index.json"


class TagIndex:
    def __init__(self, path=None):
        self._path = Path(path) if path else _default_index_path()
        self._data: dict[str, dict] = {}
        self._load()

    def _load(self):
        try:
            self._data = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            self._data = {}

    def _save(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp, self._path)

    def update(self, md_path, doc):
        key = str(Path(md_path).resolve())
        annot_tags = sorted({t for a in doc.annotations for t in a.tags})
        if not doc.doc_tags and not doc.annotations:
            self._data.pop(key, None)
        else:
            self._data[key] = {
                "doc_tags": list(doc.doc_tags),
                "annot_tags": annot_tags,
                "count": len(doc.annotations),
            }
        self._save()

    def all_tags(self) -> list[str]:
        tags: set[str] = set()
        for entry in self._data.values():
            tags.update(entry.get("doc_tags", []))
            tags.update(entry.get("annot_tags", []))
        return sorted(tags)

    def files_with_tag(self, tag) -> list[str]:
        out = []
        for path, entry in self._data.items():
            if tag in entry.get("doc_tags", []) or tag in entry.get("annot_tags", []):
                out.append(path)
        return out

    def prune(self):
        for path in list(self._data.keys()):
            if not Path(path).exists():
                self._data.pop(path, None)
        self._save()
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_tag_index.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add app/tag_index.py tests/test_tag_index.py
git commit -m "feat: tag index cache for cross-file filtering"
```

---

## Task 4: QWebChannel bridge

**Files:**
- Create: `app/annotation_bridge.py`
- Test: `tests/test_annotation_bridge.py` (signal-emission part)

- [ ] **Step 1: Write failing tests**

`tests/test_annotation_bridge.py`:
```python
import json

from app.annotation_bridge import AnnotationBridge


def test_add_emits_added(qapp):
    bridge = AnnotationBridge()
    got = []
    bridge.added.connect(got.append)
    bridge.add('{"id":"abc","exact":"x"}')
    assert got == ['{"id":"abc","exact":"x"}']


def test_remove_emits_removed(qapp):
    bridge = AnnotationBridge()
    got = []
    bridge.removed.connect(got.append)
    bridge.remove("abc")
    assert got == ["abc"]


def test_report_orphans_parses_json(qapp):
    bridge = AnnotationBridge()
    got = []
    bridge.orphansReported.connect(got.append)
    bridge.reportOrphans(json.dumps(["a", "b"]))
    assert got == [["a", "b"]]
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_annotation_bridge.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.annotation_bridge'`

- [ ] **Step 3: Implement**

`app/annotation_bridge.py`:
```python
"""QWebChannel bridge: JavaScript in the rendered page calls these slots."""

from __future__ import annotations

import json

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot


class AnnotationBridge(QObject):
    added = pyqtSignal(str)            # full annotation payload (json, id included)
    changed = pyqtSignal(str, str)     # id, fields json
    removed = pyqtSignal(str)          # id
    clicked = pyqtSignal(str)          # id
    orphansReported = pyqtSignal(list)  # list[str] of ids

    @pyqtSlot(str)
    def add(self, payload_json):
        self.added.emit(payload_json)

    @pyqtSlot(str, str)
    def update(self, ann_id, fields_json):
        self.changed.emit(ann_id, fields_json)

    @pyqtSlot(str)
    def remove(self, ann_id):
        self.removed.emit(ann_id)

    @pyqtSlot(str)
    def clickedAnnotation(self, ann_id):
        self.clicked.emit(ann_id)

    @pyqtSlot(str)
    def reportOrphans(self, ids_json):
        try:
            ids = json.loads(ids_json)
        except json.JSONDecodeError:
            ids = []
        self.orphansReported.emit(ids)
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_annotation_bridge.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add app/annotation_bridge.py tests/test_annotation_bridge.py
git commit -m "feat: QWebChannel annotation bridge"
```

---

## Task 5: In-page annotation JavaScript

**Files:**
- Create: `assets/annotations.js`

This file has no standalone unit test; it is exercised end-to-end in Task 6.

- [ ] **Step 1: Write the JS**

`assets/annotations.js`:
```javascript
(function () {
  "use strict";

  var COLORS = ["#ffd54f", "#a5d6a7", "#90caf9", "#f48fb1", "#ce93d8"];
  var bridge = null;
  var toolbar = null;

  // ---- text/node mapping -------------------------------------------------
  function buildMap() {
    var walker = document.createTreeWalker(
      document.body, NodeFilter.SHOW_TEXT, {
        acceptNode: function (n) {
          if (!n.nodeValue) return NodeFilter.FILTER_REJECT;
          var p = n.parentNode;
          if (p && p.closest &&
              p.closest("script,style,.annot-toolbar")) {
            return NodeFilter.FILTER_REJECT;
          }
          return NodeFilter.FILTER_ACCEPT;
        }
      });
    var nodes = [], offsets = [], text = "", n;
    while ((n = walker.nextNode())) {
      offsets.push(text.length);
      nodes.push(n);
      text += n.nodeValue;
    }
    return { nodes: nodes, offsets: offsets, text: text };
  }

  function nodeBase(map, node) {
    for (var i = 0; i < map.nodes.length; i++) {
      if (map.nodes[i] === node) return map.offsets[i];
    }
    return -1;
  }

  function offsetsOfRange(map, range) {
    var sb = nodeBase(map, range.startContainer);
    var eb = nodeBase(map, range.endContainer);
    if (sb < 0 || eb < 0) return null;
    return { start: sb + range.startOffset, end: eb + range.endOffset };
  }

  function rangeFromOffsets(map, start, end) {
    var sNode = null, sOff = 0, eNode = null, eOff = 0;
    for (var i = 0; i < map.nodes.length; i++) {
      var base = map.offsets[i];
      var len = map.nodes[i].nodeValue.length;
      if (sNode === null && start >= base && start < base + len) {
        sNode = map.nodes[i]; sOff = start - base;
      }
      if (end > base && end <= base + len) {
        eNode = map.nodes[i]; eOff = end - base; break;
      }
    }
    if (!sNode || !eNode) return null;
    var r = document.createRange();
    r.setStart(sNode, sOff); r.setEnd(eNode, eOff);
    return r;
  }

  function resolveStart(map, ann) {
    if (!ann.exact) return -1;
    var text = map.text, best = -1, bestScore = -1;
    var idx = text.indexOf(ann.exact);
    while (idx !== -1) {
      var before = text.slice(Math.max(0, idx - ann.prefix.length), idx);
      var after = text.slice(idx + ann.exact.length,
                             idx + ann.exact.length + ann.suffix.length);
      var score = 0;
      if (ann.prefix && before.endsWith(ann.prefix)) score += 2;
      if (ann.suffix && after.startsWith(ann.suffix)) score += 2;
      score += 1 - Math.min(1, Math.abs(idx - ann.textPosition) / 1000);
      if (score > bestScore) { bestScore = score; best = idx; }
      idx = text.indexOf(ann.exact, idx + 1);
    }
    return best;
  }

  // ---- mark rendering ----------------------------------------------------
  function wrapRange(range, ann) {
    var nodes = [];
    var walker = document.createTreeWalker(
      range.commonAncestorContainer, NodeFilter.SHOW_TEXT, {
        acceptNode: function (n) {
          return range.intersectsNode(n)
            ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
        }
      });
    var n;
    while ((n = walker.nextNode())) nodes.push(n);
    if (nodes.length === 0 && range.startContainer.nodeType === 3) {
      nodes.push(range.startContainer);
    }
    nodes.forEach(function (node) {
      var s = (node === range.startContainer) ? range.startOffset : 0;
      var e = (node === range.endContainer) ? range.endOffset
                                            : node.nodeValue.length;
      if (e <= s) return;
      var sub = document.createRange();
      sub.setStart(node, s); sub.setEnd(node, e);
      var mark = document.createElement("mark");
      mark.className = "annot";
      mark.setAttribute("data-id", ann.id);
      mark.style.background = ann.color;
      try { sub.surroundContents(mark); } catch (err) { /* skip */ }
    });
  }

  function unwrap(id) {
    var marks = document.querySelectorAll('mark.annot[data-id="' + id + '"]');
    marks.forEach(function (m) {
      var parent = m.parentNode;
      while (m.firstChild) parent.insertBefore(m.firstChild, m);
      parent.removeChild(m);
      parent.normalize();
    });
  }

  // ---- selection toolbar -------------------------------------------------
  function hideToolbar() { if (toolbar) toolbar.style.display = "none"; }

  function ensureToolbar() {
    if (toolbar) return toolbar;
    toolbar = document.createElement("div");
    toolbar.className = "annot-toolbar";
    toolbar.style.display = "none";
    COLORS.forEach(function (c) {
      var sw = document.createElement("button");
      sw.className = "annot-swatch";
      sw.style.background = c;
      sw.title = "高亮";
      sw.addEventListener("mousedown", function (ev) {
        ev.preventDefault();
        createFromSelection(c);
      });
      toolbar.appendChild(sw);
    });
    document.body.appendChild(toolbar);
    return toolbar;
  }

  function showToolbar(rect) {
    var t = ensureToolbar();
    t.style.display = "flex";
    var top = window.scrollY + rect.top - t.offsetHeight - 8;
    if (top < window.scrollY) top = window.scrollY + rect.bottom + 8;
    t.style.top = top + "px";
    t.style.left = (window.scrollX + rect.left) + "px";
  }

  function createFromSelection(color) {
    var sel = window.getSelection();
    if (!sel || sel.isCollapsed) return;
    var range = sel.getRangeAt(0);
    var map = buildMap();
    var info = offsetsOfRange(map, range);
    if (!info || info.end <= info.start) { hideToolbar(); return; }
    var ann = {
      id: (crypto.randomUUID ? crypto.randomUUID() :
           String(Date.now()) + Math.random()).replace(/-/g, ""),
      exact: map.text.slice(info.start, info.end),
      prefix: map.text.slice(Math.max(0, info.start - 32), info.start),
      suffix: map.text.slice(info.end, info.end + 32),
      textPosition: info.start,
      color: color, note: "", tags: []
    };
    wrapRange(range, ann);
    sel.removeAllRanges();
    hideToolbar();
    if (bridge) bridge.add(JSON.stringify(ann));
  }

  // ---- public API (called from Python) -----------------------------------
  window.__annot = {
    render: function (jsonStr) {
      var list;
      try { list = JSON.parse(jsonStr || "[]"); } catch (e) { list = []; }
      var map = buildMap();
      var orphans = [];
      list.forEach(function (ann) {
        var start = resolveStart(map, ann);
        if (start < 0) { orphans.push(ann.id); return; }
        var range = rangeFromOffsets(map, start, start + ann.exact.length);
        if (!range) { orphans.push(ann.id); return; }
        wrapRange(range, ann);
      });
      if (bridge) bridge.reportOrphans(JSON.stringify(orphans));
    },
    remove: function (id) { unwrap(id); },
    updateColor: function (id, color) {
      document.querySelectorAll('mark.annot[data-id="' + id + '"]')
        .forEach(function (m) { m.style.background = color; });
    },
    scrollTo: function (id) {
      var m = document.querySelector('mark.annot[data-id="' + id + '"]');
      if (m) m.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  };

  window.__annotBoot = function (jsonStr) {
    function afterChannel() { window.__annot.render(jsonStr); }
    if (typeof QWebChannel !== "undefined" && window.qt &&
        qt.webChannelTransport) {
      new QWebChannel(qt.webChannelTransport, function (channel) {
        bridge = channel.objects.bridge;
        afterChannel();
      });
    } else {
      afterChannel();
    }
  };

  document.addEventListener("mouseup", function (e) {
    if (e.target.closest && e.target.closest(".annot-toolbar")) return;
    var sel = window.getSelection();
    if (!sel || sel.isCollapsed || !sel.toString().trim()) {
      hideToolbar();
      return;
    }
    var rect = sel.getRangeAt(0).getBoundingClientRect();
    showToolbar(rect);
  });

  document.addEventListener("mousedown", function (e) {
    var m = e.target.closest && e.target.closest("mark.annot");
    if (m && bridge) bridge.clickedAnnotation(m.getAttribute("data-id"));
  });
})();
```

- [ ] **Step 2: Syntax-check the JS via Node (if available) or skip**

Run: `node --check assets/annotations.js` (if Node present)
Expected: no output (valid). If Node is unavailable, Task 6 validates it in WebEngine.

- [ ] **Step 3: Commit**

```bash
git add assets/annotations.js
git commit -m "feat: in-page annotation JS (anchoring, marks, toolbar)"
```

---

## Task 6: Renderer integration (web channel + injection)

**Files:**
- Modify: `app/renderer.py`
- Test: `tests/test_annotation_bridge.py` (append end-to-end render test)

- [ ] **Step 1: Write the failing end-to-end test**

Append to `tests/test_annotation_bridge.py`:
```python
import tempfile
from pathlib import Path

from PyQt6.QtCore import QEventLoop, QTimer

from app.annotations import Annotation
from app.renderer import RendererView


def _wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def _eval(view, js):
    box = {}
    loop = QEventLoop()
    def cb(v):
        box["v"] = v
        loop.quit()
    view.page().runJavaScript(js, cb)
    QTimer.singleShot(4000, loop.quit)
    loop.exec()
    return box.get("v")


def test_stored_annotation_renders_mark(qapp, tmp_path):
    md = tmp_path / "d.md"
    md.write_text("The quick brown fox jumps over the lazy dog.", encoding="utf-8")
    ann = Annotation.new(exact="brown fox", prefix="quick ", suffix=" jumps",
                         textPosition=10)
    view = RendererView()
    view.resize(700, 500)
    view.set_annotations([ann.to_dict()])
    view.load_file(md)
    _wait(4000)
    count = _eval(view, "document.querySelectorAll('mark.annot').length")
    assert count == 1


def test_orphan_annotation_not_rendered(qapp, tmp_path):
    md = tmp_path / "d.md"
    md.write_text("nothing matches here", encoding="utf-8")
    ann = Annotation.new(exact="absent phrase", textPosition=0)
    view = RendererView()
    view.resize(700, 500)
    view.set_annotations([ann.to_dict()])
    view.load_file(md)
    _wait(4000)
    count = _eval(view, "document.querySelectorAll('mark.annot').length")
    assert count == 0
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_annotation_bridge.py -q`
Expected: FAIL — `AttributeError: 'RendererView' object has no attribute 'set_annotations'`

- [ ] **Step 3: Add imports to `app/renderer.py`**

After the existing imports (the block ending with `from .md_converter import convert, state_page_html`), add:
```python
from PyQt6.QtCore import QFile, QIODevice
from PyQt6.QtWebChannel import QWebChannel

from .annotation_bridge import AnnotationBridge
```

- [ ] **Step 4: Set up the channel in `__init__`**

In `RendererView.__init__`, after `self._theme = "light"` and before `self.show_empty()`, insert:
```python
        self.bridge = AnnotationBridge(self)
        self._channel = QWebChannel(self)
        self._channel.registerObject("bridge", self.bridge)
        self.page().setWebChannel(self._channel)
        self._annot_json = "[]"
        self._qwebchannel_js = self._read_resource(":/qtwebchannel/qwebchannel.js")
        self._annotations_js = (
            Path(__file__).parent.parent / "assets" / "annotations.js"
        ).read_text(encoding="utf-8")
        self.page().loadFinished.connect(self._inject_annotations)
```

- [ ] **Step 5: Add the helper + injection + public methods**

Add these methods to `RendererView` (e.g. after `reload_current`):
```python
    @staticmethod
    def _read_resource(path: str) -> str:
        f = QFile(path)
        if f.open(QIODevice.OpenModeFlag.ReadOnly):
            data = bytes(f.readAll()).decode("utf-8")
            f.close()
            return data
        return ""

    def _inject_annotations(self, ok):
        if not ok or not self._current_path:
            return
        boot = "window.__annotBoot(%s);" % json.dumps(self._annot_json)
        self.page().runJavaScript(
            self._qwebchannel_js + "\n" + self._annotations_js + "\n" + boot
        )

    def set_annotations(self, annotations: list[dict]):
        self._annot_json = json.dumps(annotations, ensure_ascii=False)
        self.page().runJavaScript(
            "window.__annot && window.__annot.render(%s)" % json.dumps(self._annot_json)
        )

    def remove_annotation(self, ann_id: str):
        self.page().runJavaScript(
            "window.__annot && window.__annot.remove(%s)" % json.dumps(ann_id)
        )

    def update_annotation_color(self, ann_id: str, color: str):
        self.page().runJavaScript(
            "window.__annot && window.__annot.updateColor(%s,%s)"
            % (json.dumps(ann_id), json.dumps(color))
        )

    def scroll_to_annotation(self, ann_id: str):
        self.page().runJavaScript(
            "window.__annot && window.__annot.scrollTo(%s)" % json.dumps(ann_id)
        )
```

- [ ] **Step 6: Run the end-to-end tests**

Run: `python -m pytest tests/test_annotation_bridge.py -q`
Expected: PASS (5 passed)

- [ ] **Step 7: Commit**

```bash
git add app/renderer.py tests/test_annotation_bridge.py
git commit -m "feat: wire annotation channel and JS injection into renderer"
```

---

## Task 7: Mark styling + highlighter icon

**Files:**
- Modify: `assets/obsidian-light.css`, `app/theme.py`

- [ ] **Step 1: Add mark + toolbar CSS**

Append to `assets/obsidian-light.css`:
```css
mark.annot {
  border-radius: 2px;
  padding: 0 1px;
  color: inherit;
  cursor: pointer;
}

.theme-dark mark.annot {
  mix-blend-mode: screen;
}

.annot-toolbar {
  position: absolute;
  z-index: 9999;
  display: flex;
  gap: 6px;
  padding: 6px 8px;
  background: #2b2b2b;
  border-radius: 8px;
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.35);
}

.annot-swatch {
  width: 18px;
  height: 18px;
  border: 1px solid rgba(255, 255, 255, 0.4);
  border-radius: 50%;
  cursor: pointer;
  padding: 0;
}
```

- [ ] **Step 2: Add the highlighter icon**

In `app/theme.py`, in the `ICONS` dict, after the `"file-down"` entry add:
```python
    "highlighter": (
        '<path d="m9 11-6 6v3h9l3-3"/>'
        '<path d="m22 12-4.6 4.6a2 2 0 0 1-2.8 0l-5.2-5.2a2 2 0 0 1 0-2.8L14 4"/>'
    ),
```

- [ ] **Step 3: Verify icon loads**

Run:
```bash
python -c "import os; os.environ['QT_QPA_PLATFORM']='offscreen'; \
from PyQt6.QtWidgets import QApplication; QApplication([]); \
from app.theme import svg_icon; print('ok', not svg_icon('highlighter', '#000').isNull())"
```
Expected: `ok True`

- [ ] **Step 4: Commit**

```bash
git add assets/obsidian-light.css app/theme.py
git commit -m "feat: annotation mark styles and highlighter icon"
```

---

## Task 8: Annotations panel widget

**Files:**
- Create: `app/annotations_panel.py`

This panel lists annotations for the current document, edits the selected one
(note, color, tags), edits document tags, and filters by tag. It communicates
with the window through plain callbacks passed in the constructor (matching the
existing `on_file_selected` pattern).

- [ ] **Step 1: Implement the panel**

`app/annotations_panel.py`:
```python
"""Left-panel tab listing and editing annotations for the current document."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QColorDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .theme import LIGHT, Theme, collection_stylesheet


class _NoteEdit(QPlainTextEdit):
    """QPlainTextEdit that emits editingFinished when it loses focus."""

    editingFinished = pyqtSignal()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.editingFinished.emit()


class AnnotationsPanel(QWidget):
    def __init__(self, callbacks: dict, parent=None):
        super().__init__(parent)
        # callbacks: note_changed(id,text), color_changed(id,hex),
        # tags_changed(id,list), deleted(id), doc_tags_changed(list),
        # activated(id)
        self._cb = callbacks
        self._theme = LIGHT
        self._doc = None
        self._selected_id = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        layout.addWidget(QLabel("文件標籤"))
        self._doc_tags = QLineEdit()
        self._doc_tags.setPlaceholderText("以逗號分隔，如：PD協定, 待讀")
        self._doc_tags.editingFinished.connect(self._emit_doc_tags)
        layout.addWidget(self._doc_tags)

        self._filter = QLineEdit()
        self._filter.setPlaceholderText("篩選標籤…")
        self._filter.textChanged.connect(self._refresh_list)
        layout.addWidget(self._filter)

        self._list = QListWidget()
        self._list.itemClicked.connect(self._on_item_clicked)
        self._list.itemDoubleClicked.connect(self._on_item_activated)
        layout.addWidget(self._list, stretch=1)

        layout.addWidget(QLabel("備註"))
        self._note = _NoteEdit()
        self._note.setFixedHeight(80)
        self._note.editingFinished.connect(self._emit_note)
        layout.addWidget(self._note)

        self._tags = QLineEdit()
        self._tags.setPlaceholderText("此標註的標籤，逗號分隔")
        self._tags.editingFinished.connect(self._emit_tags)
        layout.addWidget(self._tags)

        row = QHBoxLayout()
        self._color_btn = QPushButton("顏色…")
        self._color_btn.clicked.connect(self._pick_color)
        self._delete_btn = QPushButton("刪除")
        self._delete_btn.clicked.connect(self._delete_selected)
        row.addWidget(self._color_btn)
        row.addWidget(self._delete_btn)
        layout.addLayout(row)

        self.apply_theme(LIGHT)
        self._set_editor_enabled(False)

    # ---- external API ----
    def apply_theme(self, theme: Theme):
        self._theme = theme
        self.setStyleSheet(collection_stylesheet(theme, "QListWidget"))

    def set_document(self, doc):
        self._doc = doc
        self._doc_tags.setText(", ".join(doc.doc_tags) if doc else "")
        self._selected_id = None
        self._set_editor_enabled(False)
        self._refresh_list()

    def select(self, ann_id: str):
        self._selected_id = ann_id
        self._load_editor(ann_id)
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == ann_id:
                self._list.setCurrentItem(item)
                break

    # ---- internal ----
    def _refresh_list(self):
        self._list.clear()
        if not self._doc:
            return
        flt = self._filter.text().strip()
        for a in self._doc.annotations:
            if flt and flt not in a.tags and flt not in (a.note or ""):
                continue
            label = (a.exact[:40] + "…") if len(a.exact) > 40 else a.exact
            if a.tags:
                label += "  #" + " #".join(a.tags)
            item = QListWidgetItem("● " + label)
            item.setForeground(QColor(a.color))
            item.setData(Qt.ItemDataRole.UserRole, a.id)
            self._list.addItem(item)

    def _find(self, ann_id):
        if not self._doc:
            return None
        for a in self._doc.annotations:
            if a.id == ann_id:
                return a
        return None

    def _on_item_clicked(self, item):
        self._selected_id = item.data(Qt.ItemDataRole.UserRole)
        self._load_editor(self._selected_id)

    def _on_item_activated(self, item):
        ann_id = item.data(Qt.ItemDataRole.UserRole)
        self._cb["activated"](ann_id)

    def _load_editor(self, ann_id):
        a = self._find(ann_id)
        if not a:
            self._set_editor_enabled(False)
            return
        self._set_editor_enabled(True)
        self._note.setPlainText(a.note)
        self._tags.setText(", ".join(a.tags))

    def _set_editor_enabled(self, on):
        for w in (self._note, self._tags, self._color_btn, self._delete_btn):
            w.setEnabled(on)

    def _emit_note(self):
        if self._selected_id:
            self._cb["note_changed"](self._selected_id, self._note.toPlainText())

    def _emit_tags(self):
        if self._selected_id:
            tags = [t.strip() for t in self._tags.text().split(",") if t.strip()]
            self._cb["tags_changed"](self._selected_id, tags)

    def _emit_doc_tags(self):
        tags = [t.strip() for t in self._doc_tags.text().split(",") if t.strip()]
        self._cb["doc_tags_changed"](tags)

    def _pick_color(self):
        a = self._find(self._selected_id)
        if not a:
            return
        color = QColorDialog.getColor(QColor(a.color), self, "選擇高亮顏色")
        if color.isValid():
            self._cb["color_changed"](self._selected_id, color.name())

    def _delete_selected(self):
        if self._selected_id:
            self._cb["deleted"](self._selected_id)
```

- [ ] **Step 2: Smoke-test construction**

Run:
```bash
python -c "import os; os.environ['QT_QPA_PLATFORM']='offscreen'; \
from PyQt6.QtWidgets import QApplication; QApplication([]); \
from app.annotations_panel import AnnotationsPanel; \
cb={k:(lambda *a:None) for k in ['note_changed','color_changed','tags_changed','deleted','doc_tags_changed','activated']}; \
print('ok', AnnotationsPanel(cb) is not None)"
```
Expected: `ok True`

- [ ] **Step 3: Commit**

```bash
git add app/annotations_panel.py
git commit -m "feat: annotations panel widget"
```

---

## Task 9: Add the "標註" tab and ribbon button

**Files:**
- Modify: `app/left_panel.py`, `app/ribbon.py`

- [ ] **Step 1: Add the ribbon button**

In `app/ribbon.py`, in `Ribbon.__init__`, after `self._add_btn("☰", "目錄", 2)` add:
```python
        self._add_btn("✍", "標註", 3)
```

- [ ] **Step 2: Add the panel tab in `left_panel.py`**

Add import near the other panel imports:
```python
from .annotations_panel import AnnotationsPanel
```

Change the `LeftPanel.__init__` signature to accept annotation callbacks:
```python
    def __init__(self, on_file_selected, on_anchor_clicked,
                 annotation_callbacks, theme: Theme = LIGHT, parent=None):
```

After `self._toc = TocView(on_anchor_clicked=on_anchor_clicked)` add:
```python
        self._annotations = AnnotationsPanel(annotation_callbacks)
```

After `self._tabs.addTab(self._toc, "目錄")` add:
```python
        self._tabs.addTab(self._annotations, "標註")
```

Add a property after the `recent` property:
```python
    @property
    def annotations(self) -> AnnotationsPanel:
        return self._annotations
```

In `apply_theme`, after `self._toc.apply_theme(theme)` add:
```python
        self._annotations.apply_theme(theme)
```

- [ ] **Step 3: Verify (window construction is covered in Task 10)**

Run: `python -m pytest -q`
Expected: existing tests still PASS (no regressions from imports).

- [ ] **Step 4: Commit**

```bash
git add app/ribbon.py app/left_panel.py
git commit -m "feat: add 標註 tab and ribbon button"
```

---

## Task 10: Window wiring

**Files:**
- Modify: `app/window.py`

- [ ] **Step 1: Add imports**

After `from .annotations import ...` location (top of file, with the other
`from .` imports), add:
```python
import json

from .annotations import Annotation, AnnotationStore, DocumentAnnotations
from .tag_index import TagIndex
```
(If `json` is already imported, do not duplicate it.)

- [ ] **Step 2: Build annotation state + callbacks in `__init__`**

In `MainWindow.__init__`, before `self._panel = LeftPanel(...)`, add:
```python
        self._tag_index = TagIndex()
        self._doc_annotations = DocumentAnnotations()
        annotation_callbacks = {
            "note_changed": self._annot_note_changed,
            "color_changed": self._annot_color_changed,
            "tags_changed": self._annot_tags_changed,
            "deleted": self._annot_deleted,
            "doc_tags_changed": self._annot_doc_tags_changed,
            "activated": self._annot_activated,
        }
```

Update the `LeftPanel(...)` call to pass the callbacks:
```python
        self._panel = LeftPanel(
            on_file_selected=self._open_file,
            on_anchor_clicked=self._scroll_to_anchor,
            annotation_callbacks=annotation_callbacks,
            theme=self._theme,
        )
```

- [ ] **Step 3: Connect bridge signals (after `self._renderer` is created)**

Immediately after the renderer is constructed in `__init__`, add:
```python
        self._renderer.bridge.added.connect(self._on_bridge_added)
        self._renderer.bridge.changed.connect(self._on_bridge_changed)
        self._renderer.bridge.removed.connect(self._on_bridge_removed)
        self._renderer.bridge.clicked.connect(self._on_bridge_clicked)
        self._renderer.bridge.orphansReported.connect(self._on_bridge_orphans)
```

- [ ] **Step 4: Load the sidecar on open**

In `_open_file`, after `self._current_file = path` and before
`self._renderer.load_file(path)`, add:
```python
        self._doc_annotations = AnnotationStore.load(path)
        self._renderer.set_annotations(
            [a.to_dict() for a in self._doc_annotations.annotations]
        )
        self._panel.annotations.set_document(self._doc_annotations)
```

- [ ] **Step 5: Add the handler methods**

Add to `MainWindow` (near `_reload_current`):
```python
    def _persist_annotations(self):
        if not self._current_file:
            return
        AnnotationStore.save(self._current_file, self._doc_annotations)
        self._tag_index.update(self._current_file, self._doc_annotations)
        self._panel.annotations.set_document(self._doc_annotations)

    def _find_annotation(self, ann_id):
        for a in self._doc_annotations.annotations:
            if a.id == ann_id:
                return a
        return None

    # --- signals from the page (bridge) ---
    def _on_bridge_added(self, payload_json):
        if not self._current_file:
            return
        ann = Annotation.from_dict(json.loads(payload_json))
        self._doc_annotations.annotations.append(ann)
        self._persist_annotations()

    def _on_bridge_changed(self, ann_id, fields_json):
        a = self._find_annotation(ann_id)
        if not a:
            return
        fields = json.loads(fields_json)
        for key, value in fields.items():
            setattr(a, key, value)
        self._persist_annotations()

    def _on_bridge_removed(self, ann_id):
        self._doc_annotations.annotations = [
            a for a in self._doc_annotations.annotations if a.id != ann_id
        ]
        self._renderer.remove_annotation(ann_id)
        self._persist_annotations()

    def _on_bridge_clicked(self, ann_id):
        self._panel.switch_to(3)
        self._panel.annotations.select(ann_id)

    def _on_bridge_orphans(self, ids):
        # Orphans remain listed in the panel; no document marks to show.
        pass

    # --- callbacks from the annotations panel ---
    def _annot_note_changed(self, ann_id, text):
        a = self._find_annotation(ann_id)
        if a and a.note != text:
            a.note = text
            self._persist_annotations()

    def _annot_color_changed(self, ann_id, color):
        a = self._find_annotation(ann_id)
        if a:
            a.color = color
            self._renderer.update_annotation_color(ann_id, color)
            self._persist_annotations()

    def _annot_tags_changed(self, ann_id, tags):
        a = self._find_annotation(ann_id)
        if a and a.tags != tags:
            a.tags = tags
            self._persist_annotations()

    def _annot_deleted(self, ann_id):
        self._on_bridge_removed(ann_id)

    def _annot_doc_tags_changed(self, tags):
        self._doc_annotations.doc_tags = tags
        self._persist_annotations()

    def _annot_activated(self, ann_id):
        self._renderer.scroll_to_annotation(ann_id)
```

- [ ] **Step 6: Manual smoke test**

Run: `python main.py`
Steps: open a `.md`, select text, click a color swatch → highlight appears and a
`<file>.notes.json` is created next to the file; switch to the 標註 tab → the
annotation is listed; edit its note/tags/color; reload (refresh button) → the
highlight re-appears.

- [ ] **Step 7: Run the full suite**

Run: `python -m pytest -q`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add app/window.py
git commit -m "feat: wire annotations into the main window"
```

---

## Task 11: Recent-list tag filter

**Files:**
- Modify: `app/recent_files.py`

Adds a tag filter combo above the list. Because `RecentFilesView` is a
`QListWidget`, wrap filtering by reading the `TagIndex` passed from the window.

- [ ] **Step 1: Accept a tag index and add a filter combo**

In `RecentFilesView.__init__`, change the signature to:
```python
    def __init__(self, on_file_selected, tag_index=None, parent=None):
```
Store it:
```python
        self._tag_index = tag_index
        self._active_tag = ""
```

- [ ] **Step 2: Filter `_refresh` by the active tag**

Replace the body of `_refresh` loop guard so that, when `_active_tag` is set and
a `tag_index` exists, only files carrying that tag are shown. Replace `_refresh`
with:
```python
    def _refresh(self):
        self.clear()
        allowed = None
        if self._active_tag and self._tag_index is not None:
            allowed = set(self._tag_index.files_with_tag(self._active_tag))
        has_items = False
        for p in self._load():
            path = Path(p)
            if not path.exists():
                continue
            if allowed is not None and str(path.resolve()) not in allowed:
                continue
            item = QListWidgetItem(path.name)
            item.setToolTip(p)
            item.setData(Qt.ItemDataRole.UserRole, p)
            self.addItem(item)
            has_items = True
        if not has_items:
            msg = "沒有符合標籤的檔案" if self._active_tag else "尚無最近開啟的檔案"
            item = QListWidgetItem(msg)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.addItem(item)
```

- [ ] **Step 3: Public setter for the active tag**

Add:
```python
    def set_tag_filter(self, tag: str):
        self._active_tag = tag or ""
        self._refresh()
```

- [ ] **Step 4: Pass the index from `left_panel.py` and `window.py`**

In `left_panel.py`, change the recent construction to:
```python
        self._recent = RecentFilesView(
            on_file_selected=on_file_selected, tag_index=annotation_callbacks.get("tag_index")
        )
```
In `window.py`, add `"tag_index": self._tag_index` to the
`annotation_callbacks` dict built in Task 10 Step 2.

- [ ] **Step 5: Run suite + manual check**

Run: `python -m pytest -q` → PASS.
Manual: tag a document, reopen the app, set the filter tag → only matching files
remain in the recent list.

- [ ] **Step 6: Commit**

```bash
git add app/recent_files.py app/left_panel.py app/window.py
git commit -m "feat: filter recent files by tag"
```

---

## Task 12: README documentation

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add an Annotations section**

After the "Images And Diagrams" section in `README.md`, add:
```markdown
## Annotations

Select text in the preview to highlight it (pick a color from the popup), then
use the **標註** tab to add a note or tags, change the color, or delete it. Tag a
whole file in the same tab, and filter the **最近** list by tag to find files.

Annotations are saved in a sidecar file named `<document>.md.notes.json` next to
the Markdown file. They never modify your Markdown source. If you move or rename
the Markdown file, move the `.notes.json` with it to keep the annotations.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: document annotations feature"
```

---

## Task 13: Release

**Files:**
- Modify: `app/version.py`, `installer.iss` (via bump script)

- [ ] **Step 1: Bump version**

Run: `python tools/bump_version.py 1.3.0`
(Annotations is a feature addition; use a minor bump. Adjust if the maintainer
prefers a patch.)

- [ ] **Step 2: Commit**

```bash
git add -A
git commit -m "Bump version to 1.3.0"
```

- [ ] **Step 3: Push + tag (release flow per DEVELOPMENT.md §6)**

Pushing this repo requires the `wulove1029` GitHub account (see project memory
`release-push-auth`). The default credential lacks access:
```bash
gh auth switch -u wulove1029
git -c credential.helper= -c credential.helper='!gh auth git-credential' push origin main
git tag v1.3.0
git -c credential.helper= -c credential.helper='!gh auth git-credential' push origin v1.3.0
gh auth switch -u jerrywu-voltraware
```

- [ ] **Step 4: Verify the release build**

Run: `gh run watch <run-id> --repo wulove1029/markdown_viewer --exit-status`
Then confirm the `MarkdownViewer_Setup_v1.3.0.exe` asset on the release page.

---

## Self-Review Notes

- **Spec coverage:** highlights+colors (Tasks 5–7), notes (Tasks 8, 10),
  per-selection tags (Tasks 8, 10), document tags (Tasks 8, 10), cross-file
  filtering (Tasks 3, 11), sidecar persistence (Task 2), anchoring + orphans
  (Tasks 5, 6, 10), bridge (Task 4), tests (Tasks 2–6). All spec sections map to
  a task.
- **Cross-file "all recent" annotation view:** v1 delivers tag-based file
  filtering in the recent list (Task 11); a combined annotation browser across
  files is intentionally deferred (noted as a future enhancement, not in scope).
- **Bridge id ownership:** ids are generated in JS (`crypto.randomUUID`) and
  reused by Python via `Annotation.from_dict`, avoiding an async round-trip. This
  refines the spec's "Python assigns id" wording without changing behavior.
- **Naming consistency:** bridge slot `clickedAnnotation` (JS call) →
  signal `clicked`; renderer methods `set_annotations` / `remove_annotation` /
  `update_annotation_color` / `scroll_to_annotation` are used identically in
  Task 10.
```
