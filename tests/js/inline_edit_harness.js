/* Drives assets/inline_edit.js against a minimal DOM stub.
 *
 * Not a browser: the stub implements only what inline_edit.js touches, so it
 * verifies the open / cancel / commit / paste state machine and the guards
 * around it, not layout or real clipboard behaviour. Prints "OK" and exits 0
 * when every case passes; prints the failure and exits 1 otherwise.
 *
 * Run directly (node tests/js/inline_edit_harness.js) or via
 * tests/test_inline_edit_js.py.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SCRIPT = path.join(__dirname, "..", "..", "assets", "inline_edit.js");

// ---- minimal DOM ---------------------------------------------------------
function matchesOne(el, selector) {
  selector = selector.trim();
  if (selector.charAt(0) === "[") {
    return Object.prototype.hasOwnProperty.call(
      el.attrs, selector.slice(1, -1)
    );
  }
  const parts = selector.split(".");
  const tag = parts.shift();
  if (tag && el.tagName !== tag.toUpperCase()) return false;
  const classes = String(el.className || "").split(/\s+/);
  return parts.every((c) => classes.indexOf(c) >= 0);
}

function matches(el, selector) {
  return selector.split(",").some((one) => matchesOne(el, one));
}

function El(tag, opts) {
  opts = opts || {};
  const el = {
    tagName: String(tag).toUpperCase(),
    className: opts.className || "",
    attrs: opts.attrs || {},
    // A real CSSStyleDeclaration reports "" for an unset property, and
    // restoring a hidden block depends on that, so the stub must match.
    style: { display: "", height: "" },
    childNodes: [],
    parentNode: null,
    value: "",
    textContent: "",
    spellcheck: true,
    readOnly: false,
    scrollHeight: 60,
    selectionStart: 0,
    selectionEnd: 0,
    listeners: {},
    getAttribute(name) {
      return Object.prototype.hasOwnProperty.call(this.attrs, name)
        ? this.attrs[name]
        : null;
    },
    addEventListener(type, fn) {
      (this.listeners[type] = this.listeners[type] || []).push(fn);
    },
    appendChild(child) {
      child.parentNode = this;
      this.childNodes.push(child);
      return child;
    },
    removeChild(child) {
      const at = this.childNodes.indexOf(child);
      if (at >= 0) this.childNodes.splice(at, 1);
      child.parentNode = null;
      return child;
    },
    insertBefore(node, ref) {
      node.parentNode = this;
      const at = ref ? this.childNodes.indexOf(ref) : -1;
      if (at < 0) this.childNodes.push(node);
      else this.childNodes.splice(at, 0, node);
      return node;
    },
    focus() { el.focused = true; },
    setSelectionRange(from, to) {
      this.selectionStart = from;
      this.selectionEnd = to;
    },
    setRangeText(text, from, to) {
      this.value = this.value.slice(0, from) + text + this.value.slice(to);
      this.selectionStart = this.selectionEnd = from + text.length;
    },
    closest(selector) {
      let node = this;
      while (node) {
        if (matches(node, selector)) return node;
        node = node.parentNode;
      }
      return null;
    }
  };
  Object.defineProperty(el, "nextSibling", {
    get() {
      if (!this.parentNode) return null;
      const at = this.parentNode.childNodes.indexOf(this);
      return this.parentNode.childNodes[at + 1] || null;
    }
  });
  return el;
}

const body = El("body");
const head = El("head");
const document = {
  head: head,
  body: body,
  listeners: {},
  createElement: (tag) => El(tag),
  addEventListener(type, fn) {
    (this.listeners[type] = this.listeners[type] || []).push(fn);
  }
};

function fire(type, event) {
  event.preventDefault = function () { event.defaultPrevented = true; };
  event.stopPropagation = function () { event.propagationStopped = true; };
  (document.listeners[type] || []).forEach((fn) => fn(event));
  return event;
}

// ---- bridge stub ---------------------------------------------------------
const calls = [];
let sourceText = "alpha\nbeta";
let commitReply = { ok: true };
let commitTableReply = { ok: true };
let fetchTable = null;   // attached to the fetch reply when set
let fetchSig = "17:42";  // the file signature the page has to echo back
let pasteReply = { ok: true, link: "![](assets/image-1.png)" };
// null => answer with a deterministic "SERIALIZED <model>"; a dict => that.
let serializeReply = null;
// Every setInlineEditing(flag) Python would have received, in order.
const editingStates = [];

/* Commits normally answer synchronously here, which no real QWebChannel ever
   does. Turning holdReply on parks the callback in `held` instead, so a test
   can look at the page while a write is genuinely in flight. */
let holdReply = false;
let held = null;

function release(reply) {
  const cb = held;
  held = null;
  holdReply = false;
  if (cb) cb(JSON.stringify(reply));
}

const bridge = {
  inlineEditFetch(start, end, cb) {
    calls.push(["fetch", start, end]);
    const reply = { ok: true, text: sourceText, sig: fetchSig };
    if (fetchTable) reply.table = fetchTable;
    cb(JSON.stringify(reply));
  },
  inlineEditCommit(start, end, original, next, sig, cb) {
    calls.push(["commit", start, end, original, next, sig]);
    if (holdReply) { held = cb; return; }
    cb(JSON.stringify(commitReply));
  },
  inlineEditCommitTable(start, end, original, modelJson, sig, cb) {
    calls.push(["commit-table", start, end, original, modelJson, sig]);
    if (holdReply) { held = cb; return; }
    cb(JSON.stringify(commitTableReply));
  },
  inlineEditSerializeTable(modelJson, cb) {
    calls.push(["serialize", modelJson]);
    cb(JSON.stringify(
      serializeReply === null
        ? { ok: true, text: "SERIALIZED " + modelJson }
        : serializeReply
    ));
  },
  inlineEditReload(cb) {
    calls.push(["reload"]);
    cb(JSON.stringify({ ok: true }));
  },
  inlineEditPasteImage(cb) {
    calls.push(["paste"]);
    cb(JSON.stringify(pasteReply));
  },
  setInlineEditing(flag) {
    editingStates.push(!!flag);
  },
  requestWysiwygEdit(startLine) {
    calls.push(["wysiwyg", startLine]);
  }
};

// ---- load the real script ------------------------------------------------
const sandbox = { document: document, console: console, JSON: JSON, Array: Array };
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(SCRIPT, "utf8"), sandbox, { filename: SCRIPT });

// ---- assertions ----------------------------------------------------------
const failures = [];
function check(label, condition) {
  if (!condition) failures.push(label);
}

function makeBlock(start, end, tag) {
  const block = El(tag || "p", {
    attrs: { "data-src-start": String(start), "data-src-end": String(end) }
  });
  body.appendChild(block);
  return block;
}

// What the renderer really produces for a pipe table; inline_edit.js refuses
// to open the grid over anything else (P0-4).
function makeTableBlock(start, end) {
  return makeBlock(start, end, "table");
}

function descendants(node, out) {
  out = out || [];
  (node.childNodes || []).forEach((child) => {
    out.push(child);
    descendants(child, out);
  });
  return out;
}

function firstMatching(root, predicate) {
  return root ? (descendants(root).find(predicate) || null) : null;
}

// The editor wrapper, whatever it holds; textareaOf() assumes raw mode.
function wrapperOf(block) {
  const next = block.nextSibling;
  return next && String(next.className).indexOf("inline-edit") === 0
    ? next
    : null;
}

// Searched rather than indexed: a refused write inserts a warning strip
// ahead of the textarea, and that must not make this helper lie.
function textareaOf(block) {
  return firstMatching(wrapperOf(block), (el) => el.tagName === "TEXTAREA");
}

function warnOf(block) {
  return firstMatching(
    wrapperOf(block), (el) => el.className === "inline-edit-warn"
  );
}

function reloadBtnOf(block) {
  return firstMatching(
    wrapperOf(block), (el) => el.className === "inline-edit-reload"
  );
}

// Disabled: a triple-click must do nothing at all.
sandbox.window.__inlineEditBoot(bridge, false);
let block = makeBlock(2, 3);
fire("click", { target: block, detail: 3 });
check("disabled: no bridge call", calls.length === 0);
check("disabled: no textarea", textareaOf(block) === null);

// Enabled but only double-clicked: selection-for-copy must stay untouched.
sandbox.window.__inlineEdit.setEnabled(true);
fire("click", { target: block, detail: 2 });
check("double-click: no bridge call", calls.length === 0);
check("double-click: no textarea", textareaOf(block) === null);

// Enabled: the block is hidden and replaced by a textarea holding the source.
fire("click", { target: block, detail: 3 });
let ta = textareaOf(block);
check("open: fetch called with the block range",
  JSON.stringify(calls[0]) === JSON.stringify(["fetch", 2, 3]));
check("open: textarea holds the raw source", ta && ta.value === "alpha\nbeta");
check("open: block hidden", block.style.display === "none");

// A second triple-click while editing must not stack editors.
const callsBefore = calls.length;
fire("click", { target: makeBlock(9, 9), detail: 3 });
check("open: no re-entry while editing", calls.length === callsBefore);

// Esc restores the rendering and never reaches the bridge.
fire("keydown", { target: ta, key: "Escape" });
check("cancel: block restored", block.style.display === "");
check("cancel: textarea gone", textareaOf(block) === null);
check("cancel: nothing committed", calls.length === callsBefore);

// Ctrl+Enter commits the edited text.
fire("click", { target: block, detail: 3 });
ta = textareaOf(block);
ta.value = "alpha edited";
fire("keydown", { target: ta, key: "Enter", ctrlKey: true });
const commit = calls[calls.length - 1];
check("commit: bridge got the range, original, new text and signature",
  JSON.stringify(commit) ===
  JSON.stringify(["commit", 2, 3, "alpha\nbeta", "alpha edited", "17:42"]));
check("commit: editor closed", textareaOf(block) === null);

// An unchanged commit is a no-op, not a write.
fire("click", { target: block, detail: 3 });
ta = textareaOf(block);
const beforeNoop = calls.length;
fire("keydown", { target: ta, key: "Enter", ctrlKey: true });
check("commit: unchanged text is not written", calls.length === beforeNoop);
check("commit: unchanged still closes", textareaOf(block) === null);

// Clicking outside commits; clicking inside does not.
fire("click", { target: block, detail: 3 });
ta = textareaOf(block);
ta.value = "changed by click-away";
const insideBefore = calls.length;
fire("mousedown", { target: ta });
check("click inside: still editing", textareaOf(block) !== null);
check("click inside: nothing committed", calls.length === insideBefore);
fire("mousedown", { target: body });
check("click outside: committed",
  calls[calls.length - 1][4] === "changed by click-away");

// Pasting an image goes through Python and lands at the cursor.
fire("click", { target: block, detail: 3 });
ta = textareaOf(block);
ta.value = "before after";
ta.selectionStart = ta.selectionEnd = 7;
let ev = fire("paste", {
  target: ta,
  clipboardData: { types: ["Files"], items: [], getData: () => "" }
});
check("paste image: native paste suppressed", ev.defaultPrevented === true);
check("paste image: link inserted at the cursor",
  ta.value === "before ![](assets/image-1.png)after");

// Pasting text is left to the browser.
const beforeText = calls.length;
ev = fire("paste", {
  target: ta,
  clipboardData: { types: ["text/plain"], items: [], getData: () => "hello" }
});
check("paste text: not intercepted", ev.defaultPrevented !== true);
check("paste text: bridge untouched", calls.length === beforeText);

fire("keydown", { target: ta, key: "Escape" });

// Guards: gestures that belong to other features must pass straight through.
const guarded = calls.length;
const annotated = makeBlock(20, 20);
const mark = El("mark", { className: "annot" });
annotated.appendChild(mark);
fire("click", { target: mark, detail: 3 });
check("guard: annotation triple-click not hijacked", calls.length === guarded);
check("guard: no editor over an annotation", textareaOf(annotated) === null);

const taskList = makeBlock(30, 31);
const checkbox = El("input", { className: "task-list-item-checkbox" });
taskList.appendChild(checkbox);
fire("click", { target: checkbox, detail: 3 });
check("guard: task checkbox not hijacked", calls.length === guarded);

const untagged = El("p");
body.appendChild(untagged);
fire("click", { target: untagged, detail: 3 });
check("guard: block without a source range ignored", calls.length === guarded);

// Disabling mid-edit tears the editor down.
fire("click", { target: block, detail: 3 });
check("disable: editor open before disabling", textareaOf(block) !== null);
sandbox.window.__inlineEdit.setEnabled(false);
check("disable: editor torn down", textareaOf(block) === null);
check("disable: block visible again", block.style.display === "");

// ---- table mode ----------------------------------------------------------
// A stand-in for assets/table_edit.js: the real grid is exercised by
// tests/js/table_edit_harness.js, so what matters here is only the contract
// inline_edit.js relies on (element / getModel / focus / destroy + opts).
let tableCalls = [];
let tableHandle = null;

const fakeTableEdit = {
  create(model, opts) {
    const element = El("div", { className: "tedit" });
    const handle = {
      element: element,
      opts: opts || {},
      model: JSON.parse(JSON.stringify(model)),
      destroyed: 0,
      focused: 0,
      busy: false,
      getModel() { return this.model; },
      focus() { this.focused += 1; },
      setBusy(flag) { this.busy = !!flag; },
      destroy() {
        this.destroyed += 1;
        if (element.parentNode) element.parentNode.removeChild(element);
      }
    };
    tableCalls.push({ model: model, opts: handle.opts });
    tableHandle = handle;
    return handle;
  }
};

const tableModel = {
  headers: ["A", "B"],
  aligns: ["", "center"],
  rows: [["1", "2"]],
  indent: ""
};

sandbox.window.__inlineEdit.setEnabled(true);
sourceText = "| A | B |\n| --- | :-: |\n| 1 | 2 |";
fetchTable = tableModel;

// (f) A table reply with no table_edit.js loaded must still open something.
const noGrid = makeTableBlock(40, 42);
fire("click", { target: noGrid, detail: 3 });
ta = textareaOf(noGrid);
check("table fallback: raw textarea when __tableEdit is missing",
  ta !== null && ta.tagName === "TEXTAREA" && ta.value === sourceText);
fire("keydown", { target: ta, key: "Escape" });

sandbox.window.__tableEdit = fakeTableEdit;

// (a) + (b) A table reply builds the grid inside an .inline-edit wrapper.
tableCalls = [];
const grid = makeTableBlock(50, 52);
fire("click", { target: grid, detail: 3 });
check("table: __tableEdit.create called once", tableCalls.length === 1);
check("table: create got the model from the fetch reply",
  tableCalls.length === 1 &&
  JSON.stringify(tableCalls[0].model) === JSON.stringify(tableModel));
let wrapper = wrapperOf(grid);
check("table: wrapper carries the .inline-edit class", wrapper !== null);
check("table: grid element is the wrapper's only child",
  wrapper !== null && wrapper.childNodes.length === 1 &&
  wrapper.childNodes[0] === tableHandle.element);
check("table: no textarea built",
  wrapper !== null && wrapper.childNodes[0].tagName === "DIV");
check("table: block hidden", grid.style.display === "none");
check("table: grid focused on open",
  tableHandle !== null && tableHandle.focused >= 1);

// (d) An untouched model must never reach Python.
let beforeTable = calls.length;
tableHandle.opts.onCommit();
check("table: unchanged model is not written", calls.length === beforeTable);
check("table: unchanged still closes", wrapperOf(grid) === null);
check("table: unchanged destroyed the grid", tableHandle.destroyed === 1);
check("table: unchanged restored the block", grid.style.display === "");

// (c) An edited model goes to inlineEditCommitTable as getModel() JSON.
fire("click", { target: grid, detail: 3 });
let handle = tableHandle;
handle.model.rows[0][0] = "edited";
handle.opts.onCommit();
let sent = calls[calls.length - 1];
check("table: bridge got the table commit",
  JSON.stringify(sent) === JSON.stringify([
    "commit-table", 50, 52, sourceText, JSON.stringify(handle.model), "17:42"
  ]));
check("table: commit closed the grid", wrapperOf(grid) === null);
check("table: commit destroyed the grid", handle.destroyed === 1);

// A refused write must leave the grid (and the user's edits) standing.
commitTableReply = { ok: false, error: "stale" };
fire("click", { target: grid, detail: 3 });
handle = tableHandle;
handle.model.rows[0][0] = "not lost";
const focusedBefore = handle.focused;
handle.opts.onCommit();
check("table: refused write keeps the grid", wrapperOf(grid) !== null);
check("table: refused write does not destroy", handle.destroyed === 0);
check("table: refused write hands focus back",
  handle.focused === focusedBefore + 1);
check("table: refused write keeps the busy tint off",
  wrapperOf(grid).className === "inline-edit");
commitTableReply = { ok: true };

// (e) Cancel tears the grid down.
handle.opts.onCancel();
check("table: cancel destroyed the grid", handle.destroyed === 1);
check("table: cancel removed the wrapper", wrapperOf(grid) === null);
check("table: cancel restored the block", grid.style.display === "");

// Switching to raw source: the same range, and cancelling afterwards must
// still bring the block back (open() would memorise a hidden block otherwise).
//
// P0-1. The textarea must open on what the GRID holds, serialized back to
// pipe syntax by Python -- not on the file text the block was opened with.
// Handing over the file text silently dropped everything typed into the
// cells, and the Ctrl+Enter that followed then saw value === original and
// closed without writing anything at all.
fire("click", { target: grid, detail: 3 });
handle = tableHandle;
handle.model.rows[0][0] = "typed into a cell";
const liveModel = JSON.stringify(handle.model);
handle.opts.onToggleRaw();
check("toggle raw: grid destroyed", handle.destroyed === 1);
check("toggle raw: Python was asked to serialize the live model",
  JSON.stringify(calls[calls.length - 1]) ===
  JSON.stringify(["serialize", liveModel]));
ta = textareaOf(grid);
check("toggle raw: textarea holds the serialized grid, not the file text",
  ta !== null && ta.tagName === "TEXTAREA" &&
  ta.value === "SERIALIZED " + liveModel);
check("toggle raw: block still hidden", grid.style.display === "none");

// ...and the optimistic lock's baseline stays the FILE's text. If `original`
// followed the textarea, the lock would compare the file against itself and
// could never notice a foreign write.
ta.value = "hand edited after the toggle";
fire("keydown", { target: ta, key: "Enter", ctrlKey: true });
sent = calls[calls.length - 1];
check("toggle raw: the lock still compares against the file's own text",
  sent[0] === "commit" && sent[3] === sourceText &&
  sent[4] === "hand edited after the toggle");
check("toggle raw: the range and signature survive the toggle",
  sent[1] === 50 && sent[2] === 52 && sent[5] === "17:42");

// Esc after a toggle still has to un-hide the block.
fire("click", { target: grid, detail: 3 });
tableHandle.opts.onToggleRaw();
ta = textareaOf(grid);
fire("keydown", { target: ta, key: "Escape" });
check("toggle raw: cancel shows the block again", grid.style.display === "");

// A model Python cannot serialize falls back to the file text rather than
// dropping the user into an empty textarea.
serializeReply = { ok: false, error: "bad-model" };
fire("click", { target: grid, detail: 3 });
tableHandle.opts.onToggleRaw();
ta = textareaOf(grid);
check("toggle raw: a refused serialize falls back to the file text",
  ta !== null && ta.value === sourceText);
fire("keydown", { target: ta, key: "Escape" });
serializeReply = null;

// No serialize slot at all (an older bridge) must degrade the same way.
const savedSerialize = bridge.inlineEditSerializeTable;
delete bridge.inlineEditSerializeTable;
fire("click", { target: grid, detail: 3 });
tableHandle.opts.onToggleRaw();
ta = textareaOf(grid);
check("toggle raw: no serialize slot still opens the file text",
  ta !== null && ta.value === sourceText);
fire("keydown", { target: ta, key: "Escape" });
bridge.inlineEditSerializeTable = savedSerialize;

// (e) setEnabled(false) goes through restore(), so it destroys too.
fire("click", { target: grid, detail: 3 });
handle = tableHandle;
check("table: reopened before disabling", wrapperOf(grid) !== null);
sandbox.window.__inlineEdit.setEnabled(false);
check("table: disabling destroyed the grid", handle.destroyed === 1);
check("table: disabling removed the wrapper", wrapperOf(grid) === null);
check("table: disabling restored the block", grid.style.display === "");

// Clicking away commits the grid, exactly as it does for a textarea.
sandbox.window.__inlineEdit.setEnabled(true);
fire("click", { target: grid, detail: 3 });
handle = tableHandle;
handle.model.rows[0][1] = "click away";
beforeTable = calls.length;
fire("mousedown", { target: handle.element });
check("table: mousedown inside the grid does not commit",
  calls.length === beforeTable && wrapperOf(grid) !== null);
fire("mousedown", { target: body });
sent = calls[calls.length - 1];
check("table: mousedown outside commits the grid",
  sent[0] === "commit-table" && sent[4] === JSON.stringify(handle.model));

// ---- P0-4: only a real <table> may open the grid ------------------------
// "- | a | b |" over "- |---|---|" is an unordered list, and parse_table
// reads it as a perfectly good pipe table. Opening the grid over it and
// saving would rewrite the list as a table and destroy its structure, so the
// DOM -- the only place that knows what markdown-it actually produced -- has
// the final say, whatever Python's text-level guess was.
let beforeListy = tableCalls.length;
const listy = makeBlock(60, 61, "ul");
fire("click", { target: listy, detail: 3 });
check("table-like list: no grid built", tableCalls.length === beforeListy);
check("table-like list: raw textarea instead",
  textareaOf(listy) !== null && textareaOf(listy).value === sourceText);
fire("keydown", { target: textareaOf(listy), key: "Escape" });

// A paragraph whose source happens to parse as a table is no different.
beforeListy = tableCalls.length;
const parag = makeBlock(62, 63, "p");
fire("click", { target: parag, detail: 3 });
check("table-like paragraph: no grid built", tableCalls.length === beforeListy);
check("table-like paragraph: raw textarea instead", textareaOf(parag) !== null);
fire("keydown", { target: textareaOf(parag), key: "Escape" });

// ---- P1-3 / P1-2 / P0-2 on the grid path --------------------------------
holdReply = true;
fire("click", { target: grid, detail: 3 });
handle = tableHandle;
handle.model.rows[0][0] = "in flight";
handle.opts.onCommit();
check("busy: the grid stops taking input while the write is in flight",
  handle.busy === true);
check("busy: the wrapper is tinted",
  wrapperOf(grid).className === "inline-edit is-busy");

// A second Ctrl+Enter (or a click-away landing on top of one) used to send
// the same edit twice; the second write came back "stale" against the file
// the first had just produced and blamed an external program for it.
beforeTable = calls.length;
handle.opts.onCommit();
fire("mousedown", { target: body });
check("busy: a second commit is never sent", calls.length === beforeTable);

release({ ok: false, error: "stale" });
check("stale: the grid is still standing", wrapperOf(grid) !== null);
check("stale: the grid was never destroyed", handle.destroyed === 0);
check("stale: the grid takes input again", handle.busy === false);
check("stale: the busy tint is off",
  wrapperOf(grid).className === "inline-edit");
let warn = warnOf(grid);
check("stale: a warning strip is inserted", warn !== null);
check("stale: the warning says the edit was not saved",
  warn !== null && warn.childNodes[0].textContent.indexOf("沒有存進去") >= 0);
const reloadBtn = reloadBtnOf(grid);
check("stale: the strip offers a reload button", reloadBtn !== null);
const beforeReload = calls.length;
(reloadBtn.listeners.click || []).forEach((fn) => fn({
  preventDefault() {}, stopPropagation() {}
}));
check("stale: the reload button asks Python to re-render",
  JSON.stringify(calls[calls.length - 1]) === JSON.stringify(["reload"]));
check("stale: and it writes nothing on the way",
  calls.length === beforeReload + 1);

// A second refusal must not stack a second strip on top of the first.
commitTableReply = { ok: false, error: "stale" };
handle.model.rows[0][0] = "retried";
handle.opts.onCommit();
check("stale: only ever one warning strip",
  descendants(wrapperOf(grid)).filter(
    (el) => el.className === "inline-edit-warn").length === 1);
commitTableReply = { ok: true };
sandbox.window.__inlineEdit.setEnabled(false);
sandbox.window.__inlineEdit.setEnabled(true);

fetchTable = null;

// ---- the raw path gets the same two guarantees --------------------------
holdReply = true;
fire("click", { target: block, detail: 3 });
ta = textareaOf(block);
ta.value = "raw in flight";
const beforeRaw = calls.length;
fire("keydown", { target: ta, key: "Enter", ctrlKey: true });
check("raw busy: one write sent", calls.length === beforeRaw + 1);
check("raw busy: the textarea is read-only", ta.readOnly === true);
fire("keydown", { target: ta, key: "Enter", ctrlKey: true });
fire("mousedown", { target: body });
check("raw busy: a second commit is never sent", calls.length === beforeRaw + 1);

release({ ok: false, error: "stale" });
check("raw stale: the textarea survives", textareaOf(block) !== null);
check("raw stale: what the user typed survives",
  textareaOf(block).value === "raw in flight");
check("raw stale: typing is possible again", ta.readOnly === false);
warn = warnOf(block);
check("raw stale: a warning strip is inserted", warn !== null);
check("raw stale: the strip offers a reload button",
  reloadBtnOf(block) !== null);
fire("keydown", { target: ta, key: "Escape" });

// ---- P2: Python is told when the preview owns unsaved text --------------
// The window cannot ask -- runJavaScript answers asynchronously and the
// answer is needed before a modal dialog opens -- so the page pushes it.
editingStates.length = 0;
fire("click", { target: block, detail: 3 });
check("state: opening reports true",
  JSON.stringify(editingStates) === JSON.stringify([true]));
fire("keydown", { target: textareaOf(block), key: "Escape" });
check("state: cancelling reports false",
  JSON.stringify(editingStates) === JSON.stringify([true, false]));

editingStates.length = 0;
fetchTable = tableModel;
fire("click", { target: grid, detail: 3 });
check("state: opening the grid reports true",
  JSON.stringify(editingStates) === JSON.stringify([true]));
sandbox.window.__inlineEdit.setEnabled(false);
check("state: tearing the grid down reports false",
  JSON.stringify(editingStates) === JSON.stringify([true, false]));
sandbox.window.__inlineEdit.setEnabled(true);
fetchTable = null;

// ---- v2: preview_double_click routing (assets/inline_edit.js dblclick) ---
sandbox.window.__inlineEdit.setEnabled(true);
calls.length = 0;

// Default boot state ("inline" preference / not yet told otherwise): a
// double-click must stay free for the browser's native word-select, exactly
// like v1.
let dblBlock = makeBlock(5, 6);
fire("dblclick", { target: dblBlock, detail: 2 });
check("dblclick default: no bridge call", calls.length === 0);

// setDoubleClickMode("wysiwyg"): a double-click on a rendered block asks
// Python to jump into WYSIWYG, passing the block's starting source line.
sandbox.window.__inlineEdit.setDoubleClickMode("wysiwyg");
fire("dblclick", { target: dblBlock, detail: 2 });
check("dblclick wysiwyg: bridge asked with the block's start line",
  JSON.stringify(calls) === JSON.stringify([["wysiwyg", 5]]));

// Switching back to "inline" (settings changed mid-session) silences it
// again without touching triple-click at all.
calls.length = 0;
sandbox.window.__inlineEdit.setDoubleClickMode("inline");
fire("dblclick", { target: dblBlock, detail: 2 });
check("dblclick back to inline: no bridge call", calls.length === 0);
fire("click", { target: dblBlock, detail: 3 });
check("triple-click still opens the inline editor regardless of dblclick mode",
  textareaOf(dblBlock) !== null);
fire("keydown", { target: textareaOf(dblBlock), key: "Escape" });

// The double-click boot parameter wires the initial mode the same way.
calls.length = 0;
sandbox.window.__inlineEditBoot(bridge, true, "wysiwyg");
fire("dblclick", { target: dblBlock, detail: 2 });
check("dblclick: boot(..., \"wysiwyg\") enables it immediately",
  JSON.stringify(calls) === JSON.stringify([["wysiwyg", 5]]));

if (failures.length) {
  console.error("FAILED:\n  " + failures.join("\n  "));
  process.exit(1);
}
console.log("OK");
