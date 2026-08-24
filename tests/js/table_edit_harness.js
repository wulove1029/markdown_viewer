/* Drives assets/table_edit.js against a minimal DOM stub.
 *
 * Not a browser: the stub implements only what table_edit.js touches, so it
 * verifies the grid's shape, the model round-trip and the row/column/align/
 * keyboard/paste operations, not layout, real carets or real clipboards.
 * Prints "OK" and exits 0 when every case passes; prints the failures and
 * exits 1 otherwise.
 *
 * Run directly: node tests/js/table_edit_harness.js
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SCRIPT = path.join(__dirname, "..", "..", "assets", "table_edit.js");

// ---- minimal DOM ---------------------------------------------------------
let lastFocused = null;

function El(tag) {
  const el = {
    tagName: String(tag).toUpperCase(),
    className: "",
    attrs: {},
    // A real CSSStyleDeclaration reports "" for an unset property, and
    // clearing an alignment depends on that, so the stub must match.
    style: { textAlign: "" },
    childNodes: [],
    parentNode: null,
    textContent: "",
    contentEditable: "inherit",
    spellcheck: true,
    title: "",
    type: "",
    listeners: {},
    getAttribute(name) {
      return Object.prototype.hasOwnProperty.call(this.attrs, name)
        ? this.attrs[name]
        : null;
    },
    setAttribute(name, value) { this.attrs[name] = String(value); },
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
    focus() { lastFocused = el; }
  };
  return el;
}

const head = El("head");
const body = El("body");
// No createRange / getSelection / execCommand on purpose: table_edit.js has to
// stay usable when they are missing, and the fallbacks are what runs here.
const document = {
  head: head,
  body: body,
  createElement: (tag) => El(tag),
  addEventListener() {}
};

// ---- stub query helpers --------------------------------------------------
function classesOf(el) {
  return String(el.className || "").split(/\s+/).filter(Boolean);
}

function hasClass(el, name) {
  return classesOf(el).indexOf(name) >= 0;
}

function findAll(root, name, out) {
  out = out || [];
  (root.childNodes || []).forEach((node) => {
    if (hasClass(node, name)) out.push(node);
    findAll(node, name, out);
  });
  return out;
}

function find(root, name) {
  return findAll(root, name)[0] || null;
}

function fireOn(el, type, event) {
  event = event || {};
  event.preventDefault = function () { event.defaultPrevented = true; };
  event.stopPropagation = function () { event.propagationStopped = true; };
  (el.listeners[type] || []).forEach((fn) => fn(event));
  return event;
}

function clipboard(text) {
  return { clipboardData: { getData: () => text } };
}

// ---- load the real script ------------------------------------------------
const sandbox = { document: document, console: console, JSON: JSON };
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(SCRIPT, "utf8"), sandbox, { filename: SCRIPT });

const tableEdit = sandbox.window.__tableEdit;

// ---- assertions ----------------------------------------------------------
const failures = [];
function check(label, condition) {
  if (!condition) failures.push(label);
}
function same(label, actual, expected) {
  const a = JSON.stringify(actual);
  const b = JSON.stringify(expected);
  if (a !== b) failures.push(label + " (got " + a + ", want " + b + ")");
}

const MODEL = {
  headers: ["讀回的前 4 bytes", "代表"],
  aligns: ["", "center"],
  rows: [
    ["`FF FF FF FF`", "**這片板子從未校正過**"],
    ["`00 00 00 00`", "空白"]
  ],
  indent: "  "
};

function mk(model, opts) {
  const api = tableEdit.create(model || MODEL, opts);
  body.appendChild(api.element);
  return api;
}

// ---- 1. structure --------------------------------------------------------
let api = mk();
let root = api.element;
check("api: create returns the documented surface",
  typeof api.getModel === "function" && typeof api.focus === "function" &&
  typeof api.destroy === "function" && !!root);
check("style: injected into head", head.childNodes.length === 1);
check("structure: root class", root.className === "tedit");
check("structure: toolbar present", !!find(root, "tedit-bar"));
check("structure: grid present", !!find(root, "tedit-grid"));
check("structure: hint present", !!find(root, "tedit-hint"));
check("structure: colbar present", !!find(root, "tedit-colbar"));
check("structure: head row present", !!find(root, "tedit-head"));
check("structure: one corner cell", findAll(root, "tedit-corner").length === 1);
check("structure: one colhead per column",
  findAll(root, "tedit-colhead").length === 2);
check("structure: a gutter per header and body row",
  findAll(root, "tedit-gutter").length === 3);
check("structure: (rows + header) * cols editable cells",
  findAll(root, "tedit-cell").length === 6);
check("structure: three align buttons per column",
  findAll(root, "tedit-align").length === 6);
check("structure: column insert/delete per column",
  findAll(root, "tedit-colins").length === 2 &&
  findAll(root, "tedit-coldel").length === 2);
check("structure: row insert/delete per body row",
  findAll(root, "tedit-rowins").length === 2 &&
  findAll(root, "tedit-rowdel").length === 2);
check("structure: three toolbar buttons",
  findAll(root, "tedit-btn").length === 3);
check("structure: cells are plaintext-only contenteditable",
  findAll(root, "tedit-cell").every(
    (c) => c.contentEditable === "plaintext-only"));
check("structure: header cells carry the header text",
  findAll(root, "tedit-cell")[0].textContent === MODEL.headers[0]);
check("structure: alignment mirrored onto the cells",
  findAll(root, "tedit-cell")[1].style.textAlign === "center" &&
  findAll(root, "tedit-cell")[3].style.textAlign === "center" &&
  findAll(root, "tedit-cell")[0].style.textAlign === "");

// ---- 2. round-trip -------------------------------------------------------
same("round-trip: model survives untouched", api.getModel(), MODEL);

// focus() lands on the first header cell.
api.focus();
check("focus: first header cell focused",
  lastFocused === findAll(root, "tedit-cell")[0]);

// ---- 3. typing in a cell -------------------------------------------------
let cells = findAll(root, "tedit-cell");
cells[0].textContent = "位址";
cells[5].textContent = "尾端";
let m = api.getModel();
check("edit: header text read back", m.headers[0] === "位址");
check("edit: body text read back", m.rows[1][1] === "尾端");
check("edit: nothing else moved", m.rows[0][0] === MODEL.rows[0][0]);
api.destroy();

// ---- 4. adding rows and columns -----------------------------------------
api = mk();
root = api.element;
let toolbar = findAll(root, "tedit-btn");
fireOn(toolbar[0], "click");            // ＋列
m = api.getModel();
check("add row: one more row", m.rows.length === 3);
same("add row: new row is blank and full width", m.rows[2], ["", ""]);
check("add row: DOM grew too",
  findAll(root, "tedit-cell").length === 8 &&
  findAll(root, "tedit-gutter").length === 4);

toolbar = findAll(root, "tedit-btn");
fireOn(toolbar[1], "click");            // ＋欄
m = api.getModel();
check("add column: one more header", m.headers.length === 3);
check("add column: new header is blank", m.headers[2] === "");
check("add column: aligns kept in step", m.aligns.length === 3 &&
  m.aligns[2] === "");
check("add column: every row is still headers.length wide",
  m.rows.every((r) => r.length === 3));
check("add column: DOM grew too",
  findAll(root, "tedit-colhead").length === 3 &&
  findAll(root, "tedit-cell").length === 12);
check("add column: indent untouched", m.indent === "  ");

// Per-column insert lands to the RIGHT of the clicked column.
fireOn(findAll(root, "tedit-colins")[0], "click");
m = api.getModel();
check("column insert: inserted after the clicked column",
  m.headers.length === 4 && m.headers[0] === MODEL.headers[0] &&
  m.headers[1] === "" && m.headers[2] === MODEL.headers[1]);

// Per-row insert lands BELOW the clicked row.
api.destroy();
api = mk();
root = api.element;
fireOn(findAll(root, "tedit-rowins")[0], "click");
m = api.getModel();
check("row insert: inserted below the clicked row",
  m.rows.length === 3 && m.rows[0][0] === MODEL.rows[0][0] &&
  m.rows[1][0] === "" && m.rows[2][0] === MODEL.rows[1][0]);
api.destroy();

// ---- 5. deleting rows and columns ---------------------------------------
api = mk();
root = api.element;
fireOn(findAll(root, "tedit-rowdel")[0], "click");
m = api.getModel();
check("row delete: row gone", m.rows.length === 1);
same("row delete: the right row survived", m.rows[0], MODEL.rows[1]);

fireOn(findAll(root, "tedit-coldel")[1], "click");
m = api.getModel();
check("column delete: column gone", m.headers.length === 1);
same("column delete: the right column survived",
  m.headers, [MODEL.headers[0]]);
same("column delete: aligns follow", m.aligns, [""]);
check("column delete: rows narrowed", m.rows[0].length === 1);

// The last column is not removable: an empty table is not a table.
fireOn(findAll(root, "tedit-coldel")[0], "click");
m = api.getModel();
check("column delete: the last column is kept", m.headers.length === 1);
check("column delete: grid still standing",
  findAll(root, "tedit-colhead").length === 1);

// Rows may be emptied out entirely; only the header row is mandatory.
fireOn(findAll(root, "tedit-rowdel")[0], "click");
m = api.getModel();
check("row delete: rows may go to zero", m.rows.length === 0);
check("row delete: header row remains",
  findAll(root, "tedit-cell").length === 1);
api.destroy();

// ---- 6. alignment toggling ----------------------------------------------
api = mk();
root = api.element;
let aligns = findAll(root, "tedit-align");   // [c0 l, c0 c, c0 r, c1 l, ...]
check("align: data-align attribute stamped",
  aligns[0].getAttribute("data-align") === "left" &&
  aligns[1].getAttribute("data-align") === "center" &&
  aligns[2].getAttribute("data-align") === "right");
check("align: the model's alignment starts lit",
  hasClass(aligns[4], "is-on") && !hasClass(aligns[0], "is-on"));

fireOn(aligns[0], "click");                  // column 0 -> left
same("align: click sets the column", api.getModel().aligns, ["left", "center"]);
check("align: button lit", hasClass(aligns[0], "is-on"));
check("align: cells follow",
  findAll(root, "tedit-cell")[0].style.textAlign === "left" &&
  findAll(root, "tedit-cell")[2].style.textAlign === "left");

fireOn(aligns[2], "click");                  // column 0 -> right
same("align: another button re-points the column",
  api.getModel().aligns, ["right", "center"]);
check("align: only one button lit per column",
  hasClass(aligns[2], "is-on") && !hasClass(aligns[0], "is-on"));

fireOn(aligns[2], "click");                  // column 0 -> cleared
same("align: clicking the lit button clears it",
  api.getModel().aligns, ["", "center"]);
check("align: nothing lit", !hasClass(aligns[2], "is-on"));
check("align: cells cleared",
  findAll(root, "tedit-cell")[0].style.textAlign === "");
api.destroy();

// ---- 7. keyboard ---------------------------------------------------------
api = mk();
root = api.element;
cells = findAll(root, "tedit-cell");
fireOn(root, "keydown", { target: cells[0], key: "Tab" });
check("tab: moves to the next cell", lastFocused === cells[1]);
fireOn(root, "keydown", { target: cells[1], key: "Tab" });
check("tab: wraps onto the next row", lastFocused === cells[2]);
let ev = fireOn(root, "keydown", { target: cells[2], key: "Tab", shiftKey: true });
check("shift+tab: moves back", lastFocused === cells[1]);
check("tab: default suppressed", ev.defaultPrevented === true);

// Tab out of the very last cell grows the table.
fireOn(root, "keydown", { target: cells[5], key: "Tab" });
m = api.getModel();
check("tab: last cell appends a row", m.rows.length === 3);
same("tab: appended row is blank", m.rows[2], ["", ""]);
check("tab: focus moved into the new row",
  lastFocused === findAll(root, "tedit-cell")[6]);

// Enter walks down the column, and grows the table at the bottom.
cells = findAll(root, "tedit-cell");
fireOn(root, "keydown", { target: cells[1], key: "Enter" });
check("enter: moves down the same column", lastFocused === cells[3]);
fireOn(root, "keydown", { target: cells[7], key: "Enter" });
check("enter: last row appends a row", api.getModel().rows.length === 4);

// Shift+Enter types the literal <br> a pipe cell needs for a line break.
cells = findAll(root, "tedit-cell");
cells[0].textContent = "第一行";
fireOn(root, "keydown", { target: cells[0], key: "Enter", shiftKey: true });
check("shift+enter: literal <br> inserted",
  api.getModel().headers[0] === "第一行<br>");

// A keystroke outside any cell is none of the editor's business.
ev = fireOn(root, "keydown", { target: find(root, "tedit-bar"), key: "Tab" });
check("keydown: ignored outside a cell", ev.defaultPrevented !== true);
api.destroy();

// ---- 8. callbacks --------------------------------------------------------
const fired = [];
api = mk(MODEL, {
  onCommit: () => fired.push("commit"),
  onCancel: () => fired.push("cancel"),
  onToggleRaw: () => fired.push("raw")
});
root = api.element;
cells = findAll(root, "tedit-cell");
fireOn(root, "keydown", { target: cells[0], key: "Enter", ctrlKey: true });
fireOn(root, "keydown", { target: cells[0], key: "Enter", metaKey: true });
fireOn(root, "keydown", { target: cells[0], key: "Escape" });
fireOn(findAll(root, "tedit-btn")[2], "click");
same("callbacks: commit / cancel / raw all fired",
  fired, ["commit", "commit", "cancel", "raw"]);
api.destroy();

// Missing callbacks must not throw.
api = mk(MODEL, {});
root = api.element;
cells = findAll(root, "tedit-cell");
fireOn(root, "keydown", { target: cells[0], key: "Enter", ctrlKey: true });
fireOn(root, "keydown", { target: cells[0], key: "Escape" });
fireOn(findAll(root, "tedit-btn")[2], "click");
check("callbacks: absent handlers are survivable", true);
api.destroy();
api = mk(MODEL, undefined);
check("callbacks: undefined opts survivable", !!api.element);
api.destroy();

// ---- 9. paste ------------------------------------------------------------
api = mk();
root = api.element;
cells = findAll(root, "tedit-cell");
ev = fireOn(root, "paste",
  Object.assign({ target: cells[0] }, clipboard("a\tb\nc\td\ne\tf")));
m = api.getModel();
check("paste: multi-cell paste intercepted", ev.defaultPrevented === true);
same("paste: header row filled", m.headers, ["a", "b"]);
same("paste: body rows filled", m.rows, [["c", "d"], ["e", "f"]]);
check("paste: indent untouched", m.indent === "  ");
api.destroy();

// A paste wider/taller than the table grows it.
api = mk();
root = api.element;
cells = findAll(root, "tedit-cell");
fireOn(root, "paste",
  Object.assign({ target: cells[2] }, clipboard("x\ty\tz\n1\t2\t3\n4\t5\t6")));
m = api.getModel();
check("paste: columns grown", m.headers.length === 3);
check("paste: rows grown", m.rows.length === 3);
same("paste: landed at the pasted-into cell", m.rows[0], ["x", "y", "z"]);
same("paste: last pasted row", m.rows[2], ["4", "5", "6"]);
check("paste: every row still headers.length wide",
  m.rows.every((r) => r.length === 3));
check("paste: header row untouched", m.headers[0] === MODEL.headers[0]);

// Plain text is the browser's job.
cells = findAll(root, "tedit-cell");
ev = fireOn(root, "paste",
  Object.assign({ target: cells[0] }, clipboard("just a word")));
check("paste: plain text not intercepted", ev.defaultPrevented !== true);
check("paste: plain text left the model alone",
  api.getModel().headers[0] === MODEL.headers[0]);

// A paste landing outside a cell is ignored.
ev = fireOn(root, "paste",
  Object.assign({ target: find(root, "tedit-bar") }, clipboard("a\tb")));
check("paste: ignored outside a cell", ev.defaultPrevented !== true);
api.destroy();

// ---- 10. teardown --------------------------------------------------------
api = mk();
root = api.element;
check("destroy: attached before", body.childNodes.indexOf(root) >= 0);
api.destroy();
check("destroy: detached from the parent", root.parentNode === null);
check("destroy: gone from the document",
  body.childNodes.indexOf(root) < 0);
api.destroy();
check("destroy: is idempotent", true);
check("destroy: model still readable", api.getModel().headers.length === 2);
check("style: still a single style node", head.childNodes.length === 1);

// ---- 11. IME composition -------------------------------------------------
// This is a Chinese-first app, so the common case is real: the Enter that
// picks a candidate arrives as a keydown with isComposing set (older Windows
// IMEs report keyCode 229 instead). Acting on it swallows the selection,
// jumps to the next row, and lets render() rebuild the DOM out from under the
// composition -- destroying the characters being typed.
api = mk();
root = api.element;
cells = findAll(root, "tedit-cell");
lastFocused = null;

ev = fireOn(root, "keydown",
  { target: cells[0], key: "Enter", isComposing: true });
check("ime: a composing Enter is left to the IME", ev.defaultPrevented !== true);
check("ime: a composing Enter does not move the focus", lastFocused === null);
check("ime: a composing Enter does not grow the table",
  api.getModel().rows.length === 2);

ev = fireOn(root, "keydown", { target: cells[0], key: "Tab", keyCode: 229 });
check("ime: a keyCode-229 Tab is left to the IME", ev.defaultPrevented !== true);
check("ime: a keyCode-229 Tab does not move the focus", lastFocused === null);

cells[0].textContent = "組字";
fireOn(root, "keydown",
  { target: cells[0], key: "Enter", shiftKey: true, isComposing: true });
check("ime: a composing Shift+Enter types no literal <br>",
  api.getModel().headers[0] === "組字");

// The very same keystrokes still work once the composition has ended.
ev = fireOn(root, "keydown", { target: cells[0], key: "Enter" });
check("ime: a finished Enter still walks down the column",
  ev.defaultPrevented === true && lastFocused === cells[2]);
api.destroy();

// ---- 12. setBusy ---------------------------------------------------------
// The caller freezes the grid while a write is in flight, the way the raw
// editor sets textarea.readOnly: getModel() has already been serialized and
// sent, so anything typed from here on would be dropped without a trace.
api = mk();
root = api.element;
check("busy: setBusy is part of the handle", typeof api.setBusy === "function");

api.setBusy(true);
check("busy: every cell stops being editable",
  findAll(root, "tedit-cell").every((c) => c.contentEditable === "false"));

// A structural edit rebuilds every cell; the rebuilt ones must stay frozen.
fireOn(findAll(root, "tedit-btn")[0], "click");            // ＋列
check("busy: cells rebuilt during a busy spell are frozen too",
  findAll(root, "tedit-cell").every((c) => c.contentEditable === "false"));

api.setBusy(false);
check("busy: clearing it makes every cell editable again",
  findAll(root, "tedit-cell").every(
    (c) => c.contentEditable === "plaintext-only"));
check("busy: the model came through untouched",
  api.getModel().headers[0] === MODEL.headers[0]);

api.destroy();
api.setBusy(true);
check("busy: setBusy after destroy is a no-op", true);

if (failures.length) {
  console.error("FAILED:\n  " + failures.join("\n  "));
  process.exit(1);
}
console.log("OK");
