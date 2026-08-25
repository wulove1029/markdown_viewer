/* In-place editing of a rendered block.
 *
 * Triple-clicking a top-level block in the preview swaps it for a textarea
 * holding the raw Markdown behind it (located through the data-src-start /
 * data-src-end attributes md_converter stamps on every block). Ctrl+Enter or a
 * click outside commits; Esc restores the rendering and touches nothing.
 *
 * Only ever active in preview mode: Python both gates it through
 * __inlineEdit.setEnabled and refuses every bridge call while the editor owns
 * the buffer, so a stale page can never write behind the editor's back.
 */
(function () {
  var bridge = null;
  var enabled = false;
  // { block, blockDisplay, wrapper, textarea, start, end, original, sig,
  //   mode: "raw" | "table", editor: __tableEdit handle | null,
  //   modelJson: the table model as opened (table mode only),
  //   busy: a write is in flight, warned: the stale strip is already up }
  var active = null;
  var styleNode = null;

  var STYLE =
    ".inline-edit { margin: 0 0 14px; }" +
    ".inline-edit textarea {" +
    "  display: block; width: 100%; box-sizing: border-box;" +
    "  min-height: 48px; resize: vertical; overflow: hidden;" +
    '  font-family: "Cascadia Code", "Fira Code", Consolas, monospace;' +
    "  font-size: .92em; line-height: 1.65; color: inherit;" +
    "  background: rgba(128,128,128,.10);" +
    "  border: 1px solid rgba(128,128,128,.45); border-radius: 6px;" +
    "  padding: 8px 10px; outline: none;" +
    "}" +
    ".inline-edit textarea:focus { border-color: rgba(96,165,250,.85); }" +
    ".inline-edit.is-busy textarea { opacity: .6; }" +
    ".inline-edit.is-busy .tedit { opacity: .6; }" +
    ".inline-edit .inline-edit-hint {" +
    "  font-size: .76em; opacity: .6; margin-top: 4px;" +
    "}" +
    // The stale strip has to be impossible to miss: it is the only warning
    // the user gets that the text still on screen was never written.
    ".inline-edit .inline-edit-warn {" +
    "  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;" +
    "  margin: 0 0 8px; padding: 7px 10px; border-radius: 6px;" +
    "  font-size: .82em; line-height: 1.55; color: inherit;" +
    "  background: rgba(217,119,6,.20);" +
    "  border: 1px solid rgba(217,119,6,.85);" +
    "}" +
    ".inline-edit .inline-edit-reload {" +
    "  font: inherit; font-size: .95em; line-height: 1.5; color: inherit;" +
    "  cursor: pointer; background: rgba(128,128,128,.20);" +
    "  border: 1px solid rgba(217,119,6,.85); border-radius: 5px;" +
    "  padding: 2px 9px; white-space: nowrap;" +
    "}" +
    ".inline-edit .inline-edit-reload:hover {" +
    "  background: rgba(128,128,128,.34);" +
    "}";

  /* The triple-click that opens an editor is also a selection, so
     annotations.js has already raised the highlight toolbar over the spot the
     editor is about to occupy. */
  function dropHighlightToolbar() {
    if (window.__annot && window.__annot.hideToolbar) {
      try { window.__annot.hideToolbar(); } catch (e) {}
    }
  }

  function injectStyle() {
    if (styleNode && styleNode.parentNode) return;
    styleNode = document.createElement("style");
    styleNode.textContent = STYLE;
    document.head.appendChild(styleNode);
  }

  function parseReply(raw) {
    try { return JSON.parse(raw || "null"); } catch (e) { return null; }
  }

  function autoSize(ta) {
    ta.style.height = "auto";
    ta.style.height = Math.max(ta.scrollHeight, 48) + "px";
  }

  /* Tell Python whether the preview currently owns unsaved text.
     runJavaScript answers asynchronously, so Python can never *ask*
     isEditing() at the moment it needs the answer (a modal reload prompt is
     about to block the event loop). Pushing the state as it changes gives
     the window a plain synchronous flag to guard on instead. */
  function notifyEditing(flag) {
    if (!bridge || !bridge.setInlineEditing) return;
    try {
      bridge.setInlineEditing(!!flag);
    } catch (e) {
      /* an older bridge without the slot: the guard simply stays off */
    }
  }

  function restore() {
    if (!active) return;
    var current = active;
    active = null;
    if (current.editor) current.editor.destroy();
    if (current.wrapper.parentNode) {
      current.wrapper.parentNode.removeChild(current.wrapper);
    }
    current.block.style.display = current.blockDisplay;
    notifyEditing(false);
  }

  /* A refused write leaves the page holding text that is not in the file.
     Python deliberately does not reload the preview in that case -- that
     would destroy the very edit being rescued -- so the page has to say so
     itself, and offer the reload as an explicit, informed choice. */
  function showStaleWarning(pending) {
    if (!pending.wrapper || pending.warned) return;
    pending.warned = true;
    var bar = document.createElement("div");
    bar.className = "inline-edit-warn";

    var msg = document.createElement("span");
    msg.textContent =
      "⚠ 檔案已在外部變更，這次編輯沒有存進去。" +
      "請先複製你的內容，再重新載入預覽。";
    bar.appendChild(msg);

    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "inline-edit-reload";
    btn.textContent = "重新載入預覽";
    // mousedown would otherwise pull the caret out of the editor, and the
    // document-level mousedown handler would try to commit on the way past.
    btn.addEventListener("mousedown", function (e) { e.preventDefault(); });
    btn.addEventListener("click", function (e) {
      e.preventDefault();
      if (bridge && bridge.inlineEditReload) {
        bridge.inlineEditReload(function () {});
      }
    });
    bar.appendChild(btn);

    pending.wrapper.insertBefore(bar, pending.wrapper.childNodes[0]);
  }

  /* *text* is what the textarea shows; *original* is the optimistic lock's
     baseline -- what the file said for this block when the editor opened.
     They are the same on a fresh open and deliberately differ after a switch
     out of the grid, where the textarea has to show the grid's current
     content while the lock must still compare against the file. */
  function open(block, start, end, text, sig, original) {
    injectStyle();
    dropHighlightToolbar();
    var wrapper = document.createElement("div");
    wrapper.className = "inline-edit";

    var ta = document.createElement("textarea");
    ta.className = "inline-edit-input";
    ta.spellcheck = false;
    ta.value = text;
    ta.addEventListener("input", function () { autoSize(ta); });

    var hint = document.createElement("div");
    hint.className = "inline-edit-hint";
    hint.textContent = "Ctrl+Enter 儲存 · Esc 取消 · Ctrl+V 可貼上剪貼簿圖片";

    wrapper.appendChild(ta);
    wrapper.appendChild(hint);
    block.parentNode.insertBefore(wrapper, block.nextSibling);

    active = {
      block: block,
      blockDisplay: block.style.display,
      wrapper: wrapper,
      textarea: ta,
      start: start,
      end: end,
      original: original === undefined ? text : original,
      sig: sig || "",
      mode: "raw",
      editor: null,
      modelJson: null,
      busy: false,
      warned: false
    };
    block.style.display = "none";
    autoSize(ta);
    ta.focus();
    ta.setSelectionRange(text.length, text.length);
    notifyEditing(true);
  }

  /* Same .inline-edit wrapper as the raw editor, and not by accident: the
     document-level mousedown treats a click outside .inline-edit as a
     click-away commit, and blocked() uses the same class to refuse re-entry.
     Dropping the grid in bare would make its own buttons look like clicks
     outside the editor and commit the table out from under the user. */
  function openTable(block, start, end, text, model, sig) {
    injectStyle();
    dropHighlightToolbar();
    var wrapper = document.createElement("div");
    wrapper.className = "inline-edit";

    var editor = window.__tableEdit.create(model, {
      onCommit: function () { commit(); },
      onCancel: function () { restore(); },
      onToggleRaw: function () { toggleRaw(); }
    });
    wrapper.appendChild(editor.element);
    // No hint line here: table_edit.js prints its own.
    block.parentNode.insertBefore(wrapper, block.nextSibling);

    active = {
      block: block,
      blockDisplay: block.style.display,
      wrapper: wrapper,
      textarea: null,
      start: start,
      end: end,
      original: text,
      sig: sig || "",
      mode: "table",
      editor: editor,
      // Normalized through the editor rather than taken from the reply, so
      // "did the user change anything?" compares like with like.
      modelJson: JSON.stringify(editor.getModel()),
      busy: false,
      warned: false
    };
    block.style.display = "none";
    editor.focus();
    notifyEditing(true);
  }

  /* Grid -> raw textarea, on the editor's "switch to source" button.

     The textarea has to show what the *grid* holds right now, serialized
     back to pipe syntax by Python. Handing it `original` -- the file's own
     text as of the moment the editor opened -- silently threw away
     everything typed into the cells first, and the Ctrl+Enter that followed
     then saw value === original, decided nothing had changed and closed
     without writing: two data-loss bugs stacked onto one button.

     `original` itself deliberately does NOT move. It is the optimistic
     lock's baseline (what the file said for these lines), not "what the
     editor showed"; if it tracked the textarea the lock would end up
     comparing the file against itself and could never catch a foreign
     write. After a toggle the two therefore differ, on purpose. */
  function toggleRaw() {
    if (!active) return;
    var was = active;
    if (was.mode !== "table" || !was.editor ||
        !bridge || !bridge.inlineEditSerializeTable) {
      swapToRaw(was, was.original);
      return;
    }
    bridge.inlineEditSerializeTable(
      JSON.stringify(was.editor.getModel()),
      function (raw) {
        if (active !== was) return;  // torn down while we were waiting
        var res = parseReply(raw);
        // A model Python refuses to serialize is a bug, not a user action;
        // falling back to the file's text keeps the block editable instead
        // of dropping the user into an empty textarea.
        var text = (res && res.ok && typeof res.text === "string")
          ? res.text
          : was.original;
        swapToRaw(was, text);
      }
    );
  }

  /* restore() has to run *before* open(): open() memorises
     block.style.display, so opening while the block is still hidden would
     record "none" and a later Esc would leave the block invisible forever. */
  function swapToRaw(was, text) {
    restore();
    open(was.block, was.start, was.end, text, was.sig, was.original);
  }

  function commit() {
    if (!active) return;
    var pending = active;
    // One write in flight at a time. Two Ctrl+Enters in quick succession
    // used to send the same edit twice, and the second write came back
    // "stale" -- against the file the first write had just produced -- so
    // the app told the user an external program had changed their file when
    // the only writer was the app itself.
    if (pending.busy) return;
    if (pending.mode === "table") {
      commitTable(pending);
      return;
    }
    var value = pending.textarea.value;
    if (value === pending.original || !bridge || !bridge.inlineEditCommit) {
      restore();
      return;
    }
    pending.busy = true;
    pending.textarea.readOnly = true;
    pending.wrapper.className = "inline-edit is-busy";
    bridge.inlineEditCommit(
      pending.start, pending.end, pending.original, value, pending.sig,
      function (raw) {
        if (active !== pending) return;
        var res = parseReply(raw);
        if (res && res.ok === false) {
          // A refused write must never eat what the user typed; Python has
          // already put the reason in the status bar.
          pending.busy = false;
          pending.textarea.readOnly = false;
          pending.wrapper.className = "inline-edit";
          if (res.error === "stale") showStaleWarning(pending);
          pending.textarea.focus();
          return;
        }
        // On success Python re-renders, so this usually never arrives.
        restore();
      }
    );
  }

  function commitTable(pending) {
    var modelJson = JSON.stringify(pending.editor.getModel());
    if (modelJson === pending.modelJson ||
        !bridge || !bridge.inlineEditCommitTable) {
      restore();
      return;
    }
    pending.busy = true;
    pending.wrapper.className = "inline-edit is-busy";
    // The grid's counterpart to textarea.readOnly on the raw path: the model
    // was serialized on the line above, so anything typed into a cell from
    // here on would be dropped without a trace.
    if (pending.editor.setBusy) pending.editor.setBusy(true);
    bridge.inlineEditCommitTable(
      pending.start, pending.end, pending.original, modelJson, pending.sig,
      function (raw) {
        if (active !== pending) return;
        var res = parseReply(raw);
        if (res && res.ok === false) {
          // Same rule as the raw path: a refused write must never throw away
          // what the user built. Keep the grid standing and hand it back;
          // Python has already put the reason in the status bar.
          pending.busy = false;
          pending.wrapper.className = "inline-edit";
          if (pending.editor.setBusy) pending.editor.setBusy(false);
          if (res.error === "stale") showStaleWarning(pending);
          pending.editor.focus();
          return;
        }
        // On success Python re-renders, so this usually never arrives.
        restore();
      }
    );
  }

  function insertAtCursor(ta, text) {
    if (ta.setRangeText) {
      ta.setRangeText(text, ta.selectionStart, ta.selectionEnd, "end");
    } else {
      var from = ta.selectionStart;
      var to = ta.selectionEnd;
      ta.value = ta.value.slice(0, from) + text + ta.value.slice(to);
      ta.selectionStart = ta.selectionEnd = from + text.length;
    }
    ta.focus();
    autoSize(ta);
  }

  /* True when the paste is worth handing to Python instead of the browser.
     An image on the clipboard shows up as an image/* or "Files" type and
     carries no text; an empty DataTransfer is inconclusive, so ask Python
     rather than silently dropping the image. */
  function looksLikeImagePaste(dt) {
    if (!dt) return true;
    var text = "";
    try { text = (dt.getData && dt.getData("text/plain")) || ""; } catch (e) { text = ""; }
    if (text) return false;
    var types = dt.types ? Array.prototype.slice.call(dt.types) : [];
    for (var i = 0; i < types.length; i++) {
      if (types[i].indexOf("image/") === 0 || types[i] === "Files") return true;
    }
    var items = dt.items || [];
    for (var j = 0; j < items.length; j++) {
      if (items[j].kind === "file" &&
          (items[j].type || "").indexOf("image/") === 0) {
        return true;
      }
    }
    return types.length === 0;
  }

  function blocked(target) {
    if (!target || !target.closest) return true;
    // The annotation note editor already owns double-clicks on a highlight.
    if (target.closest("mark.annot")) return true;
    if (target.closest(".inline-edit")) return true;
    if (target.closest(".annot-editor, .annot-card, .annot-menu, .annot-toolbar")) {
      return true;
    }
    var tag = (target.tagName || "").toLowerCase();
    return tag === "input" || tag === "textarea" || tag === "select";
  }

  // Triple-click, not double: a double-click stays free for the browser's
  // native word-select, so quickly selecting text to copy never opens the
  // editor by accident.
  document.addEventListener("click", function (e) {
    if (e.detail !== 3) return;
    if (!enabled || active || !bridge || !bridge.inlineEditFetch) return;
    if (blocked(e.target)) return;
    var block = e.target.closest("[data-src-start]");
    if (!block) return;
    var start = parseInt(block.getAttribute("data-src-start"), 10);
    var end = parseInt(block.getAttribute("data-src-end"), 10);
    if (isNaN(start) || isNaN(end)) return;
    e.preventDefault();
    // The browser already paragraph-selected on the third mousedown; drop
    // that selection so the editor does not open over a highlighted block.
    if (window.getSelection) {
      try { window.getSelection().removeAllRanges(); } catch (err) {}
    }
    bridge.inlineEditFetch(start, end, function (raw) {
      var res = parseReply(raw);
      if (active || !res || !res.ok || typeof res.text !== "string") return;
      if (!block.parentNode) return;  // re-rendered while we were waiting
      var sig = typeof res.sig === "string" ? res.sig : "";
      // The grid only ever opens over a block the renderer really turned
      // into a <table>. Python's reply cannot settle this by itself: it
      // feeds the raw lines to parse_table, which reads "- | a | b |" /
      // "- |---|---|" as a perfectly good pipe table when it is in fact an
      // unordered list, and saving that back as a table destroys the list.
      // The DOM is the only place that knows which block type markdown-it
      // actually produced, so it gets the final say. Everything else --
      // including a page where table_edit.js failed to load -- falls back to
      // the raw textarea, which can never rewrite the block's structure.
      if (res.table && block.tagName === "TABLE" &&
          window.__tableEdit && window.__tableEdit.create) {
        openTable(block, start, end, res.text, res.table, sig);
        return;
      }
      open(block, start, end, res.text, sig);
    });
  });

  // Raw mode only, deliberately: in table mode active.textarea is null and
  // the event target is a grid cell, so this returns immediately and
  // table_edit.js keeps sole ownership of the keyboard inside the grid.
  document.addEventListener("keydown", function (e) {
    if (!active || e.target !== active.textarea) return;
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      restore();
      return;
    }
    if ((e.key === "Enter" || e.key === "Return") && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      e.stopPropagation();
      commit();
    }
  }, true);

  document.addEventListener("mousedown", function (e) {
    if (!active) return;
    if (e.target.closest && e.target.closest(".inline-edit")) return;
    commit();
  });

  document.addEventListener("paste", function (e) {
    if (!active || e.target !== active.textarea) return;
    if (!bridge || !bridge.inlineEditPasteImage) return;
    if (!looksLikeImagePaste(e.clipboardData)) return;
    e.preventDefault();
    var ta = active.textarea;
    bridge.inlineEditPasteImage(function (raw) {
      var res = parseReply(raw);
      if (!res || !res.ok || !res.link) return;
      if (!active || active.textarea !== ta) return;
      insertAtCursor(ta, res.link);
    });
  });

  // v2 "click to edit": a genuine double-click (detail === 2) is free for the
  // browser's native word-select unless the user's preview_double_click
  // preference is "wysiwyg" (app/edit_backend.py), in which case it should
  // jump straight into the WYSIWYG editor -- mirroring VSCode's Office
  // Viewer extension. This never touches the triple-click inline-block-edit
  // above: they are different gestures, so the "inline" preference leaves
  // every bit of v1 behaviour (this whole file) untouched.
  var dblClickMode = "inline";

  document.addEventListener("dblclick", function (e) {
    if (dblClickMode !== "wysiwyg") return;
    if (!bridge || !bridge.requestWysiwygEdit) return;
    if (blocked(e.target)) return;
    var block = e.target.closest("[data-src-start]");
    if (!block) return;
    var start = parseInt(block.getAttribute("data-src-start"), 10);
    bridge.requestWysiwygEdit(isNaN(start) ? 0 : start);
  });

  window.__inlineEdit = {
    setEnabled: function (value) {
      enabled = !!value;
      if (!enabled) restore();
    },
    setDoubleClickMode: function (mode) {
      dblClickMode = mode === "wysiwyg" ? "wysiwyg" : "inline";
    },
    isEditing: function () { return active !== null; }
  };

  window.__inlineEditBoot = function (channelBridge, isEnabled, doubleClickMode) {
    bridge = channelBridge || null;
    enabled = !!isEnabled;
    dblClickMode = doubleClickMode === "wysiwyg" ? "wysiwyg" : "inline";
  };
})();
