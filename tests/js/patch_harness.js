/* Drives the pure diff core in assets/patch.js.
 *
 * _diffKeys never touches the DOM, so the stub is nothing but an empty
 * window object for the script to hang its export on. Each case asserts the
 * *whole* operation list, and then -- the part that proves the algorithm
 * rather than the shape of its output -- replays that list over prev and
 * demands the result be exactly next. A seeded random sweep does the same
 * for 500 more pairs. Prints "OK" and exits 0 when everything passes; prints
 * the failures and exits 1 otherwise.
 *
 * Run directly (node tests/js/patch_harness.js) or via tests/test_patch_js.py.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SCRIPT = path.join(__dirname, "..", "..", "assets", "patch.js");

// ---- load the real script ------------------------------------------------
const sandbox = {};
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(SCRIPT, "utf8"), sandbox, { filename: SCRIPT });

const patch = sandbox.window.__mdvPatch;
if (!patch || typeof patch._diffKeys !== "function") {
  console.error("FAILED:\n  window.__mdvPatch._diffKeys was not exported");
  process.exit(1);
}
const diffKeys = patch._diffKeys;

// ---- assertions ----------------------------------------------------------
const failures = [];
function check(label, condition, detail) {
  if (!condition) failures.push(detail ? `${label}\n      ${detail}` : label);
}

// "keep(0,0) replace(1,1)" -- op(prevIndex,nextIndex), "-" for null.
function sig(ops) {
  return ops
    .map((o) => {
      const p = o.prevIndex === null ? "-" : o.prevIndex;
      const n = o.nextIndex === null ? "-" : o.nextIndex;
      return `${o.op}(${p},${n})`;
    })
    .join(" ");
}

function count(ops, kind) {
  return ops.filter((o) => o.op === kind).length;
}

/* Replays an operation list over prev and reports everything wrong with it:
   the rebuilt array not being next, but also the structural promises the
   caller will lean on -- every prev entry spoken for exactly once and in
   order, every next entry produced exactly once and in order, a keep only
   ever over identical content, a remove carrying no nextIndex, and an
   insert anchored on a block that is still standing when the insert runs
   (which means the first later op touching prev must be a keep with that
   very index, or no such op at all when the anchor is null).

   This is the only check that can tell a correct diff from one that merely
   looks plausible, so every case runs it. */
function replay(prev, next, ops) {
  const problems = [];
  const built = [];
  let expectPrev = 0;
  let expectNext = 0;

  ops.forEach((op, at) => {
    const takesPrev =
      op.op === "keep" || op.op === "replace" || op.op === "remove";
    const makesNext =
      op.op === "keep" || op.op === "replace" || op.op === "insert";
    if (!takesPrev && !makesNext) {
      problems.push(`op ${at}: unknown op "${op.op}"`);
      return;
    }
    if (takesPrev) {
      if (op.prevIndex !== expectPrev) {
        problems.push(
          `op ${at} (${op.op}) takes prev ${op.prevIndex}, expected ${expectPrev}`
        );
      }
      expectPrev = op.prevIndex + 1;
    }
    if (makesNext) {
      if (op.nextIndex !== expectNext) {
        problems.push(
          `op ${at} (${op.op}) makes next ${op.nextIndex}, expected ${expectNext}`
        );
      }
      expectNext = op.nextIndex + 1;
    }
    if (op.op === "keep") {
      if (prev[op.prevIndex] !== next[op.nextIndex]) {
        problems.push(
          `op ${at} keeps "${prev[op.prevIndex]}" over "${next[op.nextIndex]}"`
        );
      }
      built.push(prev[op.prevIndex]);
    } else if (op.op === "replace" || op.op === "insert") {
      built.push(next[op.nextIndex]);
    } else if (op.nextIndex !== null) {
      problems.push(`op ${at} is a remove carrying nextIndex ${op.nextIndex}`);
    }
  });

  ops.forEach((op, at) => {
    if (op.op !== "insert") return;
    let anchorOp = null;
    for (let k = at + 1; k < ops.length; k += 1) {
      const later = ops[k];
      if (later.op === "keep" || later.op === "replace" ||
          later.op === "remove") {
        anchorOp = later;
        break;
      }
    }
    if (anchorOp === null) {
      if (op.prevIndex !== null) {
        problems.push(`op ${at} anchors on ${op.prevIndex} with nothing after it`);
      }
    } else if (op.prevIndex !== anchorOp.prevIndex) {
      problems.push(
        `op ${at} anchors on ${op.prevIndex}, the next surviving block is ${anchorOp.prevIndex}`
      );
    } else if (anchorOp.op !== "keep") {
      problems.push(`op ${at} anchors on a block that is ${anchorOp.op}d`);
    }
  });

  if (expectPrev !== prev.length) {
    problems.push(`only ${expectPrev} of ${prev.length} prev blocks accounted for`);
  }
  if (expectNext !== next.length) {
    problems.push(`only ${expectNext} of ${next.length} next blocks produced`);
  }
  if (built.length !== next.length ||
      built.some((key, i) => key !== next[i])) {
    problems.push(
      `replay built ${JSON.stringify(built)}, next is ${JSON.stringify(next)}`
    );
  }
  return problems;
}

/* Diff, assert the full operation list, then replay it. `expected` is the
   sig() string; pass null when the case only demands a legal answer. */
function verify(label, prev, next, expected) {
  const ops = diffKeys(prev, next);
  if (expected !== null) {
    check(`${label}: operation list`, sig(ops) === expected,
      `got      ${sig(ops)}\n      expected ${expected}`);
  }
  const problems = replay(prev, next, ops);
  check(`${label}: replay rebuilds next`, problems.length === 0,
    `${problems.join("\n      ")}\n      ops: ${sig(ops)}`);
  return ops;
}

// ---- the cases -----------------------------------------------------------
// One block edited: the prefix/suffix trim alone should settle it, and the
// blocks either side must survive untouched. This is the case the whole
// feature exists for.
let ops = verify("one block edited", ["A", "B", "C"], ["A", "B2", "C"],
  "keep(0,0) replace(1,1) keep(2,2)");
check("one block edited: exactly one replace", count(ops, "replace") === 1);

ops = verify("one block deleted", ["A", "B", "C"], ["A", "C"],
  "keep(0,0) remove(1,-) keep(2,1)");
check("one block deleted: one remove, nothing replaced",
  count(ops, "remove") === 1 && count(ops, "replace") === 0);

ops = verify("one block inserted", ["A", "C"], ["A", "B", "C"],
  "keep(0,0) insert(1,1) keep(1,2)");
check("one block inserted: one insert, nothing replaced",
  count(ops, "insert") === 1 && count(ops, "replace") === 0);

// Front matter is block zero, so editing it exercises the suffix trim with
// no prefix at all -- and the body below it must not move.
ops = verify("front matter edited", ["FM", "A", "B"], ["FM2", "A", "B"],
  "replace(0,0) keep(1,1) keep(2,2)");
check("front matter edited: only block 0 replaced",
  count(ops, "replace") === 1 && count(ops, "keep") === 2);

ops = verify("inserted at the top", ["A", "B", "C"], ["X", "A", "B", "C"],
  "insert(0,0) keep(0,1) keep(1,2) keep(2,3)");
check("inserted at the top: nothing else touched",
  count(ops, "insert") === 1 && count(ops, "keep") === 3 &&
  count(ops, "replace") === 0 && count(ops, "remove") === 0);

ops = verify("inserted at the end", ["A", "B", "C"], ["A", "B", "C", "X"],
  "keep(0,0) keep(1,1) keep(2,2) insert(-,3)");
check("inserted at the end: appended, not anchored",
  count(ops, "insert") === 1 && ops[3].prevIndex === null);

// Duplicate keys are where a keyed diff normally falls apart: three
// identical B blocks and a new block in front of them must not shuffle the
// Bs around each other.
ops = verify("insert in front of duplicates",
  ["A", "B", "B", "B"], ["X", "A", "B", "B", "B"],
  "insert(0,0) keep(0,1) keep(1,2) keep(2,3) keep(3,4)");
check("insert in front of duplicates: one insert, zero replaces",
  count(ops, "insert") === 1 && count(ops, "replace") === 0);

ops = verify("remove one of several duplicates",
  ["A", "B", "B", "B"], ["A", "B", "B"],
  "keep(0,0) keep(1,1) keep(2,2) remove(3,-)");
check("remove one of several duplicates: one remove, zero replaces",
  count(ops, "remove") === 1 && count(ops, "replace") === 0);

// The suffix trim earns its keep here: the last block is unchanged, but its
// key also turns up earlier in next. A greedy left-to-right match on its own
// hands the surviving <hr> node to the *first* rule and builds a second one
// for the end -- two DOM operations instead of one, and the node the reader
// is looking at moves out from under them. Trimming from the right pins it.
ops = verify("an unchanged last block whose key repeats",
  ["p", "hr"], ["hr", "hr"],
  "replace(0,0) keep(1,1)");
check("an unchanged last block whose key repeats: the last node is reused",
  ops.length === 2 && count(ops, "keep") === 1);

ops = verify("everything rewritten", ["A", "B", "C"], ["D", "E", "F"],
  "replace(0,0) replace(1,1) replace(2,2)");
check("everything rewritten: three replaces, nothing kept",
  count(ops, "replace") === 3 && count(ops, "keep") === 0);

ops = verify("empty to populated", [], ["A", "B"],
  "insert(-,0) insert(-,1)");
check("empty to populated: two inserts", count(ops, "insert") === 2);

ops = verify("populated to empty", ["A", "B"], [],
  "remove(0,-) remove(1,-)");
check("populated to empty: two removes", count(ops, "remove") === 2);

ops = verify("identical", ["A", "B", "C"], ["A", "B", "C"],
  "keep(0,0) keep(1,1) keep(2,2)");
check("identical: nothing but keeps",
  count(ops, "keep") === 3 && ops.length === 3);

// A swap has no cheap answer under a monotonic match and none is demanded:
// what matters is that the list is still legal and still rebuilds next.
ops = verify("swapped order", ["A", "B"], ["B", "A"],
  "remove(0,-) keep(1,0) insert(-,1)");
check("swapped order: every block accounted for", ops.length === 3);

// Keys are content-derived, so a block really can hash to "__proto__".
// Assigning through that name on a bare object reparents the map instead of
// storing anything, and "constructor" reads back a function: without the
// prefix in patch.js this case throws or silently loses matches.
ops = verify("prototype-shaped keys",
  ["__proto__", "constructor", "toString"], ["constructor", "__proto__"],
  "remove(0,-) keep(1,0) replace(2,1)");
check("prototype-shaped keys: the shared block was recognised",
  count(ops, "keep") === 1);

// The trim is what keeps a long document cheap: one edited block in the
// middle of 500 must cost exactly one replace, never a walk of the rest.
const long = [];
for (let i = 0; i < 500; i += 1) long.push("block-" + i);
const longNext = long.slice();
longNext[250] = "block-250-edited";
ops = verify("one edit in a 500 block document", long, longNext, null);
check("one edit in a 500 block document: one replace, 499 keeps",
  count(ops, "replace") === 1 && count(ops, "keep") === 499 &&
  ops[250].op === "replace",
  `replace=${count(ops, "replace")} keep=${count(ops, "keep")}`);

// ---- seeded random sweep -------------------------------------------------
/* mulberry32. Math.random would make any failure impossible to reproduce,
   which is exactly the wrong property for a fuzz test. */
function makeRng(seed) {
  let a = seed >>> 0;
  return function () {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const rng = makeRng(20260824);
function pick(n) {
  return Math.floor(rng() * n);
}

// A tiny alphabet on purpose: duplicate keys are where a keyed diff goes
// wrong, and real documents are full of them (repeated headings, identical
// table rows, horizontal rules). Two prototype names ride along so the
// prefix guard is exercised in combination with everything else.
const ALPHABET = ["A", "B", "C", "D", "__proto__", "constructor"];

function randomKeys(max) {
  const out = [];
  const n = pick(max + 1);
  for (let i = 0; i < n; i += 1) out.push(ALPHABET[pick(ALPHABET.length)]);
  return out;
}

function mutate(keys) {
  const out = keys.slice();
  const edits = 1 + pick(3);
  for (let e = 0; e < edits; e += 1) {
    const kind = out.length === 0 ? 0 : pick(4);
    if (kind === 0) {
      out.splice(pick(out.length + 1), 0, ALPHABET[pick(ALPHABET.length)]);
    } else if (kind === 1) {
      out.splice(pick(out.length), 1);
    } else if (kind === 2) {
      out[pick(out.length)] = ALPHABET[pick(ALPHABET.length)];
    } else if (out.length >= 2) {
      const a = pick(out.length);
      const b = pick(out.length);
      const held = out[a];
      out[a] = out[b];
      out[b] = held;
    }
  }
  return out;
}

let bad = 0;
let firstBad = "";
let changed = 0;   // pairs that were not simply identical
for (let n = 0; n < 500; n += 1) {
  const prev = randomKeys(12);
  // Half realistic (a few edits on top of prev, which is what saving
  // actually produces), half unrelated, which stresses the greedy pass.
  const next = n % 2 === 0 ? mutate(prev) : randomKeys(12);
  const list = diffKeys(prev, next);
  if (list.some((o) => o.op !== "keep")) changed += 1;
  const problems = replay(prev, next, list);
  if (problems.length) {
    bad += 1;
    if (bad === 1) {
      firstBad =
        `case ${n}: ${problems.join("; ")}` +
        `\n      prev=${JSON.stringify(prev)}` +
        `\n      next=${JSON.stringify(next)}` +
        `\n      ops=${sig(list)}`;
    }
  }
}
check("random sweep: all 500 pairs replay to next", bad === 0,
  `${bad} bad pairs; first: ${firstBad}`);
// Guards against a sweep that passes because it generated nothing.
check("random sweep: the pairs actually differed", changed > 400,
  `only ${changed} of 500 pairs needed any change`);

if (failures.length) {
  console.error("FAILED:\n  " + failures.join("\n  "));
  process.exit(1);
}
console.log("OK");
