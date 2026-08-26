/* Glue between the bundled Office Viewer 4.2 Vditor fork (WYSIWYG mode) and
 * the QWebChannel bridge Python registers as "wysiwygBridge".
 *
 * Design mirrors the inline_edit.js / annotations.js split: this file owns
 * no DOM beyond what Vditor itself builds, and every side effect is reached
 * through explicit functions so a Node DOM/Vditor stub can drive it (see
 * tests/js/vditor_glue_harness.js).
 *
 * Contract (see wysiwyg_view.py and the WYSIWYG spec):
 *  - JS -> Py: bridge.contentDelta(...) on debounced input. Only the changed
 *    UTF-16 range crosses QWebChannel; older hosts fall back to
 *    bridge.contentChanged(fullValue, ...delta).
 *  - JS -> Py: bridge.saveWithContent(...delta) on Ctrl+S (native browser
 *    save must never fire; the window's atomic save pipeline owns disk I/O).
 *  - JS -> Py: bridge.ready() once Vditor has finished constructing.
 *  - Esc stays inside WYSIWYG. Office Viewer popovers consume it one layer
 *    at a time; a clean Esc never asks the host to leave the editor.
 *  - Py -> JS: window.__wysiwygGlue.setValue(text) loads Python-owned text
 *    (open / external reload / backend switch). This must NOT bounce back
 *    as a contentChanged push -- the echo guard below suppresses the input
 *    event(s) it triggers.
 */
"use strict";

(function (win, doc) {
  // A slightly longer idle window keeps the hidden Qt shadow document out of
  // the typing hot path. Save/tab/close transitions obtain an explicit live
  // snapshot, so this delay is only a performance policy, never a data-safety
  // boundary.
  var DEBOUNCE_MS = 450;

  function codicon(name) {
    return '<span class="codicon codicon-' + name + '" aria-hidden="true"></span>';
  }

  // Office Viewer uses a dense, single-row toolbar. The upstream fork keeps
  // the generic Vditor `flex-wrap: wrap`, which turns the 800px desktop editor
  // into two rows. Keep every action available in-order, tighten only divider
  // whitespace at compact desktop widths, and allow horizontal scrolling when
  // an even narrower host cannot contain the complete row.
  var OFFICE_LAYOUT_STYLE = (
    ".vditor-toolbar{flex-wrap:nowrap!important;justify-content:flex-start!important;" +
    "overflow-x:auto!important;overflow-y:hidden!important;}" +
    ".vditor-toolbar>.vditor-toolbar__item," +
    ".vditor-toolbar>.vditor-toolbar__divider{flex:0 0 auto;}" +
    ".vditor-toolbar>.vditor-toolbar__br{display:none!important;}" +
    ".vditor-toolbar::-webkit-scrollbar{height:4px;}" +
    "@media(max-width:900px){.vditor-toolbar__divider{" +
    "margin-left:3px!important;margin-right:3px!important;}}"
  );

  function injectOfficeLayoutStyle() {
    if (!doc || !doc.head || typeof doc.createElement !== "function") return;
    if (doc.querySelector && doc.querySelector("#wysiwyg-office-layout")) return;
    var style = doc.createElement("style");
    style.id = "wysiwyg-office-layout";
    style.type = "text/css";
    style.textContent = OFFICE_LAYOUT_STYLE;
    doc.head.appendChild(style);
  }

  function createGlue() {
    var state = {
      vditor: null,
      bridge: null,
      debounceTimer: null,
      // True while a Python-driven setValue() is in flight: the input
      // event(s) it causes must not be pushed back as a user edit.
      echoGuard: false,
      ready: false,
      generation: 0,
      pendingMarkdown: null,
      lastPushedMarkdown: "",
      snapshotToken: 0,
      snapshotInFlight: 0,
      revision: 0,
      documentSessionId: "",
      sessionAwaitingValue: false,
      sessionRestoreToken: 0,
      documentSessions: {},
    };

    function markdownDelta(before, after) {
      before = typeof before === "string" ? before : "";
      after = typeof after === "string" ? after : "";
      var start = 0;
      var shared = Math.min(before.length, after.length);
      while (start < shared && before.charCodeAt(start) === after.charCodeAt(start)) {
        start += 1;
      }
      function isHighSurrogate(code) {
        return code >= 0xD800 && code <= 0xDBFF;
      }
      function isLowSurrogate(code) {
        return code >= 0xDC00 && code <= 0xDFFF;
      }
      function splitsSurrogate(text, offset) {
        return offset > 0 && offset < text.length &&
          isHighSurrogate(text.charCodeAt(offset - 1)) &&
          isLowSurrogate(text.charCodeAt(offset));
      }
      if (splitsSurrogate(before, start) || splitsSurrogate(after, start)) {
        start -= 1;
      }
      var oldEnd = before.length;
      var newEnd = after.length;
      while (oldEnd > start && newEnd > start &&
             before.charCodeAt(oldEnd - 1) === after.charCodeAt(newEnd - 1)) {
        oldEnd -= 1;
        newEnd -= 1;
      }
      if (splitsSurrogate(before, oldEnd)) oldEnd += 1;
      if (splitsSurrogate(after, newEnd)) newEnd += 1;
      return {
        start: start,
        deleteCount: oldEnd - start,
        inserted: after.slice(start, newEnd),
      };
    }

    function pushMarkdown(markdown) {
      var delta = markdownDelta(state.lastPushedMarkdown, markdown);
      var baseRevision = state.revision;
      if (typeof state.bridge.contentDelta === "function") {
        state.bridge.contentDelta(
          state.generation,
          delta.start,
          delta.deleteCount,
          delta.inserted,
          baseRevision,
          markdown.length
        );
      } else {
        state.bridge.contentChanged(
          markdown,
          state.generation,
          delta.start,
          delta.deleteCount,
          delta.inserted,
          baseRevision,
          markdown.length
        );
      }
      state.lastPushedMarkdown = markdown;
      state.revision = baseRevision + 1;
    }

    function pushContent() {
      state.debounceTimer = null;
      if (state.echoGuard || !state.vditor || !state.bridge) return;
      if (state.snapshotInFlight) {
        state.debounceTimer = win.setTimeout(pushContent, DEBOUNCE_MS);
        return;
      }
      var markdown = typeof state.pendingMarkdown === "string"
        ? state.pendingMarkdown : state.vditor.getValue();
      state.pendingMarkdown = null;
      pushMarkdown(markdown);
    }

    // v4: custom toolbar button -> bridge.toolbarAction(name). window.py
    // dispatches "save"/"export_pdf"/"export_docx"/"export_html"/
    // "insert_image"/"toggle_theme" to the existing Qt-side handlers.
    function toolbarAction(name) {
      if (state.bridge && typeof state.bridge.toolbarAction === "function") {
        state.bridge.toolbarAction(name);
      }
    }

    function requestSave() {
      if (!state.vditor || !state.bridge) return;
      if (state.debounceTimer) {
        win.clearTimeout(state.debounceTimer);
        state.debounceTimer = null;
      }
      var markdown = typeof state.pendingMarkdown === "string"
        ? state.pendingMarkdown : state.vditor.getValue();
      state.pendingMarkdown = null;
      var delta = markdownDelta(state.lastPushedMarkdown, markdown);
      var baseRevision = state.revision;
      if (typeof state.bridge.saveWithContent === "function") {
        state.bridge.saveWithContent(
          markdown,
          state.generation,
          delta.start,
          delta.deleteCount,
          delta.inserted,
          baseRevision,
          markdown.length
        );
      } else {
        state.bridge.contentChanged(
          markdown,
          state.generation,
          delta.start,
          delta.deleteCount,
          delta.inserted,
          baseRevision,
          markdown.length
        );
        if (typeof state.bridge.saveRequested === "function") {
          state.bridge.saveRequested();
        }
      }
      state.lastPushedMarkdown = markdown;
      state.revision = baseRevision + 1;
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

    // Office Viewer 4.2 toolbar order. The underlying Vditor fork supplies
    // the polished Codicon set, colour pickers, themes, find/replace,
    // settings, code-block chrome and block handles. Host-specific actions
    // stay as tiny bridge adapters into the existing Qt workflows.
    function buildToolbar() {
      return [
        "outline",
        { name: "markmap", tip: "筆記關聯圖", icon: codicon("type-hierarchy"),
          click: function () { toolbarAction("open_graph"); } },
        "|",
        { name: "edit-in-source", tip: "切換原始碼編輯 (Ctrl+Shift+W)",
          className: "right", icon: codicon("vscode"),
          click: function () { toolbarAction("toggle_source"); } },
        { name: "save", tip: "儲存 (Ctrl+S)", className: "right",
          icon: codicon("save"), click: requestSave },
        "|",
        "headings", "bold", "italic", "strike", "link",
        "|",
        "font-color", "background-color",
        { name: "export", tip: "匯出…", icon: codicon("arrow-down"),
          click: function () { toolbarAction("show_export_menu"); } },
        { name: "insert-image", tip: "插入圖片…", icon: codicon("mail"),
          click: function () { toolbarAction("insert_image"); } },
        "|",
        "editor-theme", "editor-theme-toggle",
        "|",
        "list", "ordered-list", "check", "table",
        "|",
        "quote", "code",
        { name: "insert-attachment", tip: "加入附件…", icon: codicon("cloud-upload"),
          click: function () { toolbarAction("insert_attachment"); } },
        "|",
        "undo", "redo",
        "|",
        "find", "ai-settings", "settings",
      ];
    }

    function onInput(markdown) {
      if (state.echoGuard) {
        // Consume exactly the one echo this guard was raised for; a real
        // keystroke landing right after must debounce normally again.
        state.echoGuard = false;
        return;
      }
      state.pendingMarkdown = typeof markdown === "string" ? markdown : null;
      if (state.debounceTimer) win.clearTimeout(state.debounceTimer);
      state.debounceTimer = win.setTimeout(pushContent, DEBOUNCE_MS);
    }

    // Vditor's autocomplete/emoji/heading hint popups use the ".vditor-hint"
    // class and toggle CSS display to show/hide -- see index.min.js. Find and
    // those editing hints retain their exact-core Escape handling.
    function isHintPanelOpen() {
      if (!doc || !doc.querySelectorAll) return false;
      var findBar = doc.querySelector(".vditor-find-bar");
      if (findBar && (!findBar.style || findBar.style.display !== "none")) {
        return true;
      }
      var hints = doc.querySelectorAll(".vditor-hint");
      for (var i = 0; i < hints.length; i++) {
        var el = hints[i];
        if (el && el.style && el.style.display && el.style.display !== "none") {
          return true;
        }
      }
      return false;
    }

    function clickControl(control) {
      if (!control || typeof control.click !== "function") return false;
      control.click();
      return true;
    }

    // Close one exact Office Viewer layer. These selectors intentionally
    // mirror the 4.2 fork's real DOM instead of guessing from generic modal
    // geometry. Triggering its own button keeps the fork's internal open-state
    // bookkeeping synchronized with the visible DOM.
    function closeExactOfficeOverlay() {
      if (!doc || !doc.querySelector) return false;

      var aiDialog = doc.querySelector(
        ".vditor-ai-dialog-overlay:not([hidden])"
      );
      if (aiDialog) {
        var aiClose = aiDialog.querySelector && aiDialog.querySelector(
          ".vditor-ai-dialog__close, .vditor-ai-dialog__btn--cancel"
        );
        if (clickControl(aiClose)) return true;
      }

      var aiReview = doc.querySelector(".vditor-ai-review");
      if (aiReview) {
        var reviewClose = aiReview.querySelector && aiReview.querySelector(
          ".vditor-ai-review__close, [data-action='reject']"
        );
        if (clickControl(reviewClose)) return true;
      }

      var aiOverlay = doc.querySelector(".vditor-ai-overlay:not([hidden])");
      if (aiOverlay) {
        var overlayClose = aiOverlay.querySelector && aiOverlay.querySelector(
          "[data-action='close'], .vditor-ai-dialog__close"
        );
        if (clickControl(overlayClose)) return true;
      }

      var floatingMenu = doc.querySelector(
        ".vditor-settings-panel__floating-menu:not([hidden])"
      );
      if (floatingMenu) {
        var openDropdown = doc.querySelector(
          ".vditor-settings-panel__dropdown-trigger--open"
        );
        if (clickControl(openDropdown)) return true;
        floatingMenu.hidden = true;
        return true;
      }

      var themePicker = doc.querySelector(".vditor-theme-picker-popover");
      if (themePicker && themePicker.style && themePicker.style.display === "block") {
        var themeDropdown = doc.querySelector(
          ".vditor-settings-panel__dropdown-trigger--open"
        );
        if (clickControl(themeDropdown)) return true;
        themePicker.style.display = "none";
        return true;
      }

      var exactTriggers = [
        [".vditor-cm-chrome__lang--open", ".vditor-cm-chrome__lang-trigger"],
        [".vditor-cm-chrome__theme--open", ".vditor-cm-chrome__theme-trigger"],
        [
          ".vditor-mermaid-chrome__theme--open",
          ".vditor-mermaid-chrome__theme-trigger",
        ],
      ];
      for (var i = 0; i < exactTriggers.length; i++) {
        var openWrap = doc.querySelector(exactTriggers[i][0]);
        if (openWrap && openWrap.querySelector &&
            clickControl(openWrap.querySelector(exactTriggers[i][1]))) {
          return true;
        }
      }

      var toolbarTypes = ["editor-theme", "settings", "ai-settings"];
      for (var j = 0; j < toolbarTypes.length; j++) {
        var button = doc.querySelector(
          ".vditor-toolbar button[data-type='" + toolbarTypes[j] + "']"
        );
        var item = button && button.parentNode;
        if (!item || !item.children) continue;
        for (var k = 0; k < item.children.length; k++) {
          var panel = item.children[k];
          if (panel && panel.classList && panel.classList.contains("vditor-hint") &&
              panel.style && panel.style.display === "block") {
            if (clickControl(button)) return true;
          }
        }
      }
      return false;
    }

    function consumeEscape(event) {
      if (event && typeof event.preventDefault === "function") event.preventDefault();
      if (event && typeof event.stopImmediatePropagation === "function") {
        event.stopImmediatePropagation();
      } else if (event && typeof event.stopPropagation === "function") {
        event.stopPropagation();
      }
    }

    function onKeydown(event) {
      var ctrl = event.ctrlKey || event.metaKey;
      if (ctrl) {
        var key = String(event.key || "").toLowerCase();
        if (key !== "s") return;
        event.preventDefault();
        // Flush any debounced edit *before* asking Python to save, in that
        // Carry the live value and its delta in this save call, so Ctrl+S can
        // never write content one keystroke behind the debounce window.
        requestSave();
        return;
      }
      if (event.key !== "Escape" && event.key !== "Esc") return;
      if (closeExactOfficeOverlay()) {
        consumeEscape(event);
        return;
      }
      // Find/autocomplete hints already own Escape in the exact core. Do not
      // consume it here; just ensure it can never escape to the Qt host.
      if (isHintPanelOpen()) return;
      // A clean Escape deliberately remains a no-op at the host boundary.
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
      // Office Viewer's Vditor 4.x owns undo-aware nested block handles.
      // Never layer the legacy fallback handles on top of those.
      if (root.querySelector && root.querySelector(".vditor-block-handle")) {
        state.handlesInstalled = true;
        return;
      }
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
      // Install before the exact core creates/measures its toolbar so the
      // first painted frame is already single-row (no two-row flash).
      injectOfficeLayoutStyle();
      state.vditor = new VditorCtor(options.elementId || "vditor", {
        mode: "wysiwyg",
        lang: options.lang || "zh_TW",
        cdn: options.cdn || "",
        height: "100%",
        tab: "\t",
        editorTheme: options.editorTheme || "Auto",
        codeMirrorTheme: options.codeMirrorTheme || "Auto",
        mermaidTheme: options.mermaidTheme || "Auto",
        // The exact core restores its persisted `outlineWidth` before this
        // option, so 280px is only the first-use default. Its native resize
        // implementation continues to clamp and persist within 120-480px.
        outline: { position: "left", width: 280 },
        // Autosave / local cache must stay off: Ctrl+S -> the window's
        // atomic-write pipeline is the only path that ever touches disk.
        cache: {
          enable: false,
          id: options.documentSessionId || "markdown-viewer:untitled",
          // The Office fork's VS Code focus adapter keeps a private WeakMap
          // keyed by the reused Vditor instance. Changing cache.id at runtime
          // cannot clear that map, so it can restore another file's caret.
          // Glue owns per-document focus below; keep the core's browser mode.
          focusHost: "browser",
        },
        toolbarConfig: { pin: true },
        toolbar: options.toolbar || buildToolbar(),
        preview: {
          math: { engine: "KaTeX" },
          // mermaid/echarts renderers are not bundled offline; render the
          // fenced block as plain code rather than erroring out.
          render: { mermaid: false, echarts: false },
        },
        input: onInput,
        after: function () {
          state.ready = true;
          var inner = state.vditor && state.vditor.vditor;
          var cache = inner && inner.options && inner.options.cache;
          if (!cache && state.vditor && state.vditor.options) {
            cache = state.vditor.options.cache;
          }
          state.documentSessionId = cache && cache.id ? cache.id : "";
          if (options.value) {
            setValue(
              options.value,
              options.generation || 0,
              options.documentBaseUrl,
              options.documentSessionId
            );
          }
          installBlockHandles();
          injectOfficeLayoutStyle();
          syncToolbarTitles();
          restoreDocumentSession();
          if (state.bridge && typeof state.bridge.ready === "function") {
            state.bridge.ready();
          }
        },
      });
      doc.addEventListener("keydown", onKeydown, true);
      doc.addEventListener("contextmenu", onContextMenu, true);
      if (win && typeof win.addEventListener === "function") {
        win.addEventListener("blur", persistDocumentSession);
        win.addEventListener("pagehide", persistDocumentSession);
      }
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

    var DOCUMENT_SESSION_PREFIX = "markdown-viewer-document-session:";

    function documentSessionStorage() {
      try {
        return win && win.localStorage ? win.localStorage : null;
      } catch (_error) {
        return null;
      }
    }

    function nodePath(root, node) {
      if (!root || !node || !(node === root || root.contains(node))) return null;
      var path = [];
      while (node && node !== root) {
        var parent = node.parentNode;
        if (!parent || !parent.childNodes) return null;
        var index = Array.prototype.indexOf.call(parent.childNodes, node);
        if (index < 0) return null;
        path.unshift(index);
        node = parent;
      }
      return node === root ? path : null;
    }

    function nodeAtPath(root, path) {
      if (!root || !Array.isArray(path)) return null;
      var node = root;
      for (var i = 0; i < path.length; i++) {
        if (!node.childNodes || !node.childNodes[path[i]]) return null;
        node = node.childNodes[path[i]];
      }
      return node;
    }

    function clampNodeOffset(node, offset) {
      var maximum = node && node.nodeType === 3
        ? String(node.textContent || "").length
        : (node && node.childNodes ? node.childNodes.length : 0);
      return Math.max(0, Math.min(Number(offset) || 0, maximum));
    }

    function textOffsetForPoint(root, node, offset) {
      if (!doc || typeof doc.createRange !== "function") return null;
      try {
        var range = doc.createRange();
        range.selectNodeContents(root);
        range.setEnd(node, clampNodeOffset(node, offset));
        return range.toString().length;
      } catch (_error) {
        return null;
      }
    }

    function pointAtTextOffset(root, target) {
      if (!doc || typeof doc.createTreeWalker !== "function") return null;
      var showText = win.NodeFilter ? win.NodeFilter.SHOW_TEXT : 4;
      var walker = doc.createTreeWalker(root, showText);
      var remaining = Math.max(0, Number(target) || 0);
      var last = null;
      while (walker.nextNode()) {
        last = walker.currentNode;
        var length = String(last.textContent || "").length;
        if (remaining <= length) return { node: last, offset: remaining };
        remaining -= length;
      }
      return last
        ? { node: last, offset: String(last.textContent || "").length }
        : { node: root, offset: 0 };
    }

    function closestElement(node, selector) {
      var element = node && node.nodeType === 1
        ? node : (node && node.parentElement);
      return element && typeof element.closest === "function"
        ? element.closest(selector) : null;
    }

    function codeMirrorView(content) {
      var tile = content && content.cmTile;
      var view = tile && tile.view;
      return view && view.state && view.state.selection ? view : null;
    }

    function selectedCodeMirror(surface, selection) {
      var candidates = [doc && doc.activeElement];
      if (selection && selection.anchorNode) candidates.push(selection.anchorNode);
      for (var i = 0; i < candidates.length; i++) {
        var content = closestElement(candidates[i], ".cm-content");
        if (!content || !surface.contains(content)) continue;
        var block = closestElement(content, "[data-type='code-block']");
        var view = codeMirrorView(content);
        if (!block || !view) continue;
        var blocks = surface.querySelectorAll("[data-type='code-block']");
        var blockIndex = Array.prototype.indexOf.call(blocks, block);
        if (blockIndex >= 0) {
          return { content: content, view: view, blockIndex: blockIndex };
        }
      }
      return null;
    }

    function pointContext(node, offset) {
      if (!node || node.nodeType !== 3) return null;
      var text = String(node.textContent || "");
      var caret = clampNodeOffset(node, offset);
      return {
        before: text.slice(Math.max(0, caret - 32), caret),
        after: text.slice(caret, caret + 32),
      };
    }

    function closestContextOffset(text, before, after, expected) {
      before = typeof before === "string" ? before : "";
      after = typeof after === "string" ? after : "";
      var needle = before + after;
      if (!needle) return null;
      var best = null;
      var from = 0;
      while (from <= text.length) {
        var found = text.indexOf(needle, from);
        if (found < 0) break;
        var candidate = found + before.length;
        if (best === null ||
            Math.abs(candidate - expected) < Math.abs(best - expected)) {
          best = candidate;
        }
        from = found + 1;
      }
      return best;
    }

    function resolveStoredPoint(root, path, offset, textOffset, before, after) {
      // Office's focus state is path-first. That matters for repeated text
      // and for code blocks whose hidden/rendered DOM duplicates content.
      var node = nodeAtPath(root, path);
      if (node) {
        var restoredOffset = clampNodeOffset(node, offset);
        if (node.nodeType === 3) {
          var contextual = closestContextOffset(
            String(node.textContent || ""), before, after, restoredOffset
          );
          if (contextual !== null) restoredOffset = contextual;
        }
        return { node: node, offset: restoredOffset };
      }
      return typeof textOffset === "number"
        ? pointAtTextOffset(root, textOffset) : null;
    }

    function persistDocumentSession() {
      if (!state.ready || !state.documentSessionId || !state.vditor) return;
      var inner = state.vditor.vditor;
      var mode = inner && inner.currentMode;
      var surface = mode && inner[mode] && inner[mode].element;
      if (!surface) return;

      var session = {
        mode: mode,
        scrollTop: Number(surface.scrollTop) || 0,
      };
      var selection = win.getSelection && win.getSelection();
      var range = selection && selection.rangeCount
        ? selection.getRangeAt(0) : null;
      var cm = selectedCodeMirror(surface, selection);
      if (cm) {
        var main = cm.view.state.selection.main;
        session.type = "cm";
        session.blockIndex = cm.blockIndex;
        session.anchor = Number(main.anchor) || 0;
        session.head = Number(main.head) || 0;
      }
      if (range && surface.contains(range.startContainer) &&
          surface.contains(range.endContainer) && !cm) {
        var startPath = nodePath(surface, range.startContainer);
        var endPath = nodePath(surface, range.endContainer);
        if (startPath && endPath) {
          session.type = "dom";
          session.startPath = startPath;
          session.startOffset = range.startOffset;
          session.endPath = endPath;
          session.endOffset = range.endOffset;
          session.startTextOffset = textOffsetForPoint(
            surface, range.startContainer, range.startOffset
          );
          session.endTextOffset = textOffsetForPoint(
            surface, range.endContainer, range.endOffset
          );
          var startContext = pointContext(
            range.startContainer, range.startOffset
          );
          var endContext = pointContext(range.endContainer, range.endOffset);
          if (startContext) {
            session.startBefore = startContext.before;
            session.startAfter = startContext.after;
          }
          if (endContext) {
            session.endBefore = endContext.before;
            session.endAfter = endContext.after;
          }
        }
      }
      state.documentSessions[state.documentSessionId] = session;
      var storage = documentSessionStorage();
      if (storage) {
        try {
          storage.setItem(
            DOCUMENT_SESSION_PREFIX + state.documentSessionId,
            JSON.stringify(session)
          );
        } catch (_error) {
          // Private/locked storage must never affect the editor or saving.
        }
      }
    }

    function readDocumentSession(sessionId) {
      var session = state.documentSessions[sessionId];
      if (session) return session;
      var storage = documentSessionStorage();
      if (!storage) return null;
      try {
        var raw = storage.getItem(DOCUMENT_SESSION_PREFIX + sessionId);
        session = raw ? JSON.parse(raw) : null;
      } catch (_error) {
        session = null;
      }
      if (session && typeof session === "object") {
        state.documentSessions[sessionId] = session;
        return session;
      }
      return null;
    }

    function setDocumentSession(sessionId) {
      var value = typeof sessionId === "string" && sessionId
        ? sessionId : "markdown-viewer:untitled";
      var inner = state.vditor && state.vditor.vditor;
      if (!inner || !inner.options) {
        state.documentSessionId = value;
        return;
      }
      inner.options.cache = inner.options.cache || {};
      var changed = state.documentSessionId !== value ||
        inner.options.cache.id !== value;
      if (!changed) return;
      persistDocumentSession();
      inner.options.cache.enable = false;
      inner.options.cache.id = value;
      inner.options.cache.focusHost = "browser";
      state.documentSessionId = value;
      state.sessionAwaitingValue = true;
      state.sessionRestoreToken += 1;
    }

    function restoreDocumentSession() {
      if (!state.ready || !state.vditor || !state.documentSessionId) return;
      var sessionId = state.documentSessionId;
      var generation = state.generation;
      var restoreToken = ++state.sessionRestoreToken;
      var session = readDocumentSession(sessionId);
      if (!session) return;

      function stillCurrent() {
        return state.documentSessionId === sessionId &&
          state.generation === generation &&
          state.sessionRestoreToken === restoreToken &&
          !!state.vditor;
      }

      function applyScroll(surface) {
        surface.scrollTop = Math.max(0, Number(session.scrollTop) || 0);
      }

      function restoreCodeMirror(surface, attempt) {
        if (!stillCurrent()) return;
        // Off-screen code blocks are virtualized. Put the block in the
        // viewport first so the core can mount its CodeMirror view.
        applyScroll(surface);
        var blocks = surface.querySelectorAll("[data-type='code-block']");
        var block = blocks[Math.max(0, Number(session.blockIndex) || 0)];
        var content = block && block.querySelector(".cm-content");
        var view = codeMirrorView(content);
        if (view) {
          try {
            var length = view.state.doc.length;
            var anchor = Math.max(0, Math.min(Number(session.anchor) || 0, length));
            var head = Math.max(0, Math.min(Number(session.head) || 0, length));
            view.dispatch({
              selection: { anchor: anchor, head: head },
              scrollIntoView: false,
            });
            view.focus();
          } catch (_error) {
            // A pinned-bundle capability mismatch must not break loading.
          }
          applyScroll(surface);
          // CodeMirror and Chromium may scroll the newly focused caret on
          // the next layout frame even though dispatch used scrollIntoView:
          // false. Reapply the document's root scroll after that focus/layout
          // sequence settles, with the same stale-session guard.
          if (win && typeof win.requestAnimationFrame === "function") {
            win.requestAnimationFrame(function () {
              if (!stillCurrent()) return;
              applyScroll(surface);
              win.requestAnimationFrame(function () {
                if (stillCurrent()) applyScroll(surface);
              });
            });
          }
          return;
        }
        if (attempt < 72 && win &&
            typeof win.requestAnimationFrame === "function") {
          win.requestAnimationFrame(function () {
            restoreCodeMirror(surface, attempt + 1);
          });
        }
      }

      function applySession() {
        if (!stillCurrent()) return;
        var inner = state.vditor.vditor;
        var mode = inner && inner.currentMode;
        var surface = mode && inner[mode] && inner[mode].element;
        if (!surface || session.mode !== mode) return;

        if (session.type === "cm") {
          restoreCodeMirror(surface, 0);
          return;
        }
        var range = null;
        var startPoint = resolveStoredPoint(
          surface,
          session.startPath,
          session.startOffset,
          session.startTextOffset,
          session.startBefore,
          session.startAfter
        );
        var endPoint = resolveStoredPoint(
          surface,
          session.endPath,
          session.endOffset,
          session.endTextOffset,
          session.endBefore,
          session.endAfter
        );
        var startNode = startPoint && startPoint.node;
        var endNode = endPoint && endPoint.node;
        if (startNode && endNode && doc && typeof doc.createRange === "function") {
          try {
            range = doc.createRange();
            range.setStart(startNode, clampNodeOffset(startNode, startPoint.offset));
            range.setEnd(endNode, clampNodeOffset(endNode, endPoint.offset));
            try {
              surface.focus({ preventScroll: true });
            } catch (_error) {
              if (typeof surface.focus === "function") surface.focus();
            }
            var selection = win.getSelection && win.getSelection();
            if (selection) {
              selection.removeAllRanges();
              selection.addRange(range);
            }
            inner[mode].range = range.cloneRange ? range.cloneRange() : range;
          } catch (_error) {
            range = null;
          }
        }
        applyScroll(surface);
      }

      // setValue rebuilds the WYSIWYG DOM synchronously, while code/math
      // decorators settle on animation frames. Two frames matches Office
      // Viewer's own restore timing and avoids a visible caret jump.
      if (win && typeof win.requestAnimationFrame === "function") {
        win.requestAnimationFrame(function () {
          win.requestAnimationFrame(applySession);
        });
      } else {
        win.setTimeout(applySession, 0);
      }
    }

    function setDocumentBase(baseUrl, sessionId) {
      var value = typeof baseUrl === "string" ? baseUrl : "";
      state.documentBaseUrl = value;
      if (typeof sessionId === "string") setDocumentSession(sessionId);
      var inner = state.vditor && state.vditor.vditor;
      if (!inner) return;
      if (inner.options && inner.options.preview && inner.options.preview.markdown) {
        inner.options.preview.markdown.linkBase = value;
      }
      if (inner.lute && typeof inner.lute.SetLinkBase === "function") {
        inner.lute.SetLinkBase(value);
      }
    }

    function setValue(text, generation, documentBaseUrl, documentSessionId) {
      if (!state.vditor) return;
      if (state.debounceTimer) {
        win.clearTimeout(state.debounceTimer);
        state.debounceTimer = null;
      }
      state.pendingMarkdown = null;
      state.snapshotInFlight = 0;
      state.sessionRestoreToken += 1;
      var clearUndoStack = state.sessionAwaitingValue ||
        state.documentSessionId !== documentSessionId;
      if (typeof generation === "number") state.generation = generation;
      state.lastPushedMarkdown = text == null ? "" : String(text);
      state.revision = 0;
      if (state.documentSessionId === documentSessionId &&
          !state.sessionAwaitingValue) {
        // A reload of the same file still needs to preserve the current
        // caret before setValue rebuilds the editable DOM.
        persistDocumentSession();
      }
      setDocumentBase(documentBaseUrl, documentSessionId);
      state.sessionAwaitingValue = false;
      state.echoGuard = true;
      state.vditor.setValue(text == null ? "" : text, clearUndoStack);
      // Vditor's setValue does not reliably fire `input` synchronously (or
      // at all, for an unchanged value). Clear a guard nothing consumed on
      // the next tick so it cannot suppress the user's next real edit.
      win.setTimeout(function () {
        state.echoGuard = false;
        restoreDocumentSession();
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

    function takeSnapshot() {
      // Snapshot reads must not consume the normal debounced push. If the
      // renderer times out or a transition becomes stale, that push is still
      // needed for recovery and dirty-state tracking.
      return state.vditor ? state.vditor.getValue() : null;
    }

    function takeSnapshotEnvelope() {
      var markdown = takeSnapshot();
      if (typeof markdown !== "string") return null;
      var delta = markdownDelta(state.lastPushedMarkdown, markdown);
      state.snapshotToken += 1;
      state.snapshotInFlight = state.snapshotToken;
      return JSON.stringify({
        markdown: markdown,
        generation: state.generation,
        token: state.snapshotToken,
        baseRevision: state.revision,
        revision: state.revision + 1,
        length: markdown.length,
        start: delta.start,
        deleteCount: delta.deleteCount,
        inserted: delta.inserted,
      });
    }

    function acknowledgeMarkdown(markdown, generation, token, revision) {
      if (typeof generation === "number" && generation !== state.generation) return false;
      if (typeof token === "number" && token !== state.snapshotInFlight) return false;
      if (typeof markdown === "string") state.lastPushedMarkdown = markdown;
      if (typeof revision === "number") state.revision = revision;
      state.snapshotInFlight = 0;
      if (state.pendingMarkdown === markdown) {
        state.pendingMarkdown = null;
        if (state.debounceTimer) {
          win.clearTimeout(state.debounceTimer);
          state.debounceTimer = null;
        }
      } else if (typeof state.pendingMarkdown === "string" && !state.debounceTimer) {
        state.debounceTimer = win.setTimeout(pushContent, 0);
      }
      return true;
    }

    function cancelSnapshot(token) {
      if (typeof token === "number" && token && token !== state.snapshotInFlight) return;
      state.snapshotInFlight = 0;
      if (typeof state.pendingMarkdown === "string" && !state.debounceTimer) {
        state.debounceTimer = win.setTimeout(pushContent, 0);
      }
    }

    function ensureInsertionCaret() {
      var inner = state.vditor && state.vditor.vditor;
      var mode = inner && inner.currentMode;
      var surface = mode && inner[mode] && inner[mode].element;
      if (!surface || !doc || !doc.createRange || !win.getSelection) return;

      function isUsable(range) {
        if (!range || !range.startContainer ||
            !(range.startContainer === surface || surface.contains(range.startContainer))) {
          return false;
        }
        var node = range.startContainer.nodeType === 1
          ? range.startContainer : range.startContainer.parentElement;
        return !(node && node.closest && node.closest(".vditor-editor-boundary"));
      }

      var selection = win.getSelection();
      var range = selection && selection.rangeCount ? selection.getRangeAt(0) : null;
      if (!isUsable(range)) {
        range = inner[mode].range;
      }
      if (!isUsable(range)) {
        // A programmatic setValue() leaves the fork's cached range on its
        // leading zero-width boundary sentinel. Inserting there renders the
        // image visually, but DOM -> Markdown intentionally ignores it. Give
        // host-driven inserts a real paragraph at the document end instead.
        var paragraph = doc.createElement("p");
        paragraph.setAttribute("data-block", "0");
        paragraph.appendChild(doc.createElement("br"));
        var trailingBoundary = surface.querySelector(
          ":scope > .vditor-editor-boundary:last-child"
        );
        surface.insertBefore(paragraph, trailingBoundary || null);
        range = doc.createRange();
        range.setStart(paragraph, 0);
        range.collapse(true);
      }
      if (selection) {
        selection.removeAllRanges();
        selection.addRange(range);
      }
      inner[mode].range = range;
    }

    // v4: insert a Markdown snippet (image/attachment link) at the caret,
    // used by window.py after a file picker + image_paste.py import.
    function insertValue(text) {
      if (!state.vditor) return;
      var value = text == null ? "" : text;
      if (typeof state.vditor.insertMarkdown === "function") {
        ensureInsertionCaret();
        state.vditor.insertMarkdown(value);
      } else if (typeof state.vditor.insertValue === "function") {
        state.vditor.insertValue(value);
      }
    }

    function markSaved(markdown) {
      if (state.vditor && typeof state.vditor.markSaved === "function") {
        state.vditor.markSaved(typeof markdown === "string" ? markdown : undefined);
      }
    }

    return {
      boot: boot,
      setValue: setValue,
      setDocumentBase: setDocumentBase,
      getValue: getValue,
      insertValue: insertValue,
      flushPending: flushPending,
      takeSnapshot: takeSnapshot,
      takeSnapshotEnvelope: takeSnapshotEnvelope,
      acknowledgeMarkdown: acknowledgeMarkdown,
      cancelSnapshot: cancelSnapshot,
      markSaved: markSaved,
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
