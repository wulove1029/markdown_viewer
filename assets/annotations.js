(function () {
  "use strict";

  var COLORS = ["#ffd54f", "#a5d6a7", "#90caf9", "#f48fb1", "#ce93d8"];
  var bridge = null;
  var toolbar = null;
  var rail = null;
  var popover = null;
  var editor = null;
  var contextMenu = null;
  var annotations = [];
  var annotationById = {};
  var renderedIds = {};
  var activeId = "";
  var layoutFrame = 0;
  var sideNotesVisible = false;

  // ---- text/node mapping -------------------------------------------------
  function buildMap() {
    var walker = document.createTreeWalker(
      document.body, NodeFilter.SHOW_TEXT, {
        acceptNode: function (n) {
          if (!n.nodeValue) return NodeFilter.FILTER_REJECT;
          var p = n.parentNode;
          if (p && p.closest &&
              p.closest("script,style,.annot-toolbar,.annot-rail,.annot-popover,.annot-editor,.annot-menu")) {
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

  function unwrapAll() {
    document.querySelectorAll("mark.annot").forEach(function (m) {
      var parent = m.parentNode;
      while (m.firstChild) parent.insertBefore(m.firstChild, m);
      parent.removeChild(m);
      parent.normalize();
    });
  }

  function textFor(ann) {
    var note = (ann.note || "").trim();
    return note || "尚未新增備註";
  }

  function shortText(text, max) {
    text = (text || "").replace(/\s+/g, " ").trim();
    if (text.length <= max) return text;
    return text.slice(0, max - 1) + "…";
  }

  function ensureRail() {
    if (rail && rail.parentNode) return rail;
    rail = document.createElement("aside");
    rail.className = "annot-rail";
    rail.setAttribute("aria-label", "標註旁註");
    document.body.appendChild(rail);
    return rail;
  }

  function ensurePopover() {
    if (popover && popover.parentNode) return popover;
    popover = document.createElement("div");
    popover.className = "annot-popover";
    popover.style.display = "none";
    document.body.appendChild(popover);
    return popover;
  }

  function clearPopover() {
    if (popover) popover.style.display = "none";
  }

  function closeEditor() {
    if (editor && editor.parentNode) editor.parentNode.removeChild(editor);
    editor = null;
  }

  function closeContextMenu() {
    if (contextMenu && contextMenu.parentNode) {
      contextMenu.parentNode.removeChild(contextMenu);
    }
    contextMenu = null;
  }

  function removeAnnotationLocal(id) {
    unwrap(id);
    delete annotationById[id];
    delete renderedIds[id];
    annotations = annotations.filter(function (ann) { return ann.id !== id; });
    if (activeId === id) activeId = "";
    closeEditor();
    closeContextMenu();
    clearPopover();
    renderSideNotes();
    setActive(activeId);
  }

  function requestDeleteAnnotation(id) {
    if (!annotationById[id]) return;
    removeAnnotationLocal(id);
    if (bridge) bridge.remove(id);
  }

  function buildNoteCard(ann) {
    var card = document.createElement("button");
    card.type = "button";
    card.className = "annot-card";
    card.setAttribute("data-id", ann.id);
    card.style.setProperty("--annot-color", ann.color || COLORS[0]);

    var quote = document.createElement("div");
    quote.className = "annot-card-quote";
    quote.textContent = shortText(ann.exact, 84);

    var note = document.createElement("div");
    note.className = "annot-card-note";
    note.textContent = textFor(ann);

    card.appendChild(quote);
    card.appendChild(note);

    if (ann.tags && ann.tags.length) {
      var tags = document.createElement("div");
      tags.className = "annot-card-tags";
      ann.tags.forEach(function (tag) {
        var pill = document.createElement("span");
        pill.textContent = "#" + tag;
        tags.appendChild(pill);
      });
      card.appendChild(tags);
    }

    card.addEventListener("click", function () {
      activateAnnotation(ann.id, true, true);
    });
    card.addEventListener("dblclick", function (ev) {
      ev.preventDefault();
      ev.stopPropagation();
      openNoteEditor(ann.id, card);
    });
    return card;
  }

  function renderSideNotes() {
    var visible = annotations.filter(function (ann) {
      return renderedIds[ann.id];
    });
    if (!sideNotesVisible || visible.length === 0) {
      document.body.classList.remove("annot-side-notes");
      if (rail) rail.remove();
      rail = null;
      clearPopover();
      if (visible.length === 0) closeEditor();
      return;
    }

    document.body.classList.add("annot-side-notes");
    var r = ensureRail();
    r.innerHTML = "";
    visible.forEach(function (ann) {
      r.appendChild(buildNoteCard(ann));
    });
    scheduleSideNoteLayout();
    window.setTimeout(scheduleSideNoteLayout, 120);
    window.setTimeout(scheduleSideNoteLayout, 500);
  }

  function scheduleSideNoteLayout() {
    if (layoutFrame) return;
    layoutFrame = window.requestAnimationFrame(function () {
      layoutFrame = 0;
      layoutSideNotes();
    });
  }

  function layoutSideNotes() {
    if (!rail) return;
    var bodyTop = document.body.getBoundingClientRect().top + window.scrollY;
    var items = [];
    rail.querySelectorAll(".annot-card").forEach(function (card) {
      var id = card.getAttribute("data-id");
      var mark = document.querySelector('mark.annot[data-id="' + id + '"]');
      if (!mark) return;
      var rect = mark.getBoundingClientRect();
      items.push({
        card: card,
        y: Math.max(0, rect.top + window.scrollY - bodyTop - 6)
      });
    });

    items.sort(function (a, b) { return a.y - b.y; });
    var nextY = 0;
    items.forEach(function (item) {
      var y = Math.max(item.y, nextY);
      item.card.style.top = y + "px";
      nextY = y + item.card.offsetHeight + 8;
    });
  }

  function setActive(id) {
    activeId = id || "";
    document.querySelectorAll("mark.annot.is-active")
      .forEach(function (m) { m.classList.remove("is-active"); });
    document.querySelectorAll(".annot-card.is-active")
      .forEach(function (c) { c.classList.remove("is-active"); });
    if (!activeId) return;
    document.querySelectorAll('mark.annot[data-id="' + activeId + '"]')
      .forEach(function (m) { m.classList.add("is-active"); });
    var card = document.querySelector('.annot-card[data-id="' + activeId + '"]');
    if (card) card.classList.add("is-active");
  }

  function activateAnnotation(id, notify, scroll) {
    setActive(id);
    if (scroll) {
      var m = document.querySelector('mark.annot[data-id="' + id + '"]');
      if (m) m.scrollIntoView({ behavior: "smooth", block: "center" });
    }
    if (notify && bridge) bridge.clickedAnnotation(id);
  }

  function showPopover(id, target) {
    if (editor) return;
    var ann = annotationById[id];
    if (!ann) return;
    var p = ensurePopover();
    p.innerHTML = "";
    p.style.setProperty("--annot-color", ann.color || COLORS[0]);

    var quote = document.createElement("div");
    quote.className = "annot-popover-quote";
    quote.textContent = shortText(ann.exact, 72);
    var note = document.createElement("div");
    note.className = "annot-popover-note";
    note.textContent = textFor(ann);
    p.appendChild(quote);
    p.appendChild(note);

    var rect = target.getBoundingClientRect();
    p.style.display = "block";
    var top = window.scrollY + rect.bottom + 8;
    var left = window.scrollX + rect.left;
    var maxLeft = window.scrollX + document.documentElement.clientWidth -
                  p.offsetWidth - 14;
    p.style.top = top + "px";
    p.style.left = Math.max(14, Math.min(left, maxLeft)) + "px";
  }

  function positionFloatingPanel(panel, target) {
    var rect = target.getBoundingClientRect();
    panel.style.display = "block";
    var top = window.scrollY + rect.bottom + 10;
    var left = window.scrollX + rect.left;
    var maxLeft = window.scrollX + document.documentElement.clientWidth -
                  panel.offsetWidth - 14;
    panel.style.top = top + "px";
    panel.style.left = Math.max(14, Math.min(left, maxLeft)) + "px";
  }

  function saveNote(id, value) {
    var ann = annotationById[id];
    if (!ann) return;
    ann.note = value;
    renderSideNotes();
    setActive(id);
    closeEditor();
    if (bridge) bridge.update(id, JSON.stringify({ note: value }));
  }

  function openNoteEditor(id, target) {
    var ann = annotationById[id];
    if (!ann) return;
    clearPopover();
    closeEditor();
    activateAnnotation(id, true, false);

    editor = document.createElement("div");
    editor.className = "annot-editor";
    editor.style.setProperty("--annot-color", ann.color || COLORS[0]);
    editor.addEventListener("mousedown", function (ev) {
      ev.stopPropagation();
    });
    editor.addEventListener("click", function (ev) {
      ev.stopPropagation();
    });

    var quote = document.createElement("div");
    quote.className = "annot-editor-quote";
    quote.textContent = shortText(ann.exact, 86);

    var input = document.createElement("textarea");
    input.className = "annot-editor-input";
    input.placeholder = "輸入備註...";
    input.value = ann.note || "";

    var hint = document.createElement("div");
    hint.className = "annot-editor-hint";
    hint.textContent = "Ctrl+Enter 儲存，Esc 取消";

    var actions = document.createElement("div");
    actions.className = "annot-editor-actions";

    var cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "annot-editor-cancel";
    cancel.textContent = "取消";
    cancel.addEventListener("click", closeEditor);

    var save = document.createElement("button");
    save.type = "button";
    save.className = "annot-editor-save";
    save.textContent = "儲存";
    save.addEventListener("click", function () {
      saveNote(id, input.value);
    });

    actions.appendChild(cancel);
    actions.appendChild(save);
    editor.appendChild(quote);
    editor.appendChild(input);
    editor.appendChild(hint);
    editor.appendChild(actions);
    document.body.appendChild(editor);
    positionFloatingPanel(editor, target);

    input.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape") {
        ev.preventDefault();
        closeEditor();
      } else if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) {
        ev.preventDefault();
        saveNote(id, input.value);
      }
    });
    input.focus();
    input.select();
  }

  function positionMenuAt(menu, x, y) {
    menu.style.display = "block";
    var maxLeft = window.scrollX + document.documentElement.clientWidth -
                  menu.offsetWidth - 10;
    var maxTop = window.scrollY + document.documentElement.clientHeight -
                 menu.offsetHeight - 10;
    menu.style.left = Math.max(10, Math.min(x, maxLeft)) + "px";
    menu.style.top = Math.max(10, Math.min(y, maxTop)) + "px";
  }

  function openAnnotationMenu(id, x, y, target) {
    var ann = annotationById[id];
    if (!ann) return;
    clearPopover();
    closeContextMenu();
    activateAnnotation(id, true, false);

    contextMenu = document.createElement("div");
    contextMenu.className = "annot-menu";
    contextMenu.style.setProperty("--annot-color", ann.color || COLORS[0]);
    contextMenu.addEventListener("mousedown", function (ev) {
      ev.stopPropagation();
    });
    contextMenu.addEventListener("click", function (ev) {
      ev.stopPropagation();
    });

    var edit = document.createElement("button");
    edit.type = "button";
    edit.textContent = "編輯備註";
    edit.addEventListener("click", function () {
      closeContextMenu();
      openNoteEditor(id, target);
    });

    var del = document.createElement("button");
    del.type = "button";
    del.className = "is-danger";
    del.textContent = "刪除標註";
    del.addEventListener("click", function () {
      requestDeleteAnnotation(id);
    });

    contextMenu.appendChild(edit);
    contextMenu.appendChild(del);
    document.body.appendChild(contextMenu);
    positionMenuAt(contextMenu, x, y);
  }

  function isTypingTarget(target) {
    if (!target) return false;
    var tag = (target.tagName || "").toLowerCase();
    return tag === "input" || tag === "textarea" || tag === "select" ||
           target.isContentEditable ||
           !!(target.closest && target.closest(".annot-editor"));
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
    annotationById[ann.id] = ann;
    renderedIds[ann.id] = true;
    annotations.push(ann);
    renderSideNotes();
    setActive(ann.id);
    sel.removeAllRanges();
    hideToolbar();
    if (bridge) bridge.add(JSON.stringify(ann));
  }

  // ---- public API (called from Python) -----------------------------------
  window.__annot = {
    render: function (jsonStr) {
      var list;
      try { list = JSON.parse(jsonStr || "[]"); } catch (e) { list = []; }
      unwrapAll();
      annotations = list;
      annotationById = {};
      renderedIds = {};
      list.forEach(function (ann) { annotationById[ann.id] = ann; });
      var map = buildMap();
      var orphans = [];
      list.forEach(function (ann) {
        var start = resolveStart(map, ann);
        if (start < 0) { orphans.push(ann.id); return; }
        var range = rangeFromOffsets(map, start, start + ann.exact.length);
        if (!range) { orphans.push(ann.id); return; }
        wrapRange(range, ann);
        renderedIds[ann.id] = true;
      });
      renderSideNotes();
      setActive(activeId);
      if (bridge) bridge.reportOrphans(JSON.stringify(orphans));
    },
    remove: function (id) {
      removeAnnotationLocal(id);
    },
    updateColor: function (id, color) {
      document.querySelectorAll('mark.annot[data-id="' + id + '"]')
        .forEach(function (m) { m.style.background = color; });
      if (annotationById[id]) annotationById[id].color = color;
      document.querySelectorAll('.annot-card[data-id="' + id + '"]')
        .forEach(function (c) { c.style.setProperty("--annot-color", color); });
    },
    scrollTo: function (id) {
      activateAnnotation(id, false, true);
    },
    select: function (id) {
      activateAnnotation(id, false, false);
    },
    setSideNotesVisible: function (visible) {
      sideNotesVisible = !!visible;
      renderSideNotes();
      setActive(activeId);
    }
  };

  window.__annotBoot = function (jsonStr, sideNotes) {
    sideNotesVisible = !!sideNotes;
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
    if (!(e.target.closest && e.target.closest(".annot-menu"))) {
      closeContextMenu();
    }
    var m = e.target.closest && e.target.closest("mark.annot");
    if (m) activateAnnotation(m.getAttribute("data-id"), true, false);
  });

  // Persist task-list checkbox toggles back to the Markdown source.
  document.addEventListener("change", function (e) {
    var cb = e.target;
    if (!cb || !cb.classList ||
        !cb.classList.contains("task-list-item-checkbox")) {
      return;
    }
    var line = parseInt(cb.getAttribute("data-line"), 10);
    if (isNaN(line)) return;
    if (bridge && bridge.toggleTask) bridge.toggleTask(line, !!cb.checked);
  });

  document.addEventListener("contextmenu", function (e) {
    var m = e.target.closest && e.target.closest("mark.annot");
    if (!m) {
      closeContextMenu();
      return;
    }
    e.preventDefault();
    e.stopPropagation();
    openAnnotationMenu(
      m.getAttribute("data-id"),
      window.scrollX + e.clientX,
      window.scrollY + e.clientY,
      m
    );
  });

  document.addEventListener("dblclick", function (e) {
    var m = e.target.closest && e.target.closest("mark.annot");
    if (!m) return;
    e.preventDefault();
    e.stopPropagation();
    openNoteEditor(m.getAttribute("data-id"), m);
  });

  document.addEventListener("mouseover", function (e) {
    var m = e.target.closest && e.target.closest("mark.annot");
    if (m) showPopover(m.getAttribute("data-id"), m);
  });

  document.addEventListener("mouseout", function (e) {
    var m = e.target.closest && e.target.closest("mark.annot");
    if (!m) return;
    var to = e.relatedTarget;
    if (to && to.closest && to.closest("mark.annot")) return;
    clearPopover();
  });

  document.addEventListener("keydown", function (e) {
    if (isTypingTarget(e.target)) return;
    if (e.key === "Escape") {
      closeContextMenu();
      return;
    }
    if (e.key !== "Delete" || !activeId) return;
    e.preventDefault();
    requestDeleteAnnotation(activeId);
  });

  window.addEventListener("resize", scheduleSideNoteLayout);
  window.addEventListener("load", scheduleSideNoteLayout);
})();
