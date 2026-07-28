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
let pasteReply = { ok: true, link: "![](assets/image-1.png)" };

const bridge = {
  inlineEditFetch(start, end, cb) {
    calls.push(["fetch", start, end]);
    cb(JSON.stringify({ ok: true, text: sourceText }));
  },
  inlineEditCommit(start, end, original, next, cb) {
    calls.push(["commit", start, end, original, next]);
    cb(JSON.stringify(commitReply));
  },
  inlineEditPasteImage(cb) {
    calls.push(["paste"]);
    cb(JSON.stringify(pasteReply));
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

function makeBlock(start, end) {
  const block = El("p", {
    attrs: { "data-src-start": String(start), "data-src-end": String(end) }
  });
  body.appendChild(block);
  return block;
}

function textareaOf(block) {
  const wrapper = block.nextSibling;
  return wrapper && wrapper.className.indexOf("inline-edit") === 0
    ? wrapper.childNodes[0]
    : null;
}

// Disabled: a double-click must do nothing at all.
sandbox.window.__inlineEditBoot(bridge, false);
let block = makeBlock(2, 3);
fire("dblclick", { target: block });
check("disabled: no bridge call", calls.length === 0);
check("disabled: no textarea", textareaOf(block) === null);

// Enabled: the block is hidden and replaced by a textarea holding the source.
sandbox.window.__inlineEdit.setEnabled(true);
fire("dblclick", { target: block });
let ta = textareaOf(block);
check("open: fetch called with the block range",
  JSON.stringify(calls[0]) === JSON.stringify(["fetch", 2, 3]));
check("open: textarea holds the raw source", ta && ta.value === "alpha\nbeta");
check("open: block hidden", block.style.display === "none");

// A second double-click while editing must not stack editors.
const callsBefore = calls.length;
fire("dblclick", { target: makeBlock(9, 9) });
check("open: no re-entry while editing", calls.length === callsBefore);

// Esc restores the rendering and never reaches the bridge.
fire("keydown", { target: ta, key: "Escape" });
check("cancel: block restored", block.style.display === "");
check("cancel: textarea gone", textareaOf(block) === null);
check("cancel: nothing committed", calls.length === callsBefore);

// Ctrl+Enter commits the edited text.
fire("dblclick", { target: block });
ta = textareaOf(block);
ta.value = "alpha edited";
fire("keydown", { target: ta, key: "Enter", ctrlKey: true });
const commit = calls[calls.length - 1];
check("commit: bridge got the range, original and new text",
  JSON.stringify(commit) ===
  JSON.stringify(["commit", 2, 3, "alpha\nbeta", "alpha edited"]));
check("commit: editor closed", textareaOf(block) === null);

// An unchanged commit is a no-op, not a write.
fire("dblclick", { target: block });
ta = textareaOf(block);
const beforeNoop = calls.length;
fire("keydown", { target: ta, key: "Enter", ctrlKey: true });
check("commit: unchanged text is not written", calls.length === beforeNoop);
check("commit: unchanged still closes", textareaOf(block) === null);

// Clicking outside commits; clicking inside does not.
fire("dblclick", { target: block });
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
fire("dblclick", { target: block });
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
fire("dblclick", { target: mark });
check("guard: annotation double-click not hijacked", calls.length === guarded);
check("guard: no editor over an annotation", textareaOf(annotated) === null);

const taskList = makeBlock(30, 31);
const checkbox = El("input", { className: "task-list-item-checkbox" });
taskList.appendChild(checkbox);
fire("dblclick", { target: checkbox });
check("guard: task checkbox not hijacked", calls.length === guarded);

const untagged = El("p");
body.appendChild(untagged);
fire("dblclick", { target: untagged });
check("guard: block without a source range ignored", calls.length === guarded);

// Disabling mid-edit tears the editor down.
fire("dblclick", { target: block });
check("disable: editor open before disabling", textareaOf(block) !== null);
sandbox.window.__inlineEdit.setEnabled(false);
check("disable: editor torn down", textareaOf(block) === null);
check("disable: block visible again", block.style.display === "");

if (failures.length) {
  console.error("FAILED:\n  " + failures.join("\n  "));
  process.exit(1);
}
console.log("OK");
