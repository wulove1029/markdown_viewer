/* In-place editing of a rendered block.
 *
 * Double-clicking a top-level block in the preview swaps it for a textarea
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
  // { block, blockDisplay, wrapper, textarea, start, end, original }
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
    ".inline-edit .inline-edit-hint {" +
    "  font-size: .76em; opacity: .6; margin-top: 4px;" +
    "}";

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

  function restore() {
    if (!active) return;
    var current = active;
    active = null;
    if (current.wrapper.parentNode) {
      current.wrapper.parentNode.removeChild(current.wrapper);
    }
    current.block.style.display = current.blockDisplay;
  }

  function open(block, start, end, text) {
    injectStyle();
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
      original: text
    };
    block.style.display = "none";
    autoSize(ta);
    ta.focus();
    ta.setSelectionRange(text.length, text.length);
  }

  function commit() {
    if (!active) return;
    var pending = active;
    var value = pending.textarea.value;
    if (value === pending.original || !bridge || !bridge.inlineEditCommit) {
      restore();
      return;
    }
    pending.textarea.readOnly = true;
    pending.wrapper.className = "inline-edit is-busy";
    bridge.inlineEditCommit(
      pending.start, pending.end, pending.original, value,
      function (raw) {
        if (active !== pending) return;
        var res = parseReply(raw);
        if (res && res.ok === false) {
          // A refused write must never eat what the user typed; Python has
          // already put the reason in the status bar.
          pending.textarea.readOnly = false;
          pending.wrapper.className = "inline-edit";
          pending.textarea.focus();
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

  document.addEventListener("dblclick", function (e) {
    if (!enabled || active || !bridge || !bridge.inlineEditFetch) return;
    if (blocked(e.target)) return;
    var block = e.target.closest("[data-src-start]");
    if (!block) return;
    var start = parseInt(block.getAttribute("data-src-start"), 10);
    var end = parseInt(block.getAttribute("data-src-end"), 10);
    if (isNaN(start) || isNaN(end)) return;
    e.preventDefault();
    bridge.inlineEditFetch(start, end, function (raw) {
      var res = parseReply(raw);
      if (active || !res || !res.ok || typeof res.text !== "string") return;
      if (!block.parentNode) return;  // re-rendered while we were waiting
      open(block, start, end, res.text);
    });
  });

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

  window.__inlineEdit = {
    setEnabled: function (value) {
      enabled = !!value;
      if (!enabled) restore();
    },
    isEditing: function () { return active !== null; }
  };

  window.__inlineEditBoot = function (channelBridge, isEnabled) {
    bridge = channelBridge || null;
    enabled = !!isEnabled;
  };
})();
