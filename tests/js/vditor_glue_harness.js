/* Drives assets/vditor_glue.js against a minimal Vditor + DOM stub.
 *
 * Not a browser: fake timers let the harness deterministically advance the
 * 450ms debounce without a real event loop, and the fake Vditor constructor
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
const selectorResults = new Map(); // exact Office overlays, populated per test
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
    if (selectorResults.has(selector)) return selectorResults.get(selector);
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
  event.stopImmediatePropagation = function () {
    event.immediatePropagationStopped = true;
  };
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
FakeElement.prototype.querySelector = function (selector) {
  return this._querySelectors ? (this._querySelectors.get(selector) || null) : null;
};
FakeElement.prototype.click = function () {
  fireOn(this, "click");
};

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
const pushedDetails = [];
const fullContentCalls = [];
const saves = [];
const saveContents = [];
const readies = [];
const escapes = [];
const toolbarActions = [];
const zoomRequests = [];
const contextMenus = [];
const bridge = {
  contentChanged(md, generation, start, deleteCount, inserted, baseRevision, finalLength) {
    fullContentCalls.push(md);
    pushed.push(md);
    pushedDetails.push({ md, generation, start, deleteCount, inserted, baseRevision, finalLength });
  },
  contentDelta(generation, start, deleteCount, inserted, baseRevision, finalLength) {
    const before = sandbox.window.__wysiwygGlue._state.lastPushedMarkdown;
    const md = before.slice(0, start) + inserted + before.slice(start + deleteCount);
    pushed.push(md);
    pushedDetails.push({
      md, generation, start, deleteCount, inserted, baseRevision, finalLength,
      deltaOnly: true,
    });
  },
  saveRequested() { saves.push(true); },
  saveWithContent(md, generation) { saveContents.push([md, generation]); },
  ready() { readies.push(true); },
  escRequested() { escapes.push(true); },
  toolbarAction(name) { toolbarActions.push(name); },
  zoomRequested(steps) { zoomRequests.push(steps); },
  contextMenuRequested(x, y) { contextMenus.push([x, y]); },
};

// ---- boot -----------------------------------------------------------------
const instance = sandbox.window.__wysiwygBoot(bridge, { elementId: "vditor", value: "start" });
check("boot: constructs Vditor against the given element id", lastVditorId === "vditor");
check("boot: returns the vditor instance", instance === lastVditorInstance);
check("boot: mode is wysiwyg", lastVditorOptions.mode === "wysiwyg");
check("boot: cache/autosave is disabled", lastVditorOptions.cache.enable === false);
check("boot: core VS Code focus cache stays disabled for shared documents",
  lastVditorOptions.cache.focusHost === "browser");
check("boot: outline starts open on the left",
  lastVditorOptions.outline.position === "left");
check("boot: first-use outline width matches Office Viewer",
  lastVditorOptions.outline.width === 280);
const officeLayoutStyle = document.head.children.find(
  (element) => element.id === "wysiwyg-office-layout"
);
check("toolbar: compact style is installed before Vditor's after hook",
  !!officeLayoutStyle);
check("boot: Office Viewer editor theme is enabled",
  lastVditorOptions.editorTheme === "Auto");

// ---- v2: full "Office Viewer" toolbar --------------------------------------
// Built-in Office Viewer controls; host actions are asserted separately.
[
  "outline", "undo", "redo", "headings", "bold", "italic", "strike",
  "link", "font-color", "background-color", "list", "ordered-list",
  "check", "quote", "code", "table", "editor-theme",
  "editor-theme-toggle", "find", "ai-settings", "settings",
].forEach((item) => {
  check(`toolbar: includes "${item}"`, lastVditorOptions.toolbar.indexOf(item) >= 0);
});

// `after` fires once construction settles; simulate it now.
lastVditorOptions.after();
check("ready: bridge.ready() called once construction settles",
  readies.length === 1);
check("toolbar: Office layout forces one row with horizontal overflow",
  !!officeLayoutStyle &&
  officeLayoutStyle.textContent.indexOf("flex-wrap:nowrap") >= 0 &&
  officeLayoutStyle.textContent.indexOf("overflow-x:auto") >= 0 &&
  officeLayoutStyle.textContent.indexOf("vditor-toolbar__br") >= 0);
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
advance(449);
check("input: still nothing at 449ms", pushed.length === 0);
advance(1);
check("input: pushed once the 450ms debounce elapses",
  pushed.length === 1 && pushed[0] === "hello");
check("input: normal typing crosses the bridge as delta-only IPC",
  pushedDetails[0].deltaOnly === true && fullContentCalls.length === 0);

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
advance(350);
check("debounce: exactly one push, holding the latest value",
  pushed.length === 1 && pushed[0] === "hel");

// ---- setValue echo guard ----------------------------------------------------
pushed.length = 0;
sandbox.window.__wysiwygGlue.setValue("loaded from python");
check("setValue: getValue reflects the loaded text immediately",
  sandbox.window.__wysiwygGlue.getValue() === "loaded from python");
// Vditor's setValue firing `input` synchronously must not push back to Python.
lastVditorOptions.input("loaded from python");
advance(500);
check("setValue: the echo it caused is never pushed to Python",
  pushed.length === 0);

// A real edit right after a setValue push must not be swallowed by a stale
// guard: the guard consumes exactly one echo and then behaves normally.
typeText("user typed after load");
advance(450);
check("setValue: a genuine edit right after loading still pushes",
  pushed.length === 1 && pushed[0] === "user typed after load");

// setValue with no input echo at all (some Vditor versions) must not leave
// the guard permanently armed and silently eating the next real edit.
pushed.length = 0;
sandbox.window.__wysiwygGlue.setValue("no echo this time");
advance(0); // release the setTimeout(0) fallback that clears the guard
typeText("typed with no prior echo");
advance(450);
check("setValue: an unconsumed guard still releases and future edits push",
  pushed.length === 1 && pushed[0] === "typed with no prior echo");

// Delta offsets are UTF-16 (the coordinate system shared by JS and Qt), but
// boundaries may never split an emoji surrogate pair: QWebChannel cannot
// carry a lone low/high surrogate without data loss.
pushed.length = 0;
pushedDetails.length = 0;
sandbox.window.__wysiwygGlue.setValue("😀", 7);
advance(0);
typeText("😺");
advance(450);
check("delta: emoji replacement starts before the complete surrogate pair",
  pushedDetails.length === 1 && pushedDetails[0].start === 0 &&
  pushedDetails[0].deleteCount === 2 && pushedDetails[0].inserted === "😺");

pushed.length = 0;
pushedDetails.length = 0;
sandbox.window.__wysiwygGlue.setValue("\u{10000}", 8);
advance(0);
typeText("\u{10400}");
advance(450);
check("delta: an equal low surrogate never becomes a detached suffix",
  pushedDetails.length === 1 && pushedDetails[0].start === 0 &&
  pushedDetails[0].deleteCount === 2 && pushedDetails[0].inserted === "\u{10400}");

// A transition snapshot is a two-phase read. Until Python acknowledges it,
// the ordinary debounce remains pending so timeout/cancel cannot erase the
// recovery-worthy edit.
pushed.length = 0;
sandbox.window.__wysiwygGlue.setValue("snapshot base", 9);
advance(0);
typeText("snapshot pending");
const pendingEnvelope = JSON.parse(
  sandbox.window.__wysiwygGlue.takeSnapshotEnvelope()
);
advance(450);
check("snapshot: pending debounce waits for acknowledge/cancel", pushed.length === 0);
sandbox.window.__wysiwygGlue.cancelSnapshot(pendingEnvelope.token);
advance(450);
check("snapshot: cancel preserves and eventually pushes the pending edit",
  pushed.length === 1 && pushed[0] === "snapshot pending");

// ---- flushPending (used before Ctrl+S so a save definitely sees the
// latest keystroke, not one stuck behind the debounce window) --------------
pushed.length = 0;
typeText("about to save");
sandbox.window.__wysiwygGlue.flushPending();
check("flushPending: pushes immediately without waiting for the debounce",
  pushed.length === 1 && pushed[0] === "about to save");

// ---- Ctrl+S interception ----------------------------------------------------
// A keystroke sitting inside the debounce window is carried in the save
// message itself, so Python never has to race a separate content push.
pushed.length = 0;
saves.length = 0;
saveContents.length = 0;
typeText("about to ctrl-s");
let ev = fireKeydown({ key: "s", ctrlKey: true });
check("ctrl+s: carries the live edit in the save request",
  saveContents.length === 1 && saveContents[0][0] === "about to ctrl-s");
check("ctrl+s: native save suppressed", ev.defaultPrevented === true);

saveContents.length = 0;
ev = fireKeydown({ key: "S", ctrlKey: true });
check("ctrl+shift+s / capital S: still intercepted", saveContents.length === 1);

saveContents.length = 0;
ev = fireKeydown({ key: "s", metaKey: true });
check("cmd+s: also intercepted", saveContents.length === 1);

saveContents.length = 0;
ev = fireKeydown({ key: "s", ctrlKey: false });
check("plain s: not intercepted", saveContents.length === 0);
check("plain s: default not prevented", ev.defaultPrevented !== true);

// ---- Office Viewer: Esc closes one exact overlay and never leaves WYSIWYG -
escapes.length = 0;
hintPanels = [];
ev = fireKeydown({ key: "Escape" });
check("esc: clean escape never reaches the bridge", escapes.length === 0);
check("esc: clean escape is left to the exact editor", ev.defaultPrevented !== true);

escapes.length = 0;
hintPanels = [{ style: { display: "block" } }];
fireKeydown({ key: "Escape" });
check("esc: existing hint handling remains inside the editor",
  escapes.length === 0);

hintPanels = [{ style: { display: "none" } }];
fireKeydown({ key: "Escape" });
check("esc: a second escape after a hint closes still stays in WYSIWYG",
  escapes.length === 0);

// A panel with no inline display style at all (never shown) must not be
// mistaken for an open one.
escapes.length = 0;
hintPanels = [{ style: {} }];
fireKeydown({ key: "Escape" });
check("esc: an unused hint never causes a host exit", escapes.length === 0);
hintPanels = [];

const settingsItem = createFakeElement("div");
const settingsButton = createFakeElement("button");
settingsButton.dataset.type = "settings";
const settingsPanel = createFakeElement("div");
settingsPanel.classList.add("vditor-hint");
settingsPanel.style.display = "block";
settingsButton.click = function () { settingsPanel.style.display = "none"; };
settingsItem.appendChild(settingsButton);
settingsItem.appendChild(settingsPanel);
selectorResults.set(
  ".vditor-toolbar button[data-type='settings']",
  settingsButton
);
ev = fireKeydown({ key: "Escape" });
check("esc: exact settings panel is closed by its own toolbar trigger",
  settingsPanel.style.display === "none");
check("esc: closing an Office toolbar panel consumes only that escape",
  ev.defaultPrevented === true && ev.propagationStopped === true);
selectorResults.delete(".vditor-toolbar button[data-type='settings']");

const languageWrap = createFakeElement("div");
languageWrap.classList.add("vditor-cm-chrome__lang--open");
const languageTrigger = createFakeElement("button");
let languageCloseClicks = 0;
languageTrigger.click = function () {
  languageCloseClicks += 1;
  languageWrap.classList.remove("vditor-cm-chrome__lang--open");
};
languageWrap._querySelectors = new Map([
  [".vditor-cm-chrome__lang-trigger", languageTrigger],
]);
selectorResults.set(".vditor-cm-chrome__lang--open", languageWrap);
fireKeydown({ key: "Escape" });
check("esc: exact code-language overlay closes through its trigger",
  languageCloseClicks === 1 &&
  !languageWrap.classList.contains("vditor-cm-chrome__lang--open"));
selectorResults.delete(".vditor-cm-chrome__lang--open");

const mermaidWrap = createFakeElement("div");
mermaidWrap.classList.add("vditor-mermaid-chrome__theme--open");
const mermaidTrigger = createFakeElement("button");
let mermaidCloseClicks = 0;
mermaidTrigger.click = function () {
  mermaidCloseClicks += 1;
  mermaidWrap.classList.remove("vditor-mermaid-chrome__theme--open");
};
mermaidWrap._querySelectors = new Map([
  [".vditor-mermaid-chrome__theme-trigger", mermaidTrigger],
]);
selectorResults.set(".vditor-mermaid-chrome__theme--open", mermaidWrap);
fireKeydown({ key: "Escape" });
check("esc: exact Mermaid-theme overlay closes through its trigger",
  mermaidCloseClicks === 1 &&
  !mermaidWrap.classList.contains("vditor-mermaid-chrome__theme--open"));
selectorResults.delete(".vditor-mermaid-chrome__theme--open");

const aiDialog = createFakeElement("div");
const aiClose = createFakeElement("button");
aiDialog.hidden = false;
aiClose.click = function () { aiDialog.hidden = true; };
aiDialog._querySelectors = new Map([
  [".vditor-ai-dialog__close, .vditor-ai-dialog__btn--cancel", aiClose],
]);
selectorResults.set(".vditor-ai-dialog-overlay:not([hidden])", aiDialog);
fireKeydown({ key: "Escape" });
check("esc: exact AI dialog closes without a host transition", aiDialog.hidden);
selectorResults.delete(".vditor-ai-dialog-overlay:not([hidden])");
check("esc: no overlay path ever calls bridge.escRequested", escapes.length === 0);

// ---- Office Viewer host toolbar actions ----------------------------------
// These are plain-object entries interleaved with the built-in string names;
// find each by its "name" and invoke .click() the way Vditor would.
function findToolbarItem(name) {
  return (lastVditorOptions.toolbar || []).find(
    (item) => item && typeof item === "object" && item.name === name
  );
}
[
  ["markmap", "open_graph"],
  ["edit-in-source", "toggle_source"],
  ["export", "show_export_menu"],
  ["insert-image", "insert_image"],
  ["insert-attachment", "insert_attachment"],
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

const saveItem = findToolbarItem("save");
check('toolbar: custom item "save" is present', !!saveItem);
if (saveItem) {
  saveContents.length = 0;
  typeText("toolbar save latest");
  saveItem.click();
  check("toolbar: save carries the latest Markdown without a debounce race",
    saveContents.length === 1 && saveContents[0][0] === "toolbar save latest");
}

// ---- Ctrl/meta+wheel -> one canonical, accumulated page-zoom request ------
const wheelSurface = createFakeElement("div");
wheelSurface.className = "vditor-wysiwyg";
wheelSurface.style["--editor-font-size"] = "18px";

zoomRequests.length = 0;
let plainWheel = fireDocEvent("wheel", {
  target: wheelSurface, ctrlKey: false, metaKey: false, deltaY: -100,
});
check("wheel: plain scrolling remains untouched",
  !plainWheel.defaultPrevented && zoomRequests.length === 0);
let zeroWheel = fireDocEvent("wheel", {
  target: wheelSurface, ctrlKey: true, deltaY: 0,
});
check("wheel: a zero delta remains untouched",
  !zeroWheel.defaultPrevented && zoomRequests.length === 0);

let firstZoomWheel = fireDocEvent("wheel", {
  target: wheelSurface, ctrlKey: true, deltaY: -100, deltaMode: 0,
});
advance(10);
fireDocEvent("wheel", {
  target: wheelSurface, ctrlKey: true, deltaY: -100, deltaMode: 0,
});
advance(6);
check("wheel: capture suppresses the vendored 250ms font-size handler",
  firstZoomWheel.defaultPrevented === true &&
  firstZoomWheel.immediatePropagationStopped === true);
check("wheel: two events inside one frame are accumulated without postponing flush",
  zoomRequests.length === 1 && zoomRequests[0] === 2);

zoomRequests.length = 0;
for (let i = 0; i < 10; i += 1) {
  fireDocEvent("wheel", {
    target: wheelSurface, ctrlKey: true, deltaY: -100, deltaMode: 0,
  });
}
advance(16);
check("wheel: one dense batch preserves every full wheel step",
  zoomRequests.length === 1 && zoomRequests[0] === 10);
advance(64);
check("wheel: a completed full-step batch has no duplicate idle remainder",
  zoomRequests.length === 1);

zoomRequests.length = 0;
for (let i = 0; i < 10; i += 1) {
  fireDocEvent("wheel", {
    target: wheelSurface, ctrlKey: true, deltaY: -3, deltaMode: 0,
  });
}
advance(16);
check("wheel: precision deltas remain pending below one full notch",
  zoomRequests.length === 0);
advance(64);
check("wheel: precision deltas emit their net direction on idle",
  zoomRequests.length === 1 && zoomRequests[0] === 1);

zoomRequests.length = 0;
for (let i = 0; i < 10; i += 1) {
  fireDocEvent("wheel", {
    target: wheelSurface, ctrlKey: true, deltaY: -51, deltaMode: 0,
  });
  advance(20);
}
advance(60);
check("wheel: fractional notches preserve totals across frame boundaries",
  zoomRequests.reduce((sum, value) => sum + value, 0) === 5);

zoomRequests.length = 0;
for (let i = 0; i < 4; i += 1) {
  fireDocEvent("wheel", {
    target: wheelSurface, ctrlKey: true, deltaY: -149, deltaMode: 0,
  });
  advance(20);
}
advance(60);
check("wheel: positive residual is emitted once on idle",
  zoomRequests.reduce((sum, value) => sum + value, 0) === 6);

zoomRequests.length = 0;
for (let i = 0; i < 4; i += 1) {
  fireDocEvent("wheel", {
    target: wheelSurface, ctrlKey: true, deltaY: -151, deltaMode: 0,
  });
  advance(20);
}
advance(60);
check("wheel: batching never rounds every fractional event independently",
  zoomRequests.reduce((sum, value) => sum + value, 0) === 6);

zoomRequests.length = 0;
fireDocEvent("wheel", {
  target: wheelSurface, ctrlKey: true, deltaY: -49, deltaMode: 0,
});
fireDocEvent("wheel", {
  target: wheelSurface, ctrlKey: true, deltaY: 50, deltaMode: 0,
});
advance(16);
advance(64);
check("wheel: nearly-cancelled direction changes stay inside the dead zone",
  zoomRequests.length === 0);

zoomRequests.length = 0;
fireDocEvent("wheel", {
  target: wheelSurface, ctrlKey: true, deltaY: -1, deltaMode: 2,
});
advance(16);
check("wheel: one page-mode event maps to one zoom step",
  zoomRequests.length === 1 && zoomRequests[0] === 1);

zoomRequests.length = 0;
for (let i = 0; i < 10; i += 1) {
  fireDocEvent("wheel", {
    target: wheelSurface, ctrlKey: true, deltaY: -100, deltaMode: 0,
  });
  advance(30);
}
check("wheel: ten rapid events are accumulated rather than throttled away",
  zoomRequests.reduce((sum, value) => sum + value, 0) === 10);

zoomRequests.length = 0;
fireDocEvent("wheel", {
  target: wheelSurface, metaKey: true, deltaY: -200, deltaMode: 0,
});
fireDocEvent("wheel", {
  target: wheelSurface, metaKey: true, deltaY: 100, deltaMode: 0,
});
advance(40);
check("wheel: opposite deltas in one batch collapse to their net direction",
  zoomRequests.length === 1 && zoomRequests[0] === 1);
check("wheel: canonical zoom does not rewrite the editor font-size setting",
  wheelSurface.style["--editor-font-size"] === "18px");

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
advance(450);
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

advance(450);
check("drag: drop reaches Python via the exact same debounce/push path a "
  + "real keystroke uses (bridge.contentDelta fires once)",
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
