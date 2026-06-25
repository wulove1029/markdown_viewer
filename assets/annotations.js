(function () {
  "use strict";

  var COLORS = ["#ffd54f", "#a5d6a7", "#90caf9", "#f48fb1", "#ce93d8"];
  var bridge = null;
  var toolbar = null;

  // ---- text/node mapping -------------------------------------------------
  function buildMap() {
    var walker = document.createTreeWalker(
      document.body, NodeFilter.SHOW_TEXT, {
        acceptNode: function (n) {
          if (!n.nodeValue) return NodeFilter.FILTER_REJECT;
          var p = n.parentNode;
          if (p && p.closest &&
              p.closest("script,style,.annot-toolbar")) {
            return NodeFilter.FILTER_REJECT;
          }
          return NodeFilter.FILTER_ACCEPT;
        }
      });
    var nodes = [], offsets = [], text = "", n;
    while ((n = walker.nextNode())) {
      offsets.push(text.length);
      nodes.push(n);
      text += n.nodeValue;
    }
    return { nodes: nodes, offsets: offsets, text: text };
  }

  function nodeBase(map, node) {
    for (var i = 0; i < map.nodes.length; i++) {
      if (map.nodes[i] === node) return map.offsets[i];
    }
    return -1;
  }

  function offsetsOfRange(map, range) {
    var sb = nodeBase(map, range.startContainer);
    var eb = nodeBase(map, range.endContainer);
    if (sb < 0 || eb < 0) return null;
    return { start: sb + range.startOffset, end: eb + range.endOffset };
  }

  function rangeFromOffsets(map, start, end) {
    var sNode = null, sOff = 0, eNode = null, eOff = 0;
    for (var i = 0; i < map.nodes.length; i++) {
      var base = map.offsets[i];
      var len = map.nodes[i].nodeValue.length;
      if (sNode === null && start >= base && start < base + len) {
        sNode = map.nodes[i]; sOff = start - base;
      }
      if (end > base && end <= base + len) {
        eNode = map.nodes[i]; eOff = end - base; break;
      }
    }
    if (!sNode || !eNode) return null;
    var r = document.createRange();
    r.setStart(sNode, sOff); r.setEnd(eNode, eOff);
    return r;
  }

  function resolveStart(map, ann) {
    if (!ann.exact) return -1;
    var text = map.text, best = -1, bestScore = -1;
    var idx = text.indexOf(ann.exact);
    while (idx !== -1) {
      var before = text.slice(Math.max(0, idx - ann.prefix.length), idx);
      var after = text.slice(idx + ann.exact.length,
                             idx + ann.exact.length + ann.suffix.length);
      var score = 0;
      if (ann.prefix && before.endsWith(ann.prefix)) score += 2;
      if (ann.suffix && after.startsWith(ann.suffix)) score += 2;
      score += 1 - Math.min(1, Math.abs(idx - ann.textPosition) / 1000);
      if (score > bestScore) { bestScore = score; best = idx; }
      idx = text.indexOf(ann.exact, idx + 1);
    }
    return best;
  }

  // ---- mark rendering ----------------------------------------------------
  function wrapRange(range, ann) {
    var nodes = [];
    var walker = document.createTreeWalker(
      range.commonAncestorContainer, NodeFilter.SHOW_TEXT, {
        acceptNode: function (n) {
          return range.intersectsNode(n)
            ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
        }
      });
    var n;
    while ((n = walker.nextNode())) nodes.push(n);
    if (nodes.length === 0 && range.startContainer.nodeType === 3) {
      nodes.push(range.startContainer);
    }
    nodes.forEach(function (node) {
      var s = (node === range.startContainer) ? range.startOffset : 0;
      var e = (node === range.endContainer) ? range.endOffset
                                            : node.nodeValue.length;
      if (e <= s) return;
      var sub = document.createRange();
      sub.setStart(node, s); sub.setEnd(node, e);
      var mark = document.createElement("mark");
      mark.className = "annot";
      mark.setAttribute("data-id", ann.id);
      mark.style.background = ann.color;
      try { sub.surroundContents(mark); } catch (err) { /* skip */ }
    });
  }

  function unwrap(id) {
    var marks = document.querySelectorAll('mark.annot[data-id="' + id + '"]');
    marks.forEach(function (m) {
      var parent = m.parentNode;
      while (m.firstChild) parent.insertBefore(m.firstChild, m);
      parent.removeChild(m);
      parent.normalize();
    });
  }

  // ---- selection toolbar -------------------------------------------------
  function hideToolbar() { if (toolbar) toolbar.style.display = "none"; }

  function ensureToolbar() {
    if (toolbar) return toolbar;
    toolbar = document.createElement("div");
    toolbar.className = "annot-toolbar";
    toolbar.style.display = "none";
    COLORS.forEach(function (c) {
      var sw = document.createElement("button");
      sw.className = "annot-swatch";
      sw.style.background = c;
      sw.title = "高亮";
      sw.addEventListener("mousedown", function (ev) {
        ev.preventDefault();
        createFromSelection(c);
      });
      toolbar.appendChild(sw);
    });
    document.body.appendChild(toolbar);
    return toolbar;
  }

  function showToolbar(rect) {
    var t = ensureToolbar();
    t.style.display = "flex";
    var top = window.scrollY + rect.top - t.offsetHeight - 8;
    if (top < window.scrollY) top = window.scrollY + rect.bottom + 8;
    t.style.top = top + "px";
    t.style.left = (window.scrollX + rect.left) + "px";
  }

  function createFromSelection(color) {
    var sel = window.getSelection();
    if (!sel || sel.isCollapsed) return;
    var range = sel.getRangeAt(0);
    var map = buildMap();
    var info = offsetsOfRange(map, range);
    if (!info || info.end <= info.start) { hideToolbar(); return; }
    var ann = {
      id: (crypto.randomUUID ? crypto.randomUUID() :
           String(Date.now()) + Math.random()).replace(/-/g, ""),
      exact: map.text.slice(info.start, info.end),
      prefix: map.text.slice(Math.max(0, info.start - 32), info.start),
      suffix: map.text.slice(info.end, info.end + 32),
      textPosition: info.start,
      color: color, note: "", tags: []
    };
    wrapRange(range, ann);
    sel.removeAllRanges();
    hideToolbar();
    if (bridge) bridge.add(JSON.stringify(ann));
  }

  // ---- public API (called from Python) -----------------------------------
  window.__annot = {
    render: function (jsonStr) {
      var list;
      try { list = JSON.parse(jsonStr || "[]"); } catch (e) { list = []; }
      var map = buildMap();
      var orphans = [];
      list.forEach(function (ann) {
        var start = resolveStart(map, ann);
        if (start < 0) { orphans.push(ann.id); return; }
        var range = rangeFromOffsets(map, start, start + ann.exact.length);
        if (!range) { orphans.push(ann.id); return; }
        wrapRange(range, ann);
      });
      if (bridge) bridge.reportOrphans(JSON.stringify(orphans));
    },
    remove: function (id) { unwrap(id); },
    updateColor: function (id, color) {
      document.querySelectorAll('mark.annot[data-id="' + id + '"]')
        .forEach(function (m) { m.style.background = color; });
    },
    scrollTo: function (id) {
      var m = document.querySelector('mark.annot[data-id="' + id + '"]');
      if (m) m.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  };

  window.__annotBoot = function (jsonStr) {
    function afterChannel() { window.__annot.render(jsonStr); }
    if (typeof QWebChannel !== "undefined" && window.qt &&
        qt.webChannelTransport) {
      new QWebChannel(qt.webChannelTransport, function (channel) {
        bridge = channel.objects.bridge;
        afterChannel();
      });
    } else {
      afterChannel();
    }
  };

  document.addEventListener("mouseup", function (e) {
    if (e.target.closest && e.target.closest(".annot-toolbar")) return;
    var sel = window.getSelection();
    if (!sel || sel.isCollapsed || !sel.toString().trim()) {
      hideToolbar();
      return;
    }
    var rect = sel.getRangeAt(0).getBoundingClientRect();
    showToolbar(rect);
  });

  document.addEventListener("mousedown", function (e) {
    var m = e.target.closest && e.target.closest("mark.annot");
    if (m && bridge) bridge.clickedAnnotation(m.getAttribute("data-id"));
  });
})();
