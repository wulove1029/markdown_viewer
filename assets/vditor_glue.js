/* Glue between a bundled Vditor 3.11.3 instance (WYSIWYG mode) and the
 * QWebChannel bridge Python registers as "wysiwygBridge".
 *
 * Design mirrors the inline_edit.js / annotations.js split: this file owns
 * no DOM beyond what Vditor itself builds, and every side effect is reached
 * through explicit functions so a Node DOM/Vditor stub can drive it (see
 * tests/js/vditor_glue_harness.js).
 *
 * Contract (see wysiwyg_view.py and the WYSIWYG spec):
 *  - JS -> Py: bridge.contentChanged(markdown) on debounced input (250ms).
 *  - JS -> Py: bridge.saveRequested() on Ctrl+S (native browser save must
 *    never fire; the window's own Ctrl+S save pipeline owns persistence).
 *  - JS -> Py: bridge.ready() once Vditor has finished constructing.
 *  - JS -> Py: bridge.escRequested() on a "real" Esc (see onKeydown below) --
 *    the window leaves WYSIWYG back to PREVIEW, keeping the buffer dirty.
 *  - Py -> JS: window.__wysiwygGlue.setValue(text) loads Python-owned text
 *    (open / external reload / backend switch). This must NOT bounce back
 *    as a contentChanged push -- the echo guard below suppresses the input
 *    event(s) it triggers.
 */
"use strict";

(function (win, doc) {
  var DEBOUNCE_MS = 250;

  // Full toolbar, "identical to Office Viewer" per the v2 spec: outline
  // sidebar, undo/redo, headings, inline marks, indent/outdent, lists,
  // quote/code, and table (Ctrl+M) -- everything Vditor itself binds a
  // shortcut to. Export and VSCode-specific buttons (upload/record/devtools/
  // fullscreen/edit-mode/both/preview/help) are deliberately left out: this
  // is an offline, always-WYSIWYG embed with no export pipeline of its own.
  // Simple, offline, distinguishable-by-letter SVG icon (no external asset,
  // no emoji font dependency) for the v4 custom toolbar buttons below.
  function letterIcon(letter) {
    return (
      '<svg viewBox="0 0 32 32" width="16" height="16">' +
      '<text x="16" y="23" font-size="18" text-anchor="middle" ' +
      'fill="currentColor">' + letter + "</text></svg>"
    );
  }

  var BASE_TOOLBAR = [
    "outline", "|",
    "undo", "redo", "|",
    "headings", "bold", "italic", "strike", "|",
    "link", "|",
    "list", "ordered-list", "check", "outdent", "indent", "|",
    "quote", "line", "code", "inline-code", "table", "|",
  ];

  function createGlue() {
    var state = {
      vditor: null,
      bridge: null,
      debounceTimer: null,
      // True while a Python-driven setValue() is in flight: the input
      // event(s) it causes must not be pushed back as a user edit.
      echoGuard: false,
      ready: false,
    };

    function pushContent() {
      state.debounceTimer = null;
      if (state.echoGuard || !state.vditor || !state.bridge) return;
      state.bridge.contentChanged(state.vditor.getValue());
    }

    // v4: custom toolbar button -> bridge.toolbarAction(name). window.py
    // dispatches "save"/"export_pdf"/"export_docx"/"export_html"/
    // "insert_image"/"toggle_theme" to the existing Qt-side handlers.
    function toolbarAction(name) {
      if (state.bridge && typeof state.bridge.toolbarAction === "function") {
        state.bridge.toolbarAction(name);
      }
    }

    // v4: right-click anywhere in the Vditor surface -> bridge.
    // contextMenuRequested(x, y) instead of the browser's native menu; the
    // native one is suppressed here (preventDefault) and window.py builds a
    // QMenu (copy/paste/export/insert image/reveal) positioned at (x, y),
    // which are viewport-relative and map 1:1 onto this widget's local
    // coordinates (see WysiwygView._on_context_menu_requested).
    function onContextMenu(event) {
      if (event && typeof event.preventDefault === "function") {
        event.preventDefault();
      }
      if (!state.bridge || typeof state.bridge.contextMenuRequested !== "function") {
        return;
      }
      var x = event && typeof event.clientX === "number" ? event.clientX : 0;
      var y = event && typeof event.clientY === "number" ? event.clientY : 0;
      state.bridge.contextMenuRequested(x, y);
    }

    // v4: custom buttons (save / export PDF·Word·HTML / insert image /
    // theme) route through toolbarAction(name) -> bridge.toolbarAction(name);
    // window.py owns what each one actually does (file dialogs, atomic
    // save, theme state). Built here (not at module scope) so each click
    // closure can reach this instance's `state`.
    var CUSTOM_TOOLBAR_ITEMS = [
      { name: "save", tip: "存檔 (Ctrl+S)", tipPosition: "s", icon: letterIcon("S"),
        click: function () { toolbarAction("save"); } },
      { name: "export-pdf", tip: "匯出 PDF…", tipPosition: "s", icon: letterIcon("P"),
        click: function () { toolbarAction("export_pdf"); } },
      { name: "export-docx", tip: "匯出 Word…", tipPosition: "s", icon: letterIcon("W"),
        click: function () { toolbarAction("export_docx"); } },
      { name: "export-html", tip: "匯出 HTML…", tipPosition: "s", icon: letterIcon("H"),
        click: function () { toolbarAction("export_html"); } },
      { name: "insert-image", tip: "插入圖片…", tipPosition: "s", icon: letterIcon("I"),
        click: function () { toolbarAction("insert_image"); } },
      { name: "theme-toggle", tip: "切換深色模式", tipPosition: "s", icon: letterIcon("T"),
        click: function () { toolbarAction("toggle_theme"); } },
    ];

    function onInput() {
      if (state.echoGuard) {
        // Consume exactly the one echo this guard was raised for; a real
        // keystroke landing right after must debounce normally again.
        state.echoGuard = false;
        return;
      }
      if (state.debounceTimer) win.clearTimeout(state.debounceTimer);
      state.debounceTimer = win.setTimeout(pushContent, DEBOUNCE_MS);
    }

    // Vditor's autocomplete/emoji/heading hint popups use the ".vditor-hint"
    // class and toggle CSS display to show/hide -- see index.min.js. The
    // *first* Esc while one is open must only close that panel (Vditor's own
    // job); only a "clean" Esc, with nothing open, leaves WYSIWYG entirely.
    function isHintPanelOpen() {
      if (!doc || !doc.querySelectorAll) return false;
      var hints = doc.querySelectorAll(".vditor-hint");
      for (var i = 0; i < hints.length; i++) {
        var el = hints[i];
        if (el && el.style && el.style.display && el.style.display !== "none") {
          return true;
        }
      }
      return false;
    }

    function onKeydown(event) {
      var ctrl = event.ctrlKey || event.metaKey;
      if (ctrl) {
        var key = String(event.key || "").toLowerCase();
        if (key !== "s") return;
        event.preventDefault();
        // Flush any debounced edit *before* asking Python to save, in that
        // order: QWebChannel preserves call order, so contentChanged reaches
        // Python ahead of saveRequested and Ctrl+S can never save text one
        // keystroke stale behind the 250ms debounce window.
        flushPending();
        if (state.bridge && typeof state.bridge.saveRequested === "function") {
          state.bridge.saveRequested();
        }
        return;
      }
      if (event.key !== "Escape" && event.key !== "Esc") return;
      // Checked in the capture phase (this listener is registered with
      // useCapture=true below), i.e. *before* Vditor's own bubble-phase
      // handler has a chance to close the panel -- otherwise the panel would
      // already read as closed by the time this runs, and a single Esc would
      // both close the hint AND leave WYSIWYG in one keystroke.
      if (isHintPanelOpen()) return;
      if (state.bridge && typeof state.bridge.escRequested === "function") {
        state.bridge.escRequested();
      }
    }

    // ---- v4 second wave: Notion-style block handles (+ / drag-to-reorder) ----
    //
    // Technical choice: pointer events (pointerdown/pointermove/pointerup)
    // driving a manual DOM reorder, NOT the HTML5 native drag-and-drop API.
    // Native DnD inside a contenteditable region fights the browser's own
    // text-drag/selection machinery -- a dragstart fired on a contenteditable
    // selection, drop targets that paste HTML instead of moving the node, and
    // no reliable way to keep the native drag image from being the selected
    // text instead of the block -- all well-documented contenteditable/DnD
    // pain points. Pointer events give full manual control over hit-testing,
    // the insertion-line feedback and auto-scroll instead, at the cost of
    // implementing that hit-testing ourselves (see findDropTarget below).
    //
    // Handles are two DOM elements (a "+" and a "::" grip) absolutely
    // positioned over the *wrapper* Vditor itself builds around the
    // contenteditable root (`.vditor-wysiwyg > pre.vditor-reset`, see
    // Vditor's WYSIWYG constructor) -- never inside the contenteditable tree,
    // so they can never be mistaken for document content or interfere with
    // getValue()/setValue(). Listeners are bound to the handles themselves
    // (click / pointerdown) or to the contenteditable root (mouseover, to
    // detect which top-level block is hovered) -- deliberately not a single
    // capture-phase listener on the whole `.vditor-wysiwyg` container, so
    // existing text selection, table cell drag/resize and code-block
    // scrolling are never intercepted by this feature.
    var HANDLE_HIDE_DELAY_MS = 120;
    var AUTOSCROLL_EDGE_PX = 40;
    var AUTOSCROLL_STEP_PX = 16;
    var HANDLE_STYLE = (
      ".vditor-block-handle-group{position:absolute;display:none;" +
      "flex-direction:row;gap:2px;z-index:5;}" +
      ".vditor-block-handle{display:flex;align-items:center;justify-content:center;" +
      "width:18px;height:20px;border-radius:4px;font-size:13px;line-height:1;" +
      "color:var(--second-color,rgba(88,96,105,0.6));background-color:transparent;" +
      "cursor:pointer;user-select:none;-webkit-user-select:none;}" +
      ".vditor-block-handle:hover{background-color:var(--textarea-background-color," +
      "rgba(0,0,0,0.06));}" +
      ".vditor-block-handle--drag{cursor:grab;}" +
      ".vditor-block-drop-indicator{position:absolute;left:0;right:0;height:2px;" +
      "background-color:#4285f4;display:none;z-index:6;pointer-events:none;}" +
      ".vditor-block-drag-source{opacity:0.4;}"
    );

    // A top-level block is a direct child of the contenteditable root; walk
    // up from whatever the mouseover landed on until we reach one.
    function getTopLevelBlock(node) {
      var editable = state.editableEl;
      if (!editable || !node) return null;
      while (node && node.parentNode !== editable) {
        node = node.parentNode;
      }
      return node && node.parentNode === editable ? node : null;
    }

    function clearHandleHideTimer() {
      if (state.handleHideTimer) {
        win.clearTimeout(state.handleHideTimer);
        state.handleHideTimer = null;
      }
    }

    function scheduleHideHandles() {
      clearHandleHideTimer();
      state.handleHideTimer = win.setTimeout(function () {
        state.handleHideTimer = null;
        hideHandles();
      }, HANDLE_HIDE_DELAY_MS);
    }

    function hideHandles() {
      if (!state.handleGroup) return;
      state.handleGroup.style.display = "none";
      state.hoverBlock = null;
    }

    function positionHandlesFor(block) {
      if (!state.handleGroup || !state.wysiwygRoot || !block) return;
      state.hoverBlock = block;
      if (typeof block.getBoundingClientRect === "function" &&
          typeof state.wysiwygRoot.getBoundingClientRect === "function") {
        var rect = block.getBoundingClientRect();
        var rootRect = state.wysiwygRoot.getBoundingClientRect();
        state.handleGroup.style.top = (rect.top - rootRect.top) + "px";
        state.handleGroup.style.left = (rect.left - rootRect.left - 38) + "px";
      }
      state.handleGroup.style.display = "flex";
    }

    function onBlockMouseOver(event) {
      if (state.dnd && state.dnd.active) return;
      var block = getTopLevelBlock(event && event.target);
      if (!block) return;
      clearHandleHideTimer();
      positionHandlesFor(block);
    }

    function onEditableMouseLeave() {
      if (state.dnd && state.dnd.active) return;
      scheduleHideHandles();
    }

    function onHandleMouseEnter() {
      clearHandleHideTimer();
    }

    function onHandleMouseLeave() {
      scheduleHideHandles();
    }

    // Best-effort caret placement in the freshly inserted paragraph; the "+"
    // insert must never fail just because focusing it did.
    function focusStartOf(el) {
      try {
        if (!doc.createRange || !win.getSelection) return;
        var range = doc.createRange();
        range.selectNodeContents(el);
        range.collapse(true);
        var sel = win.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
        if (typeof el.focus === "function") el.focus();
      } catch (e) {
        // Caret placement is a convenience only -- swallow and move on.
      }
    }

    function onPlusClick(event) {
      if (event && typeof event.preventDefault === "function") event.preventDefault();
      var block = state.hoverBlock;
      if (!block || !block.parentNode) return;
      var p = doc.createElement("p");
      block.parentNode.insertBefore(p, block.nextSibling);
      focusStartOf(p);
      // Same debounce/push path a real keystroke takes -- no separate call.
      onInput();
    }

    function ensureDropIndicator() {
      if (state.dropIndicator) return state.dropIndicator;
      var el = doc.createElement("div");
      el.className = "vditor-block-drop-indicator";
      el.style.display = "none";
      state.wysiwygRoot.appendChild(el);
      state.dropIndicator = el;
      return el;
    }

    function showDropIndicator(target, position) {
      var el = ensureDropIndicator();
      if (typeof target.getBoundingClientRect === "function" &&
          typeof state.wysiwygRoot.getBoundingClientRect === "function") {
        var rect = target.getBoundingClientRect();
        var rootRect = state.wysiwygRoot.getBoundingClientRect();
        var top = position === "before" ? rect.top : rect.top + rect.height;
        el.style.top = (top - rootRect.top) + "px";
      }
      el.style.display = "block";
    }

    function hideDropIndicator() {
      if (state.dropIndicator) state.dropIndicator.style.display = "none";
    }

    // Walks the top-level blocks in document order and returns the first one
    // whose vertical midpoint is below clientY (-> insert "before" it), or
    // the last block (-> insert "after" it) if the cursor is below all of
    // them. The block currently being dragged is never a valid target.
    function findDropTarget(clientY) {
      var editable = state.editableEl;
      if (!editable || !editable.children || !state.dnd) return null;
      var children = editable.children;
      var last = null;
      for (var i = 0; i < children.length; i++) {
        var child = children[i];
        if (child === state.dnd.source) continue;
        var rect = typeof child.getBoundingClientRect === "function"
          ? child.getBoundingClientRect() : { top: 0, height: 0 };
        var mid = rect.top + rect.height / 2;
        if (clientY < mid) return { target: child, position: "before" };
        last = child;
      }
      return last ? { target: last, position: "after" } : null;
    }

    function autoScrollIfNeeded(clientY) {
      var editable = state.editableEl;
      if (!editable || typeof editable.getBoundingClientRect !== "function") return;
      if (typeof editable.scrollTop !== "number") return;
      var rect = editable.getBoundingClientRect();
      if (clientY - rect.top < AUTOSCROLL_EDGE_PX) {
        editable.scrollTop = Math.max(0, editable.scrollTop - AUTOSCROLL_STEP_PX);
      } else if (rect.top + rect.height - clientY < AUTOSCROLL_EDGE_PX) {
        editable.scrollTop = editable.scrollTop + AUTOSCROLL_STEP_PX;
      }
    }

    function onDragPointerMove(event) {
      if (!state.dnd || !state.dnd.active) return;
      var clientY = event && typeof event.clientY === "number" ? event.clientY : 0;
      autoScrollIfNeeded(clientY);
      var drop = findDropTarget(clientY);
      state.dnd.drop = drop;
      if (!drop) { hideDropIndicator(); return; }
      showDropIndicator(drop.target, drop.position);
    }

    function endDrag() {
      if (!state.dnd) return;
      doc.removeEventListener("pointermove", onDragPointerMove);
      doc.removeEventListener("pointerup", onDragPointerUp);
      doc.removeEventListener("pointercancel", onDragPointerUp);
      if (state.dnd.source && state.dnd.source.classList) {
        state.dnd.source.classList.remove("vditor-block-drag-source");
      }
      hideDropIndicator();
      hideHandles();
      state.dnd = { active: false };
    }

    function onDragPointerUp(event) {
      if (!state.dnd || !state.dnd.active) return;
      var drop = state.dnd.drop;
      var source = state.dnd.source;
      var moved = false;
      if (drop && drop.target && drop.target !== source && source && source.parentNode) {
        var parent = drop.target.parentNode;
        if (drop.position === "before") {
          parent.insertBefore(source, drop.target);
        } else {
          parent.insertBefore(source, drop.target.nextSibling);
        }
        moved = true;
      }
      endDrag();
      if (moved) {
        // v4 requirement: the drop must reach Python through the *exact
        // same* debounce/push path a real keystroke uses -- reusing onInput
        // here is what guarantees that (no separate bridge.contentChanged
        // call is ever made for a drag).
        onInput();
      }
    }

    function onDragPointerDown(event) {
      if (event && typeof event.button === "number" && event.button !== 0) return;
      var block = state.hoverBlock;
      if (!block) return;
      if (event && typeof event.preventDefault === "function") event.preventDefault();
      clearHandleHideTimer();
      state.dnd = { active: true, source: block, drop: null };
      if (block.classList) block.classList.add("vditor-block-drag-source");
      if (state.dragHandle && typeof state.dragHandle.setPointerCapture === "function" &&
          event && typeof event.pointerId !== "undefined") {
        try { state.dragHandle.setPointerCapture(event.pointerId); } catch (e) { /* best-effort */ }
      }
      doc.addEventListener("pointermove", onDragPointerMove);
      doc.addEventListener("pointerup", onDragPointerUp);
      doc.addEventListener("pointercancel", onDragPointerUp);
    }

    function injectHandleStyle() {
      if (state.styleInjected || !doc || !doc.head || typeof doc.createElement !== "function") return;
      var style = doc.createElement("style");
      style.type = "text/css";
      style.textContent = HANDLE_STYLE;
      doc.head.appendChild(style);
      state.styleInjected = true;
    }

    // Finds the DOM Vditor itself built for WYSIWYG mode (see the "vditor-
    // wysiwyg" div / "pre.vditor-reset" contenteditable pair Vditor's own
    // constructor creates) and wires the hover handles + drag onto it.
    // Idempotent and a no-op if that DOM isn't there yet (e.g. Node harness
    // stubs that never build a real Vditor DOM at all) -- called again once
    // it exists via the exposed _installBlockHandles() test hook.
    function installBlockHandles() {
      if (state.handlesInstalled) return;
      if (!doc || typeof doc.querySelector !== "function") return;
      var root = doc.querySelector(".vditor-wysiwyg");
      if (!root) return;
      var editable = root.firstElementChild;
      if (!editable) return;

      state.wysiwygRoot = root;
      state.editableEl = editable;

      injectHandleStyle();

      var group = doc.createElement("div");
      group.className = "vditor-block-handle-group";

      var plus = doc.createElement("div");
      plus.className = "vditor-block-handle vditor-block-handle--add";
      plus.title = "插入新段落";
      plus.textContent = "+";

      var drag = doc.createElement("div");
      drag.className = "vditor-block-handle vditor-block-handle--drag";
      drag.title = "拖曳排序";
      drag.textContent = "⋮⋮";

      group.appendChild(plus);
      group.appendChild(drag);
      root.appendChild(group);

      state.handleGroup = group;
      state.plusHandle = plus;
      state.dragHandle = drag;

      editable.addEventListener("mouseover", onBlockMouseOver);
      editable.addEventListener("mouseleave", onEditableMouseLeave);
      group.addEventListener("mouseenter", onHandleMouseEnter);
      group.addEventListener("mouseleave", onHandleMouseLeave);
      plus.addEventListener("click", onPlusClick);
      drag.addEventListener("pointerdown", onDragPointerDown);

      state.dnd = { active: false };
      state.handlesInstalled = true;
    }

    function boot(bridge, options) {
      options = options || {};
      state.bridge = bridge;
      var VditorCtor = win.Vditor;
      if (typeof VditorCtor !== "function") {
        throw new Error("window.Vditor is not loaded");
      }
      state.vditor = new VditorCtor(options.elementId || "vditor", {
        mode: "wysiwyg",
        lang: options.lang || "zh_TW",
        cdn: options.cdn || "",
        height: "100%",
        // Autosave / local cache must stay off: Ctrl+S -> the window's
        // atomic-write pipeline is the only path that ever touches disk.
        cache: { enable: false },
        toolbarConfig: { pin: true },
        toolbar: options.toolbar || BASE_TOOLBAR.concat(CUSTOM_TOOLBAR_ITEMS),
        preview: {
          // Vditor centres content in an 800px column by default; fill the
          // window instead (Vditor derives its own side padding from this).
          maxWidth: options.maxWidth || 100000,
          math: { engine: "KaTeX" },
          hljs: { style: options.hljsStyle || "github" },
          // mermaid/echarts renderers are not bundled offline; render the
          // fenced block as plain code rather than erroring out.
          render: { mermaid: false, echarts: false },
        },
        input: onInput,
        after: function () {
          state.ready = true;
          if (options.value) {
            setValue(options.value);
          }
          installBlockHandles();
          syncToolbarTitles();
          if (state.bridge && typeof state.bridge.ready === "function") {
            state.bridge.ready();
          }
        },
      });
      doc.addEventListener("keydown", onKeydown, true);
      doc.addEventListener("contextmenu", onContextMenu, true);
      return state.vditor;
    }

    // Vditor draws its hover tips with CSS classes; inside QWebEngineView a
    // missing/mismatched position class means no tip at all. Mirroring each
    // button's aria-label onto `title` guarantees Chromium's native hover
    // tooltip as a fallback for every toolbar button, built-in or custom.
    function syncToolbarTitles() {
      var bar = doc.querySelector(".vditor-toolbar");
      if (!bar) return;
      var buttons = bar.querySelectorAll("[aria-label]");
      for (var i = 0; i < buttons.length; i++) {
        if (!buttons[i].getAttribute("title")) {
          buttons[i].setAttribute("title", buttons[i].getAttribute("aria-label"));
        }
      }
    }

    function setValue(text) {
      if (!state.vditor) return;
      if (state.debounceTimer) {
        win.clearTimeout(state.debounceTimer);
        state.debounceTimer = null;
      }
      state.echoGuard = true;
      state.vditor.setValue(text == null ? "" : text);
      // Vditor's setValue does not reliably fire `input` synchronously (or
      // at all, for an unchanged value). Clear a guard nothing consumed on
      // the next tick so it cannot suppress the user's next real edit.
      win.setTimeout(function () {
        state.echoGuard = false;
      }, 0);
    }

    function getValue() {
      return state.vditor ? state.vditor.getValue() : "";
    }

    function flushPending() {
      if (state.debounceTimer) {
        win.clearTimeout(state.debounceTimer);
        pushContent();
      }
    }

    // v4: insert a Markdown snippet (image/attachment link) at the caret,
    // used by window.py after a file picker + image_paste.py import.
    function insertValue(text) {
      if (!state.vditor || !state.vditor.insertValue) return;
      state.vditor.insertValue(text == null ? "" : text);
    }

    return {
      boot: boot,
      setValue: setValue,
      getValue: getValue,
      insertValue: insertValue,
      flushPending: flushPending,
      _state: state,
      // Test-only hook (tests/js/vditor_glue_harness.js): production code
      // never calls this directly, it happens automatically from `after`
      // once Vditor's real WYSIWYG DOM exists.
      _installBlockHandles: installBlockHandles,
    };
  }

  win.__wysiwygGlue = createGlue();
  win.__wysiwygBoot = win.__wysiwygGlue.boot;
})(typeof window !== "undefined" ? window : globalThis, typeof document !== "undefined" ? document : null);
