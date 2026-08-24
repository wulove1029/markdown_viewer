/* Grid editor for a Markdown pipe table.
 *
 * Triple-clicking a table used to open a textarea holding the raw pipe
 * syntax, which is miserable: adding a column means retyping every row and
 * the delimiter row, and one missing pipe silently breaks the whole table.
 * This builds a real grid of contenteditable cells instead, so the pipe
 * characters, the column count and the alignment row stop being the user's
 * problem.
 *
 * Still source-level *inside* a cell on purpose: a cell holds that cell's
 * Markdown, so `**bold**` shows as `**bold**`. Inline WYSIWYG is a much
 * bigger problem than the one being solved here.
 *
 * Pure UI: no QWebChannel, no saving, no knowledge of where the table lives
 * in the document. The caller inserts .element, reads .getModel() when it
 * wants to write, calls .setBusy(true) while that write is in flight, and is
 * told about commit / cancel / raw-mode through opts.
 */
(function () {
  var styleNode = null;

  var STYLE =
    ".tedit { margin: 0 0 14px; }" +
    ".tedit-bar { display: flex; gap: 6px; margin: 0 0 6px; }" +
    ".tedit-btn {" +
    "  font: inherit; font-size: .8em; line-height: 1.6; color: inherit;" +
    "  cursor: pointer; background: rgba(128,128,128,.14);" +
    "  border: 1px solid rgba(128,128,128,.40); border-radius: 6px;" +
    "  padding: 2px 9px;" +
    "}" +
    ".tedit-btn:hover { background: rgba(128,128,128,.28); }" +
    // A wide table must scroll on its own instead of stretching the preview.
    ".tedit-scroll { overflow-x: auto; max-width: 100%; }" +
    // The theme owns every <table> in the page (github.css forces
    // display:block + width:max-content, obsidian-light.css adds
    // margin-left:50% and translateX(-50%) to centre wide ones). The grid is
    // a <table> too, so each of those has to be pinned back or it is laid out
    // as a block -- cells outside their own box, and under obsidian shifted
    // off screen entirely. !important because the page's stylesheet is not
    // ours and swaps with the theme.
    ".tedit-grid {" +
    "  display: table !important; table-layout: auto !important;" +
    // Full width, like the rendered table it stands in for: sized to content
    // the columns come out a third as wide and long cells wrap many lines
    // deep. Cell min-widths still push past this when there are enough
    // columns, and .tedit-scroll takes the overflow.
    "  width: 100% !important; min-width: 0 !important;" +
    "  max-width: none !important; margin: 0 !important;" +
    "  transform: none !important; overflow: visible !important;" +
    "  border-collapse: collapse !important; border-spacing: 0 !important;" +
    "  font-size: 1em; text-align: left;" +
    "}" +
    // Themes stripe and tint rows (thead tr, tbody tr:nth-child(even)); the
    // grid draws its own header, so keep every row neutral.
    ".tedit-grid tr {" +
    "  background: none !important; border: none !important;" +
    "}" +
    ".tedit-cell {" +
    "  border: 1px solid rgba(128,128,128,.45) !important;" +
    "  padding: 5px 8px !important; color: inherit;" +
    "  min-width: 84px; vertical-align: top; outline: none;" +
    "  text-align: inherit; font-weight: normal;" +
    '  font-family: "Cascadia Code", "Fira Code", Consolas, monospace;' +
    "  font-size: .9em; line-height: 1.6;" +
    "  white-space: pre-wrap; word-break: break-word;" +
    "}" +
    ".tedit-head .tedit-cell {" +
    "  background: rgba(128,128,128,.14); font-weight: 600;" +
    "}" +
    // Same focus blue as inline_edit.js, so both editors read as one feature.
    ".tedit-cell:focus {" +
    "  border-color: rgba(96,165,250,.85);" +
    "  box-shadow: inset 0 0 0 1px rgba(96,165,250,.85);" +
    "}" +
    ".tedit-corner, .tedit-gutter, .tedit-colhead {" +
    "  border: none !important; background: none !important;" +
    "  white-space: nowrap; font-weight: normal;" +
    "}" +
    // The row gutter is scaffolding down the left edge, so it shrinks to its
    // buttons. The column controls are not: each one has to stand over the
    // column it acts on, and a hairline width collapses them all into one
    // clump at the far left.
    ".tedit-corner, .tedit-gutter {" +
    "  width: 1px; padding: 0 3px !important;" +
    "}" +
    ".tedit-colhead { text-align: center; padding: 0 0 3px !important; }" +
    // Row/column controls are scaffolding, not content: they stay faint until
    // the pointer (or the caret) is actually inside the editor.
    ".tedit-op {" +
    "  font: inherit; font-size: .78em; line-height: 1.4; color: inherit;" +
    "  cursor: pointer; opacity: .35; transition: opacity .12s;" +
    "  background: rgba(128,128,128,.12);" +
    "  border: 1px solid rgba(128,128,128,.35); border-radius: 4px;" +
    "  padding: 0 4px; margin: 0 1px;" +
    "}" +
    ".tedit:hover .tedit-op, .tedit:focus-within .tedit-op { opacity: 1; }" +
    ".tedit-op:hover { background: rgba(128,128,128,.30); }" +
    ".tedit-align.is-on {" +
    "  opacity: 1; background: rgba(96,165,250,.30);" +
    "  border-color: rgba(96,165,250,.75);" +
    "}" +
    ".tedit-hint { font-size: .76em; opacity: .6; margin-top: 4px; }";

  function injectStyle() {
    if (styleNode && styleNode.parentNode) return;
    styleNode = document.createElement("style");
    styleNode.textContent = STYLE;
    document.head.appendChild(styleNode);
  }

  // ---- tiny DOM helpers --------------------------------------------------
  // classList is avoided on purpose: className string work is trivial here and
  // keeps the element surface small enough to drive from a Node DOM stub.
  function hasClass(el, name) {
    return (" " + (el && el.className ? el.className : "") + " ")
      .indexOf(" " + name + " ") >= 0;
  }

  function setClass(el, name, on) {
    var parts = String(el.className || "").split(/\s+/);
    var kept = [];
    for (var i = 0; i < parts.length; i++) {
      if (parts[i] && parts[i] !== name) kept.push(parts[i]);
    }
    if (on) kept.push(name);
    el.className = kept.join(" ");
  }

  function clear(node) {
    while (node.childNodes.length) {
      node.removeChild(node.childNodes[node.childNodes.length - 1]);
    }
  }

  function halt(e) {
    if (e && e.preventDefault) e.preventDefault();
  }

  function closestCell(node) {
    while (node) {
      if (hasClass(node, "tedit-cell")) return node;
      node = node.parentNode;
    }
    return null;
  }

  /* "plaintext-only" keeps a pasted rich-text fragment from smuggling real
     HTML into what is supposed to be Markdown source. Firefox and older
     engines reject the value (the property reads back unchanged), and an
     uneditable cell would be far worse than a stray <b>, so fall back. */
  function makeEditable(el) {
    try {
      el.contentEditable = "plaintext-only";
    } catch (err) {
      /* assignment threw: handled by the fallback below */
    }
    if (String(el.contentEditable || "").toLowerCase() !== "plaintext-only") {
      el.contentEditable = "true";
    }
    el.spellcheck = false;
  }

  function caretToEnd(el) {
    if (!document.createRange || !window.getSelection) return;
    try {
      var range = document.createRange();
      range.selectNodeContents(el);
      range.collapse(false);
      var sel = window.getSelection();
      if (!sel) return;
      sel.removeAllRanges();
      sel.addRange(range);
    } catch (err) {
      /* no selection API in this context; focus alone is good enough */
    }
  }

  /* execCommand is deprecated but is still the only way to type into a
     contenteditable while keeping the browser's own undo stack intact. */
  function insertText(el, text) {
    var done = false;
    if (document.execCommand) {
      try {
        done = document.execCommand("insertText", false, text);
      } catch (err) {
        done = false;
      }
    }
    if (!done) {
      el.textContent = String(el.textContent == null ? "" : el.textContent) +
        text;
      caretToEnd(el);
    }
  }

  // ---- model -------------------------------------------------------------
  function str(value) {
    return value == null ? "" : String(value);
  }

  function normAlign(value) {
    var v = str(value);
    if (v === "left" || v === "center" || v === "right") return v;
    return "";
  }

  /* Everything downstream assumes a rectangular table with at least one
     column, so square the input up once instead of guarding everywhere. */
  function normalize(model) {
    var m = model || {};
    var srcHeaders = m.headers || [];
    var headers = [];
    var i, j;
    for (i = 0; i < srcHeaders.length; i++) headers.push(str(srcHeaders[i]));
    if (!headers.length) headers.push("");
    var cols = headers.length;

    var srcAligns = m.aligns || [];
    var aligns = [];
    for (i = 0; i < cols; i++) aligns.push(normAlign(srcAligns[i]));

    var srcRows = m.rows || [];
    var rows = [];
    for (i = 0; i < srcRows.length; i++) {
      var srcRow = srcRows[i] || [];
      var row = [];
      for (j = 0; j < cols; j++) row.push(str(srcRow[j]));
      rows.push(row);
    }
    return {
      headers: headers,
      aligns: aligns,
      rows: rows,
      indent: typeof m.indent === "string" ? m.indent : ""
    };
  }

  function copyModel(data) {
    var rows = [];
    for (var i = 0; i < data.rows.length; i++) {
      rows.push(data.rows[i].slice());
    }
    return {
      headers: data.headers.slice(),
      aligns: data.aligns.slice(),
      rows: rows,
      indent: data.indent
    };
  }

  /* A pipe cell cannot span lines, so a newline that slipped in through a
     paste or a stray Enter is folded to a space rather than written out and
     breaking the table on the next parse. */
  function readCell(el) {
    return str(el.textContent).replace(/[\r\n]+/g, " ");
  }

  function parseTsv(text) {
    var lines = String(text).split(/\r\n|\r|\n/);
    while (lines.length > 1 && lines[lines.length - 1] === "") lines.pop();
    var grid = [];
    for (var i = 0; i < lines.length; i++) grid.push(lines[i].split("\t"));
    return grid;
  }

  // ---- editor ------------------------------------------------------------
  function create(model, opts) {
    injectStyle();
    var cfg = opts || {};
    var data = normalize(model);
    var destroyed = false;

    var busy = false;     // a caller-driven write is in flight

    var headCells = [];   // [col]
    var bodyCells = [];   // [row][col]
    var alignBtns = [];   // [col] -> { left, center, right }

    var element = document.createElement("div");
    element.className = "tedit";

    var bar = document.createElement("div");
    bar.className = "tedit-bar";
    element.appendChild(bar);

    var scroll = document.createElement("div");
    scroll.className = "tedit-scroll";
    element.appendChild(scroll);

    var hint = document.createElement("div");
    hint.className = "tedit-hint";
    hint.textContent = "Ctrl+Enter 儲存 · Esc 取消 · Tab 下一格 · Enter 下一列";
    element.appendChild(hint);

    function notify(name) {
      var fn = cfg[name];
      if (typeof fn === "function") fn();
    }

    function cols() { return data.headers.length; }
    function gridRows() { return data.rows.length + 1; }  // + the header row

    function blankRow() {
      var row = [];
      for (var i = 0; i < cols(); i++) row.push("");
      return row;
    }

    // ---- DOM building ----------------------------------------------------
    function button(cls, label, title, onClick) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = cls;
      b.textContent = label;
      b.title = title;
      // Keep mousedown from stealing the caret out of the cell being edited;
      // an alignment click should not cost the user their place.
      b.addEventListener("mousedown", function (e) { halt(e); });
      b.addEventListener("click", function (e) {
        halt(e);
        if (destroyed) return;
        onClick();
      });
      return b;
    }

    function makeCell(tag, text) {
      var el = document.createElement(tag);
      el.className = "tedit-cell";
      makeEditable(el);
      el.textContent = text;
      return el;
    }

    function alignButton(col, value, label, title) {
      var b = button("tedit-op tedit-align", label, title, function () {
        setAlign(col, value);
      });
      b.setAttribute("data-align", value);
      return b;
    }

    function buildColHead(col) {
      var th = document.createElement("th");
      th.className = "tedit-colhead";
      var trio = {
        left: alignButton(col, "left", "⇤", "靠左對齊"),
        center: alignButton(col, "center", "⇔", "置中對齊"),
        right: alignButton(col, "right", "⇥", "靠右對齊")
      };
      th.appendChild(trio.left);
      th.appendChild(trio.center);
      th.appendChild(trio.right);
      th.appendChild(button("tedit-op tedit-colins", "＋", "在右側插入欄",
        function () { insertCol(col + 1); }));
      th.appendChild(button("tedit-op tedit-coldel", "✕", "刪除此欄",
        function () { deleteCol(col); }));
      alignBtns[col] = trio;
      return th;
    }

    function buildGutter(row) {
      var td = document.createElement("td");
      td.className = "tedit-gutter";
      td.appendChild(button("tedit-op tedit-rowins", "＋", "在下方插入列",
        function () { insertRow(row + 1); }));
      td.appendChild(button("tedit-op tedit-rowdel", "✕", "刪除此列",
        function () { deleteRow(row); }));
      return td;
    }

    /* Structural edits rebuild the whole grid rather than patching it: the
       table is tiny, and one build path means the DOM can never drift out of
       step with the model. `focus` is {r, c} in grid coordinates (row 0 is
       the header row) or null to leave focus alone. */
    function render(focus) {
      var r, c;
      clear(scroll);
      headCells = [];
      bodyCells = [];
      alignBtns = [];

      var table = document.createElement("table");
      table.className = "tedit-grid";

      var thead = document.createElement("thead");
      var colbar = document.createElement("tr");
      colbar.className = "tedit-colbar";
      var corner = document.createElement("th");
      corner.className = "tedit-corner";
      colbar.appendChild(corner);
      for (c = 0; c < cols(); c++) colbar.appendChild(buildColHead(c));
      thead.appendChild(colbar);

      var headRow = document.createElement("tr");
      headRow.className = "tedit-head";
      var headGutter = document.createElement("th");
      headGutter.className = "tedit-gutter";
      headRow.appendChild(headGutter);
      for (c = 0; c < cols(); c++) {
        var th = makeCell("th", data.headers[c]);
        headCells.push(th);
        headRow.appendChild(th);
      }
      thead.appendChild(headRow);
      table.appendChild(thead);

      var tbody = document.createElement("tbody");
      for (r = 0; r < data.rows.length; r++) {
        var tr = document.createElement("tr");
        tr.appendChild(buildGutter(r));
        var line = [];
        for (c = 0; c < cols(); c++) {
          var td = makeCell("td", data.rows[r][c]);
          line.push(td);
          tr.appendChild(td);
        }
        bodyCells.push(line);
        tbody.appendChild(tr);
      }
      table.appendChild(tbody);
      scroll.appendChild(table);

      applyAligns();
      // A structural edit rebuilds every cell from scratch, so a grid that
      // was busy before the rebuild has to be made read-only again.
      if (busy) applyBusy();
      if (focus) focusCell(focus.r, focus.c);
    }

    // ---- busy ------------------------------------------------------------
    /* The grid's answer to textarea.readOnly on the raw editor's side. Once
       the caller has serialized getModel() and handed it to Python, anything
       typed into a cell is on its way to being thrown away, so stop
       accepting it rather than pretending it was saved. */
    function applyBusy() {
      var r, c;
      for (c = 0; c < headCells.length; c++) setCellBusy(headCells[c]);
      for (r = 0; r < bodyCells.length; r++) {
        for (c = 0; c < bodyCells[r].length; c++) setCellBusy(bodyCells[r][c]);
      }
    }

    function setCellBusy(el) {
      if (!el) return;
      if (busy) el.contentEditable = "false";
      else makeEditable(el);
    }

    // ---- alignment -------------------------------------------------------
    function applyAligns() {
      for (var c = 0; c < cols(); c++) {
        var value = data.aligns[c];
        var trio = alignBtns[c];
        if (trio) {
          setClass(trio.left, "is-on", value === "left");
          setClass(trio.center, "is-on", value === "center");
          setClass(trio.right, "is-on", value === "right");
        }
        if (headCells[c]) headCells[c].style.textAlign = value;
        for (var r = 0; r < bodyCells.length; r++) {
          if (bodyCells[r][c]) bodyCells[r][c].style.textAlign = value;
        }
      }
    }

    // Clicking the alignment already in force clears it, so "no alignment"
    // stays reachable without a fourth button.
    function setAlign(col, value) {
      if (col < 0 || col >= cols()) return;
      data.aligns[col] = data.aligns[col] === value ? "" : value;
      applyAligns();
    }

    // ---- structure -------------------------------------------------------
    function sync() {
      var c, r;
      for (c = 0; c < headCells.length; c++) {
        data.headers[c] = readCell(headCells[c]);
      }
      for (r = 0; r < bodyCells.length; r++) {
        for (c = 0; c < bodyCells[r].length; c++) {
          data.rows[r][c] = readCell(bodyCells[r][c]);
        }
      }
    }

    function insertRow(at) {
      sync();
      if (at < 0) at = 0;
      if (at > data.rows.length) at = data.rows.length;
      data.rows.splice(at, 0, blankRow());
      render({ r: at + 1, c: 0 });
    }

    function deleteRow(at) {
      if (at < 0 || at >= data.rows.length) return;
      sync();
      data.rows.splice(at, 1);
      render({ r: at + 1, c: 0 });
    }

    function insertCol(at) {
      sync();
      if (at < 0) at = 0;
      if (at > cols()) at = cols();
      data.headers.splice(at, 0, "");
      data.aligns.splice(at, 0, "");
      for (var r = 0; r < data.rows.length; r++) {
        data.rows[r].splice(at, 0, "");
      }
      render({ r: 0, c: at });
    }

    // A table with no columns is not a table; the last one is never removable.
    function deleteCol(at) {
      if (cols() <= 1 || at < 0 || at >= cols()) return;
      sync();
      data.headers.splice(at, 1);
      data.aligns.splice(at, 1);
      for (var r = 0; r < data.rows.length; r++) {
        data.rows[r].splice(at, 1);
      }
      render({ r: 0, c: at });
    }

    function ensureCols(count) {
      while (cols() < count) {
        data.headers.push("");
        data.aligns.push("");
        for (var r = 0; r < data.rows.length; r++) data.rows[r].push("");
      }
    }

    function ensureGridRows(count) {
      while (gridRows() < count) data.rows.push(blankRow());
    }

    function setValue(r, c, value) {
      if (r === 0) data.headers[c] = value;
      else data.rows[r - 1][c] = value;
    }

    // ---- navigation ------------------------------------------------------
    function cellAt(r, c) {
      if (r <= 0) return headCells[c] || null;
      var row = bodyCells[r - 1];
      return row ? (row[c] || null) : null;
    }

    function focusCell(r, c) {
      if (r < 0) r = 0;
      if (r >= gridRows()) r = gridRows() - 1;
      if (c < 0) c = 0;
      if (c >= cols()) c = cols() - 1;
      var el = cellAt(r, c);
      if (!el) return;
      el.focus();
      caretToEnd(el);
    }

    function positionOf(cell) {
      var c, r;
      for (c = 0; c < headCells.length; c++) {
        if (headCells[c] === cell) return { r: 0, c: c };
      }
      for (r = 0; r < bodyCells.length; r++) {
        for (c = 0; c < bodyCells[r].length; c++) {
          if (bodyCells[r][c] === cell) return { r: r + 1, c: c };
        }
      }
      return null;
    }

    // Tab walks the grid as one flat sequence; falling off the end grows the
    // table, which is how every spreadsheet behaves and saves a trip to the
    // "+ row" button while typing.
    function step(pos, delta) {
      var width = cols();
      var index = pos.r * width + pos.c + delta;
      if (index < 0) return;
      if (index >= gridRows() * width) {
        sync();
        data.rows.push(blankRow());
        render({ r: gridRows() - 1, c: 0 });
        return;
      }
      focusCell(Math.floor(index / width), index % width);
    }

    function nextInColumn(pos) {
      if (pos.r + 1 < gridRows()) {
        focusCell(pos.r + 1, pos.c);
        return;
      }
      sync();
      data.rows.push(blankRow());
      render({ r: gridRows() - 1, c: pos.c });
    }

    // ---- events ----------------------------------------------------------
    // Bound to `element`, never to document: inline_edit.js already owns
    // document-level keydown/paste and the two must not fight.
    element.addEventListener("keydown", function (e) {
      /* A keystroke that belongs to an IME composition is not the grid's to
         read. This app is Chinese-first, so the common case is real: the
         Enter that picks a candidate would be swallowed here, focus would
         jump to the next row, and render() would rebuild the DOM out from
         under the composition -- destroying the very characters being typed.
         Tab behaves the same way for IMEs that use it to cycle candidates.
         keyCode 229 is the pre-`isComposing` spelling of the same fact and
         is still what some Windows IMEs report. */
      if (e.isComposing || e.keyCode === 229) return;
      var cell = closestCell(e.target);
      if (!cell) return;
      var pos = positionOf(cell);
      if (!pos) return;

      if (e.key === "Tab") {
        halt(e);
        step(pos, e.shiftKey ? -1 : 1);
        return;
      }
      if (e.key === "Enter" || e.key === "Return") {
        if (e.ctrlKey || e.metaKey) {
          // Suppressed: contenteditable would otherwise insert a newline
          // while the commit is in flight. Escape stays un-suppressed --
          // nothing types on Escape, so there is nothing to race.
          halt(e);
          notify("onCommit");
          return;
        }
        if (e.shiftKey) {
          // A pipe cell has no newline; "<br>" is the customary stand-in.
          halt(e);
          insertText(cell, "<br>");
          return;
        }
        halt(e);
        nextInColumn(pos);
        return;
      }
      if (e.key === "Escape" || e.key === "Esc") {
        notify("onCancel");
      }
      // Arrow keys are left alone so the browser's own caret movement works.
    });

    /* Only a multi-cell payload is intercepted. Plain text belongs to the
       browser, which knows about the caret; guessing at it here would break
       "paste a word into the middle of a cell". */
    element.addEventListener("paste", function (e) {
      var cell = closestCell(e.target);
      if (!cell) return;
      var pos = positionOf(cell);
      if (!pos) return;
      var text = "";
      try {
        if (e.clipboardData && e.clipboardData.getData) {
          text = e.clipboardData.getData("text/plain");
        }
      } catch (err) {
        text = "";
      }
      text = str(text);
      if (!text) return;
      if (text.indexOf("\t") < 0 && text.indexOf("\n") < 0 &&
          text.indexOf("\r") < 0) {
        return;
      }
      halt(e);
      var grid = parseTsv(text);
      sync();
      var widest = 0;
      var i, j;
      for (i = 0; i < grid.length; i++) {
        if (grid[i].length > widest) widest = grid[i].length;
      }
      ensureCols(pos.c + widest);
      ensureGridRows(pos.r + grid.length);
      for (i = 0; i < grid.length; i++) {
        for (j = 0; j < grid[i].length; j++) {
          setValue(pos.r + i, pos.c + j, grid[i][j]);
        }
      }
      render({ r: pos.r, c: pos.c });
    });

    bar.appendChild(button("tedit-btn tedit-addrow", "＋列", "在最後加一列",
      function () { insertRow(data.rows.length); }));
    bar.appendChild(button("tedit-btn tedit-addcol", "＋欄", "在最後加一欄",
      function () { insertCol(cols()); }));
    bar.appendChild(button("tedit-btn tedit-raw", "切換原始碼", "改用原始碼編輯",
      function () { notify("onToggleRaw"); }));

    render(null);

    return {
      element: element,
      getModel: function () {
        if (!destroyed) sync();
        return copyModel(data);
      },
      focus: function () {
        if (!destroyed) focusCell(0, 0);
      },
      setBusy: function (flag) {
        if (destroyed) return;
        busy = !!flag;
        applyBusy();
      },
      destroy: function () {
        if (destroyed) return;
        // Everything this editor listens to hangs off `element`, so dropping
        // the subtree drops the listeners with it; nothing is on document.
        destroyed = true;
        if (element.parentNode) element.parentNode.removeChild(element);
        headCells = [];
        bodyCells = [];
        alignBtns = [];
      }
    };
  }

  window.__tableEdit = { create: create };
})();
