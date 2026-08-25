/* Drives assets/vditor_glue.js against a minimal Vditor + DOM stub.
 *
 * Not a browser: fake timers let the harness deterministically advance the
 * 250ms debounce without a real event loop, and the fake Vditor constructor
 * only implements setValue/getValue plus capturing the `input`/`after`
 * callbacks glue code wires up. Prints "OK" and exits 0 when every case
 * passes; prints the failure and exits 1 otherwise.
 *
 * Run directly (node tests/js/vditor_glue_harness.js) or via
 * tests/test_vditor_glue_js.py.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SCRIPT = path.join(__dirname, "..", "..", "assets", "vditor_glue.js");

// ---- fake timers (deterministic debounce control) -------------------------
let nextTimerId = 1;
const timers = new Map(); // id -> {due, fn}
let clock = 0;

function fakeSetTimeout(fn, delay) {
  const id = nextTimerId++;
  timers.set(id, { due: clock + (delay || 0), fn: fn });
  return id;
}
function fakeClearTimeout(id) {
  timers.delete(id);
}
function advance(ms) {
  clock += ms;
  // Run due timers in the order they were scheduled.
  const due = Array.from(timers.entries())
    .filter(([, t]) => t.due <= clock)
    .sort((a, b) => a[1].due - b[1].due);
  for (const [id, t] of due) {
    if (!timers.has(id)) continue; // cancelled by an earlier callback
    timers.delete(id);
    t.fn();
  }
}

// ---- minimal DOM: keydown listening + a fake .vditor-hint panel -----------
const docListeners = {};
let hintPanels = []; // [{ style: { display } }], mutated by tests below
let mountedWysiwygRoot = null; // set by the v4 block-handle tests below
const document = {
  addEventListener(type, fn) {
    (docListeners[type] = docListeners[type] || []).push(fn);
  },
  removeEventListener(type, fn) {
    const list = docListeners[type];
    if (!list) return;
    const i = list.indexOf(fn);
    if (i >= 0) list.splice(i, 1);
  },
  querySelectorAll(selector) {
    return selector === ".vditor-hint" ? hintPanels : [];
  },
  querySelector(selector) {
    return selector === ".vditor-wysiwyg" ? mountedWysiwygRoot : null;
  },
  createElement(tag) {
    return createFakeElement(tag);
  },
  head: null, // assigned once createFakeElement is defined, see below
};
document.head = createFakeElement("head");
function fireKeydown(event) {
  event.preventDefault = function () { event.defaultPrevented = true; };
  event.stopPropagation = function () { event.propagationStopped = true; };
  (docListeners.keydown || []).forEach((fn) => fn(event));
  return event;
}
function fireContextMenu(event) {
  event.preventDefault = function () { event.defaultPrevented = true; };
  (docListeners.contextmenu || []).forEach((fn) => fn(event));
  return event;
}
// Generic version of the two helpers above, for the pointermove/pointerup
// listeners the v4 drag code adds/removes on `document` during a drag.
function fireDocEvent(type, props) {
  const event = Object.assign({}, props || {});
  event.preventDefault = function () { event.defaultPrevented = true; };
  event.stopPropagation = function () { event.propagationStopped = true; };
  (docListeners[type] || []).slice().forEach((fn) => fn(event));
  return event;
}

// ---- minimal fake element tree (v4: block handles + drag-to-reorder) ------
// Not a real DOM: just enough of the Element surface (parent/child links,
// classList, style, getBoundingClientRect, addEventListener) for
// assets/vditor_glue.js's block-handle code to run against and be asserted
// on, mirroring how FakeVditor above stands in for the real Vditor class.
function makeRect(top, height, left) {
  left = left || 0;
  return { top, left, width: 200, height, bottom: top + height, right: left + 200 };
}

function FakeElement(tagName) {
  this.tagName = String(tagName || "div").toUpperCase();
  this.className = "";
  this.children = [];
  this.parentNode = null;
  this._listeners = {};
  this.style = {};
  this.dataset = {};
  this._rect = makeRect(0, 0, 0);
  this.scrollTop = 0;
  this.textContent = "";
  this.title = "";
  const el = this;
  this.classList = {
    add(c) {
      const set = el.className.split(" ").filter(Boolean);
      if (set.indexOf(c) < 0) set.push(c);
      el.className = set.join(" ");
    },
    remove(c) {
      el.className = el.className.split(" ").filter((x) => x && x !== c).join(" ");
    },
    contains(c) {
      return el.className.split(" ").indexOf(c) >= 0;
    },
  };
}
FakeElement.prototype.appendChild = function (child) {
  child.parentNode = this;
  this.children.push(child);
  return child;
};
FakeElement.prototype.insertBefore = function (child, ref) {
  if (child.parentNode) {
    const oldSiblings = child.parentNode.children;
    const oi = oldSiblings.indexOf(child);
    if (oi >= 0) oldSiblings.splice(oi, 1);
  }
  child.parentNode = this;
  if (ref == null) {
    this.children.push(child);
  } else {
    let idx = this.children.indexOf(ref);
    if (idx < 0) idx = this.children.length;
    this.children.splice(idx, 0, child);
  }
  return child;
};
Object.defineProperty(FakeElement.prototype, "nextSibling", {
  get() {
    if (!this.parentNode) return null;
    const idx = this.parentNode.children.indexOf(this);
    return idx >= 0 ? (this.parentNode.children[idx + 1] || null) : null;
  },
});
Object.defineProperty(FakeElement.prototype, "firstElementChild", {
  get() { return this.children[0] || null; },
});
FakeElement.prototype.getBoundingClientRect = function () { return this._rect; };
FakeElement.prototype.addEventListener = function (type, fn) {
  (this._listeners[type] = this._listeners[type] || []).push(fn);
};
FakeElement.prototype.removeEventListener = function (type, fn) {
  const list = this._listeners[type];
  if (!list) return;
  const i = list.indexOf(fn);
  if (i >= 0) list.splice(i, 1);
};
FakeElement.prototype.setPointerCapture = function () {};
FakeElement.prototype.releasePointerCapture = function () {};
FakeElement.prototype.focus = function () {};

function createFakeElement(tag) {
  return new FakeElement(tag);
}

// Fires `type` directly on `el` (no bubbling simulation -- tests pass the
// intended `target` explicitly via props, same as fireKeydown/fireContextMenu
// above do for document-level events).
function fireOn(el, type, props) {
  const event = Object.assign({ target: el }, props || {});
  event.preventDefault = function () { event.defaultPrevented = true; };
  event.stopPropagation = function () { event.propagationStopped = true; };
  (el._listeners[type] || []).slice().forEach((fn) => fn(event));
  return event;
}

// ---- fake Vditor -----------------------------------------------------------
let lastVditorInstance = null;
let lastVditorOptions = null;
let lastVditorId = null;

function FakeVditor(elementId, options) {
  lastVditorId = elementId;
  lastVditorOptions = options;
  this._value = "";
  this.options = options;
  lastVditorInstance = this;
}
FakeVditor.prototype.setValue = function (text) {
  this._value = text;
  // Real Vditor may or may not synchronously fire `input` on setValue;
  // exercised both ways via __simulateInput below.
};
FakeVditor.prototype.getValue = function () {
  return this._value;
};
let lastInsertedValue = null;
FakeVditor.prototype.insertValue = function (text) {
  lastInsertedValue = text;
};

function typeText(text) {
  // Simulate the user typing: Vditor mutates its own DOM and calls the
  // `input` callback glue code registered.
  lastVditorInstance._value = text;
  lastVditorOptions.input(text);
}

// ---- load the real script ---------------------------------------------------
const sandbox = {
  console: console,
  document: document,
  setTimeout: fakeSetTimeout,
  clearTimeout: fakeClearTimeout,
  Vditor: FakeVditor,
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(SCRIPT, "utf8"), sandbox, { filename: SCRIPT });

// ---- assertions --------------------------------------------------------------
const failures = [];
function check(label, condition) {
  if (!condition) failures.push(label);
}

// ---- bridge stub --------------------------------------------------------------
const pushed = [];
const saves = [];
const readies = [];
const escapes = [];
const toolbarActions = [];
const contextMenus = [];
const bridge = {
  contentChanged(md) { pushed.push(md); },
  saveRequested() { saves.push(true); },
  ready() { readies.push(true); },
  escRequested() { escapes.push(true); },
  toolbarAction(name) { toolbarActions.push(name); },
  contextMenuRequested(x, y) { contextMenus.push([x, y]); },
};

// ---- boot -----------------------------------------------------------------
const instance = sandbox.window.__wysiwygBoot(bridge, { elementId: "vditor", value: "start" });
check("boot: constructs Vditor against the given element id", lastVditorId === "vditor");
check("boot: returns the vditor instance", instance === lastVditorInstance);
check("boot: mode is wysiwyg", lastVditorOptions.mode === "wysiwyg");
check("boot: cache/autosave is disabled", lastVditorOptions.cache.enable === false);

// ---- v2: full "Office Viewer" toolbar --------------------------------------
// At least these; export/VSCode-only/fullscreen buttons are deliberately
// left out (see the comment above DEFAULT_TOOLBAR in vditor_glue.js).
[
  "outline", "undo", "redo", "headings", "bold", "italic", "strike",
  "link", "list", "ordered-list", "check", "outdent", "indent",
  "quote", "code", "inline-code", "table",
].forEach((item) => {
  check(`toolbar: includes "${item}"`, lastVditorOptions.toolbar.indexOf(item) >= 0);
});

// `after` fires once construction settles; simulate it now.
lastVditorOptions.after();
check("ready: bridge.ready() called once construction settles",
  readies.length === 1);
check("ready: initial value loaded via setValue, not a raw assignment",
  lastVditorInstance.getValue() === "start");
// Release the guard the initial setValue(options.value) armed before any
// user-input assertions below -- otherwise the first keystroke is wrongly
// swallowed as that load's echo instead of starting its own debounce.
advance(0);

// ---- debounced push on real input -----------------------------------------
typeText("hello");
check("input: nothing pushed before the debounce window elapses",
  pushed.length === 0);
advance(249);
check("input: still nothing at 249ms", pushed.length === 0);
advance(1);
check("input: pushed once the 250ms debounce elapses",
  pushed.length === 1 && pushed[0] === "hello");

// Rapid typing collapses into a single push (debounce restarts each time).
pushed.length = 0;
typeText("h");
advance(100);
typeText("he");
advance(100);
typeText("hel");
advance(100);
check("debounce: no push yet -- each keystroke restarted the timer",
  pushed.length === 0);
advance(150);
check("debounce: exactly one push, holding the latest value",
  pushed.length === 1 && pushed[0] === "hel");

// ---- setValue echo guard ----------------------------------------------------
pushed.length = 0;
sandbox.window.__wysiwygGlue.setValue("loaded from python");
check("setValue: getValue reflects the loaded text immediately",
  sandbox.window.__wysiwygGlue.getValue() === "loaded from python");
// Vditor's setValue firing `input` synchronously must not push back to Python.
lastVditorOptions.input("loaded from python");
advance(300);
check("setValue: the echo it caused is never pushed to Python",
  pushed.length === 0);

// A real edit right after a setValue push must not be swallowed by a stale
// guard: the guard consumes exactly one echo and then behaves normally.
typeText("user typed after load");
advance(250);
check("setValue: a genuine edit right after loading still pushes",
  pushed.length === 1 && pushed[0] === "user typed after load");

// setValue with no input echo at all (some Vditor versions) must not leave
// the guard permanently armed and silently eating the next real edit.
pushed.length = 0;
sandbox.window.__wysiwygGlue.setValue("no echo this time");
advance(0); // release the setTimeout(0) fallback that clears the guard
typeText("typed with no prior echo");
advance(250);
check("setValue: an unconsumed guard still releases and future edits push",
  pushed.length === 1 && pushed[0] === "typed with no prior echo");

// ---- flushPending (used before Ctrl+S so a save definitely sees the
// latest keystroke, not one stuck behind the debounce window) --------------
pushed.length = 0;
typeText("about to save");
sandbox.window.__wysiwygGlue.flushPending();
check("flushPending: pushes immediately without waiting for the debounce",
  pushed.length === 1 && pushed[0] === "about to save");

// ---- Ctrl+S interception ----------------------------------------------------
// A keystroke sitting inside the debounce window must reach Python via the
// flush Ctrl+S triggers, ahead of saveRequested (see the ordering comment
// in vditor_glue.js) -- otherwise Ctrl+S can save one edit stale.
pushed.length = 0;
saves.length = 0;
typeText("about to ctrl-s");
let ev = fireKeydown({ key: "s", ctrlKey: true });
check("ctrl+s: flushes the pending edit first",
  pushed.length === 1 && pushed[0] === "about to ctrl-s");
check("ctrl+s: bridge.saveRequested called", saves.length === 1);
check("ctrl+s: native save suppressed", ev.defaultPrevented === true);
check("ctrl+s: contentChanged reaches Python before saveRequested",
  pushed.length === 1 && saves.length === 1);

saves.length = 0;
ev = fireKeydown({ key: "S", ctrlKey: true });
check("ctrl+shift+s / capital S: still intercepted", saves.length === 1);

saves.length = 0;
ev = fireKeydown({ key: "s", metaKey: true });
check("cmd+s: also intercepted", saves.length === 1);

saves.length = 0;
ev = fireKeydown({ key: "s", ctrlKey: false });
check("plain s: not intercepted", saves.length === 0);
check("plain s: default not prevented", ev.defaultPrevented !== true);

// ---- v2: Esc leaves WYSIWYG, unless a hint panel is open first ------------
escapes.length = 0;
hintPanels = [];
fireKeydown({ key: "Escape" });
check("esc: clean escape reaches the bridge", escapes.length === 1);

escapes.length = 0;
hintPanels = [{ style: { display: "block" } }];
fireKeydown({ key: "Escape" });
check("esc: swallowed while a hint panel is open (first Esc closes it)",
  escapes.length === 0);

// Once the panel reports closed (display: none, as Vditor leaves it), the
// very next Esc reaches the bridge -- this models "first Esc closes the
// hint, second Esc leaves WYSIWYG" without needing a real hint widget.
hintPanels = [{ style: { display: "none" } }];
fireKeydown({ key: "Escape" });
check("esc: reaches the bridge once no panel reads as open",
  escapes.length === 1);

// A panel with no inline display style at all (never shown) must not be
// mistaken for an open one.
escapes.length = 0;
hintPanels = [{ style: {} }];
fireKeydown({ key: "Escape" });
check("esc: a hint element with no display style set does not block it",
  escapes.length === 1);
hintPanels = [];

// ---- v4: custom toolbar buttons (save/export/insert-image/theme) ----------
// These are plain-object entries appended after the built-in string names;
// find each by its "name" and invoke .click() the way Vditor would.
function findToolbarItem(name) {
  return (lastVditorOptions.toolbar || []).find(
    (item) => item && typeof item === "object" && item.name === name
  );
}
[
  ["save", "save"],
  ["export-pdf", "export_pdf"],
  ["export-docx", "export_docx"],
  ["export-html", "export_html"],
  ["insert-image", "insert_image"],
  ["theme-toggle", "toggle_theme"],
].forEach(([toolbarName, actionName]) => {
  const item = findToolbarItem(toolbarName);
  check(`toolbar: custom item "${toolbarName}" is present`, !!item);
  if (!item) return;
  check(`toolbar: "${toolbarName}" has a tip and an icon`,
    typeof item.tip === "string" && item.tip.length > 0 &&
    typeof item.icon === "string" && item.icon.length > 0);
  toolbarActions.length = 0;
  item.click();
  check(`toolbar: "${toolbarName}" click routes to bridge.toolbarAction("${actionName}")`,
    toolbarActions.length === 1 && toolbarActions[0] === actionName);
});

// ---- v4: right-click -> bridge.contextMenuRequested(x, y) ------------------
contextMenus.length = 0;
let cmEvent = fireContextMenu({ clientX: 42, clientY: 17 });
check("contextmenu: native menu suppressed", cmEvent.defaultPrevented === true);
check("contextmenu: bridge called once with viewport coordinates",
  contextMenus.length === 1 &&
  contextMenus[0][0] === 42 && contextMenus[0][1] === 17);

// ---- v4: insertValue (image/attachment link insertion) --------------------
lastInsertedValue = null;
sandbox.window.__wysiwygGlue.insertValue("![alt](assets/img.png)");
check("insertValue: delegates to the underlying Vditor instance",
  lastInsertedValue === "![alt](assets/img.png)");

// ---- v4 second wave: Notion-style block handles + drag-to-reorder ---------
// Build the ".vditor-wysiwyg > pre.vditor-reset > (blocks)" tree Vditor
// itself constructs for WYSIWYG mode (see the comment above
// installBlockHandles() in vditor_glue.js) and wire the handles onto it via
// the _installBlockHandles() test hook -- production code does this
// automatically from `after`, once that DOM actually exists.
mountedWysiwygRoot = createFakeElement("div");
mountedWysiwygRoot.className = "vditor-wysiwyg";
mountedWysiwygRoot._rect = makeRect(0, 300, 0);
const editableEl = createFakeElement("pre");
editableEl.className = "vditor-reset";
editableEl._rect = makeRect(0, 300, 0);
mountedWysiwygRoot.appendChild(editableEl);

function makeBlock(tag, top, height, label) {
  const b = createFakeElement(tag);
  b._rect = makeRect(top, height, 0);
  b.textContent = label;
  return b;
}
const blockA = makeBlock("p", 0, 50, "Block A");
const blockB = makeBlock("p", 50, 50, "Block B");
const blockC = makeBlock("p", 100, 50, "Block C");
editableEl.appendChild(blockA);
editableEl.appendChild(blockB);
editableEl.appendChild(blockC);

sandbox.window.__wysiwygGlue._installBlockHandles();
const glueState = sandbox.window.__wysiwygGlue._state;
check("handles: installed against the real Vditor WYSIWYG DOM shape",
  !!glueState.handleGroup && !!glueState.plusHandle && !!glueState.dragHandle);
check("handles: hidden before any hover",
  glueState.handleGroup.style.display === "none" || glueState.handleGroup.style.display === undefined);

// -- hover shows the handles positioned over the hovered block, and hiding
// again (after the hide-delay) removes them --
fireOn(editableEl, "mouseover", { target: blockA });
check("handles: shown on hover", glueState.handleGroup.style.display === "flex");
check("handles: track which block is hovered", glueState.hoverBlock === blockA);
check("handles: positioned using the hovered block's own rect (not blockB's)",
  glueState.handleGroup.style.top === "0px");

fireOn(editableEl, "mouseover", { target: blockB });
check("handles: reposition when hovering a different block",
  glueState.handleGroup.style.top === "50px" && glueState.hoverBlock === blockB);

fireOn(editableEl, "mouseleave", {});
check("handles: still shown immediately after mouseleave (hide is delayed)",
  glueState.handleGroup.style.display === "flex");
advance(120); // matches HANDLE_HIDE_DELAY_MS in vditor_glue.js
check("handles: hidden once the hide-delay elapses",
  glueState.handleGroup.style.display === "none");

// Moving onto the handle group itself (not a descendant of the
// contenteditable root) must cancel a pending hide -- otherwise hovering
// toward the "+"/"::" buttons would hide them before the click lands.
fireOn(editableEl, "mouseover", { target: blockA });
fireOn(editableEl, "mouseleave", {});
fireOn(glueState.handleGroup, "mouseenter", {});
advance(200);
check("handles: hovering the handle group itself cancels the pending hide",
  glueState.handleGroup.style.display === "flex");
fireOn(glueState.handleGroup, "mouseleave", {});
advance(200);
check("handles: hide resumes once the handle group itself is left",
  glueState.handleGroup.style.display === "none");

// -- "+" inserts an empty paragraph right after the hovered block, and
// pushes through the same debounced path as typing --
fireOn(editableEl, "mouseover", { target: blockA });
pushed.length = 0;
const lenBefore = editableEl.children.length;
fireOn(glueState.plusHandle, "click", {});
check("plus: inserts exactly one new block",
  editableEl.children.length === lenBefore + 1);
check("plus: the new block lands directly after the hovered block",
  editableEl.children[0] === blockA && editableEl.children[1] !== blockB &&
  editableEl.children[1].tagName === "P" && editableEl.children[2] === blockB);
advance(250);
check("plus: the insert reaches Python via the normal debounced push",
  pushed.length === 1);
// Clean up the inserted paragraph so the drag tests below see the original
// 3-block layout.
editableEl.children.splice(1, 1);

// -- drag-to-reorder: grab blockB's handle and drop it above blockA --
pushed.length = 0;
fireOn(editableEl, "mouseover", { target: blockB });
fireOn(glueState.dragHandle, "pointerdown", { button: 0, pointerId: 1 });
check("drag: the dragged block is marked (visual feedback)",
  blockB.classList.contains("vditor-block-drag-source"));

fireDocEvent("pointermove", { clientY: 5 }); // inside blockA (top 0-50)
check("drag: shows an insertion-line indicator while dragging over a target",
  glueState.dropIndicator && glueState.dropIndicator.style.display === "block");
check("drag: the indicator sits at the top edge of blockA (\"before\" position)",
  glueState.dropIndicator.style.top === "0px");

fireDocEvent("pointerup", { clientY: 5 });
check("drag: DOM reordered -- blockB now comes first",
  editableEl.children[0] === blockB && editableEl.children[1] === blockA &&
  editableEl.children[2] === blockC);
check("drag: the drag-source marker is cleared once the drop completes",
  !blockB.classList.contains("vditor-block-drag-source"));
check("drag: the insertion-line indicator is hidden again after drop",
  glueState.dropIndicator.style.display === "none");

advance(250);
check("drag: drop reaches Python via the exact same debounce/push path a "
  + "real keystroke uses (bridge.contentChanged fires once)",
  pushed.length === 1);

// A stray pointermove after the drop must be a no-op: the listener was
// removed from `document`, proving no separate/duplicate drag path was left
// wired up.
pushed.length = 0;
fireDocEvent("pointermove", { clientY: 999 });
check("drag: pointermove after drop is inert (listener was unwired)",
  pushed.length === 0);

// -- auto-scroll near the top/bottom edges of the editable area --
editableEl.scrollTop = 100;
fireOn(editableEl, "mouseover", { target: editableEl.children[0] });
fireOn(glueState.dragHandle, "pointerdown", { button: 0, pointerId: 2 });
fireDocEvent("pointermove", { clientY: 295 }); // within 40px of the bottom edge (rect height 300)
check("drag: auto-scrolls down when the pointer nears the bottom edge",
  editableEl.scrollTop === 116);
fireDocEvent("pointermove", { clientY: 5 }); // within 40px of the top edge
check("drag: auto-scrolls up when the pointer nears the top edge",
  editableEl.scrollTop === 100);
fireDocEvent("pointerup", { clientY: 5 });

if (failures.length) {
  console.error("FAILED:\n  " + failures.join("\n  "));
  process.exit(1);
}
console.log("OK");
