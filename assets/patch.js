/* Block-level diff for the "save without repainting the whole page" path.
 *
 * Saving hands the page a freshly rendered body. Swapping the old body out
 * wholesale is the easy way to apply it and the wrong one: every image
 * reloads, the scroll position and any open annotation popup die with the
 * old nodes, and a one-word edit costs a full repaint of a long document.
 * The alternative is to compare the top-level blocks the renderer just
 * produced against the ones already on screen and touch only what changed.
 *
 * This file is the *decision* half of that -- the pure function that says
 * which blocks to keep, replace, insert and remove. It deliberately never
 * touches the DOM, so it can be reasoned about and tested on its own; the
 * caller collects the keys, calls _diffKeys, and carries the operations out.
 *
 * KEY PREMISE, and the whole reason any of this is worth doing: a block's
 * key is derived from its *content*, with the source line numbers
 * deliberately stripped out. Inserting a single line shifts the
 * data-src-start / data-src-end that md_converter stamps on every block
 * below it, so a key carrying line numbers would make every following block
 * compare unequal -- one small edit would replace the entire document and
 * the patch would be nothing but a slower full repaint. With line numbers
 * out of the key, a block whose text did not change is *guaranteed* to land
 * in "keep"; re-stamping its shifted line numbers is a separate, cheap step
 * the caller owns.
 *
 * Two layers, chosen for speed and for predictability:
 *   1. Trim the common prefix and the common suffix. O(n), and the ordinary
 *      case -- one block edited -- never gets past it.
 *   2. Match whatever is left with a single monotonic greedy pass over a
 *      key -> prev-index queue.
 * Deliberately NOT a full Myers/LCS diff: O(n*m) is a real cost on a long
 * document, and the worst a greedy mismatch can do is replace a few more
 * nodes than strictly necessary -- it can never produce a wrong result.
 * Every operation list this returns rebuilds `next` exactly.
 */
(function () {
  /* Every key is prefixed before it is used as a property name. A block
     whose content-derived key is "__proto__" or "constructor" would
     otherwise reach into Object.prototype: assigning through __proto__
     silently reparents the map instead of storing anything, and reading
     "constructor" hands back a function where a queue was expected. The
     prefix moves every key into a namespace Object.prototype is empty in. */
  var KEY_PREFIX = "k";

  function hasOwn(obj, name) {
    return Object.prototype.hasOwnProperty.call(obj, name);
  }

  function makeOp(op, prevIndex, nextIndex) {
    return { op: op, prevIndex: prevIndex, nextIndex: nextIndex };
  }

  /* key -> { list: [prev index, ...], at: how far the scan has consumed }.
     Only the middle slice is indexed; the trimmed ends are already settled
     and indexing them would be pure waste. */
  function buildIndex(keys, from, to) {
    var index = {};
    var i, name;
    for (i = from; i < to; i += 1) {
      name = KEY_PREFIX + keys[i];
      if (hasOwn(index, name)) index[name].list.push(i);
      else index[name] = { list: [i], at: 0 };
    }
    return index;
  }

  /* The first prev index for `key` that is unused and not behind minIndex,
     or -1 when there is none.

     Monotonic on purpose: minIndex only ever grows, so an index too far
     left for this next-entry is too far left for every later one as well
     and can be dropped for good. That is what keeps the scan linear rather
     than quadratic, and it is also what makes a reordered document cost a
     couple of extra replaces instead of a tangle of crossing moves -- still
     correct, just not minimal, which is the trade this file is making. */
  function takeMatch(index, key, minIndex) {
    var name = KEY_PREFIX + key;
    if (!hasOwn(index, name)) return -1;
    var slot = index[name];
    var found;
    while (slot.at < slot.list.length && slot.list[slot.at] < minIndex) {
      slot.at += 1;
    }
    if (slot.at >= slot.list.length) return -1;
    found = slot.list[slot.at];
    slot.at += 1;
    return found;
  }

  /* One run of unmatched prev entries and one run of unmatched next entries,
     sitting between two matched blocks. Zipping them pairs the k-th doomed
     prev block with the k-th new next block as a single `replace`: that is
     the "an adjacent remove + insert collapse into a replace" rule, done
     positionally so the pairing can never cross over (three removes followed
     by three inserts must not pair the last insert with the first remove).
     Whichever run is longer spills its remainder out as plain removes or
     plain inserts, so a segment never holds both -- which is what lets an
     insert's anchor always be a surviving block. */
  function emitSegment(ops, prevFrom, prevTo, nextFrom, nextTo) {
    var prevCount = prevTo - prevFrom;
    var nextCount = nextTo - nextFrom;
    var paired = prevCount < nextCount ? prevCount : nextCount;
    var t;
    for (t = 0; t < paired; t += 1) {
      ops.push(makeOp("replace", prevFrom + t, nextFrom + t));
    }
    for (t = paired; t < prevCount; t += 1) {
      ops.push(makeOp("remove", prevFrom + t, null));
    }
    for (t = paired; t < nextCount; t += 1) {
      ops.push(makeOp("insert", null, nextFrom + t));
    }
  }

  /* An insert brings no prev entry of its own, so it needs an anchor: the
     prev index it goes *before*, or null for "append at the end". Filled in
     backwards, because the anchor always lies to the right of the insert.
     Removes are skipped -- a node about to leave the document cannot be
     inserted before. By construction the anchor lands on a `keep` (see
     emitSegment: inserts come last in a segment, so the only thing that can
     follow a run of them is the matched block that closed it), which means
     the anchor node is still in place whatever order the caller applies the
     operations in. */
  function stampAnchors(ops) {
    var anchor = null;
    var i, op;
    for (i = ops.length - 1; i >= 0; i -= 1) {
      op = ops[i];
      if (op.op === "insert") op.prevIndex = anchor;
      else if (op.op === "keep" || op.op === "replace") anchor = op.prevIndex;
    }
  }

  /* prevKeys / nextKeys are arrays of key strings, one per top-level block.
     Returns [{op, prevIndex, nextIndex}, ...] in `next` order:
       keep    prevIndex, nextIndex  -- same content, reuse the node as is
       replace prevIndex, nextIndex  -- swap prev's node for next's
       insert  prevIndex = anchor or null, nextIndex -- a brand new node
       remove  prevIndex, nextIndex = null -- drop the node
     Read the list left to right, skipping the removes, and it spells out
     `next` exactly; every prev index appears exactly once, in order. */
  function diffKeys(prevKeys, nextKeys) {
    var prev = prevKeys || [];
    var next = nextKeys || [];
    var prevLen = prev.length;
    var nextLen = next.length;
    var shortest = prevLen < nextLen ? prevLen : nextLen;
    var ops = [];
    var head = 0;
    var tail = 0;
    var prevFrom, prevTo, nextFrom, nextTo;
    var index, matchFor, minIndex, pendingPrev, pendingNext;
    var i, j, matched;

    // ---- layer 1: common prefix, common suffix ------------------------
    while (head < shortest && prev[head] === next[head]) head += 1;

    // `shortest - head` stops the two runs from overlapping when one array
    // extends the other: [A] -> [A, A] must trim one end, not both, or the
    // middle would come out with a negative length.
    while (tail < shortest - head &&
           prev[prevLen - 1 - tail] === next[nextLen - 1 - tail]) {
      tail += 1;
    }

    for (i = 0; i < head; i += 1) ops.push(makeOp("keep", i, i));

    // ---- layer 2: monotonic greedy keyed match over the middle --------
    prevFrom = head;
    prevTo = prevLen - tail;
    nextFrom = head;
    nextTo = nextLen - tail;

    index = buildIndex(prev, prevFrom, prevTo);
    matchFor = [];   // one entry per next-middle key: a prev index, or -1
    minIndex = prevFrom;
    for (j = nextFrom; j < nextTo; j += 1) {
      matched = takeMatch(index, next[j], minIndex);
      matchFor.push(matched);
      if (matched >= 0) minIndex = matched + 1;
    }

    // The matches become keeps; everything between two of them is a segment.
    pendingPrev = prevFrom;
    pendingNext = nextFrom;
    for (j = nextFrom; j < nextTo; j += 1) {
      matched = matchFor[j - nextFrom];
      if (matched < 0) continue;
      emitSegment(ops, pendingPrev, matched, pendingNext, j);
      ops.push(makeOp("keep", matched, j));
      pendingPrev = matched + 1;
      pendingNext = j + 1;
    }
    emitSegment(ops, pendingPrev, prevTo, pendingNext, nextTo);

    for (i = 0; i < tail; i += 1) {
      ops.push(makeOp("keep", prevTo + i, nextTo + i));
    }

    stampAnchors(ops);
    return ops;
  }

  window.__mdvPatch = { _diffKeys: diffKeys };
})();
