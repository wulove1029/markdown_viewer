/* Render nearby diagrams serially; exports explicitly request the whole document. */
(function () {
  "use strict";
  if (window.mdDiagrams) return;
  var options = window.__mdDiagramOptions || {};
  var theme = options.theme || "default";
  var revision = 0, active = false, installed = false, scheduled = false;
  var forceAll = !!options.eager;
  var records = [], queue = [], allPromise = null, resolveAll = null;
  var byShell = new WeakMap();
  var observer = null;
  var largeCharacters = 12000, largeLines = 180;

  function status() {
    var result = {total: records.length, rendered: 0, failed: 0, pending: 0, complete: false};
    records.forEach(function (record) {
      if (record.state === "ready") result.rendered++;
      else if (record.state === "error") result.failed++;
      else result.pending++;
    });
    result.complete = installed && result.pending === 0;
    return result;
  }

  function settle() {
    var result = status();
    if (result.complete && resolveAll) {
      var resolve = resolveAll;
      resolveAll = null;
      resolve(result);
    }
  }

  function paint(record) {
    record.element.dataset.diagramState = record.state;
    record.element.hidden = record.state !== "ready" && record.state !== "error";
    record.controls.hidden = record.state === "ready";
    // Keep a keyboard user's focus while the explicitly requested graph loads.
    record.button.setAttribute("aria-disabled", String(
      record.state === "loading" || record.state === "queued"));
    record.button.textContent = record.state === "error" ? "重新嘗試" : "載入圖表";
    record.label.textContent = record.state === "error" ?
      "圖表無法繪製，以下保留完整原始碼。" :
      record.state === "loading" || record.state === "queued" ? "正在繪製圖表…" :
      record.large ? "大型圖表，按下載入後即可閱讀。" : "捲動至此處時載入圖表。";
    record.shell.setAttribute("aria-busy", String(record.state === "loading"));
  }

  function enqueue(record, explicit) {
    if (record.state !== "pending" && record.state !== "error") return;
    if (record.state === "error" && !explicit) return;
    if (record.large && !explicit && !forceAll) return;
    record.wanted = true;
    record.explicit = record.explicit || explicit;
    record.state = "queued";
    queue.push(record);
    paint(record);
    schedule();
  }

  function schedule() {
    if (active || scheduled || !queue.length) { settle(); return; }
    scheduled = true;
    // Yield between every graph; a long library must not form one microtask chain.
    window.setTimeout(function () { scheduled = false; runNext(); }, 16);
  }

  async function runNext() {
    if (active || !queue.length) { settle(); return; }
    var record = queue.shift();
    if (!record.element.isConnected) { schedule(); return; }
    // Earlier graphs may have grown and moved this queued graph far away.
    if (!forceAll && !record.explicit && !nearby(record)) {
      record.state = "pending";
      paint(record);
      schedule();
      return;
    }
    active = true;
    record.state = "loading";
    paint(record);
    var startedRevision = revision;
    record.element.hidden = false;
    record.element.removeAttribute("data-processed");
    record.element.textContent = record.source;
    try {
      if (!window.mermaid) throw new Error("Mermaid is unavailable");
      window.mermaid.initialize({startOnLoad: false, theme: theme});
      await window.mermaid.run({nodes: [record.element], suppressErrors: false});
      if (!record.element.querySelector("svg")) throw new Error("Mermaid did not produce a graph");
      record.state = "ready";
    } catch (_error) {
      record.state = "error";
      record.element.removeAttribute("data-processed");
      record.element.textContent = record.source;
    }
    active = false;
    if (startedRevision !== revision) {
      record.state = "pending";
      record.element.textContent = record.source;
      record.element.removeAttribute("data-processed");
      enqueue(record, true);
    }
    var returnFocus = record.state === "ready" && document.activeElement === record.button;
    paint(record);
    if (returnFocus) {
      record.element.tabIndex = -1;
      record.element.focus({preventScroll: true});
    }
    schedule();
    settle();
  }

  function nearby(record) {
    var rect = record.shell.getBoundingClientRect();
    return rect.bottom >= -400 && rect.top <= window.innerHeight + 400;
  }

  function scanVisible() {
    records.filter(function (record) { return nearby(record); })
      .sort(function (left, right) {
        return Math.abs(left.shell.getBoundingClientRect().top) -
          Math.abs(right.shell.getBoundingClientRect().top);
      }).forEach(function (record) { enqueue(record, false); });
  }

  function install() {
    if (installed) return;
    var style = document.createElement("style");
    style.textContent = ".diagram-block{margin:1.2em 0;position:relative}" +
      ".diagram-block [hidden]{display:none!important}" +
      ".diagram-block pre[data-diagram-state=loading]{position:absolute;visibility:hidden;" +
      "width:100%;margin:0;left:0;top:0}" +
      ".diagram-controls{display:flex;align-items:center;justify-content:space-between;gap:16px;" +
      "min-height:72px;padding:12px 16px;border:1px solid var(--border,#ccc);border-radius:8px;" +
      "background:var(--surface,#f8f8f8);color:var(--text,#242424);white-space:normal}" +
      ".diagram-controls span{font:14px/1.6 sans-serif}.diagram-controls button{font:14px sans-serif;" +
      "padding:8px 12px;min-height:36px;border:1px solid var(--border,#aaa);border-radius:6px;" +
      "color:inherit;background:var(--surface-hover,#eee);cursor:pointer;flex:none}" +
      ".diagram-controls button:focus-visible{outline:2px solid var(--accent,#4778b8);outline-offset:2px}" +
      ".diagram-controls button[aria-disabled=true]{opacity:.65;cursor:wait}" +
      ".diagram-block pre[data-diagram-state=error]{white-space:pre;overflow:auto;max-height:none}" +
      "@media print{.diagram-controls button{display:none}.diagram-controls{min-height:0}}";
    document.head.append(style);
    document.querySelectorAll(".mermaid").forEach(function (element) {
      var source = element.getAttribute("data-diagram") || element.textContent;
      var existing = element.parentElement.classList.contains("diagram-block");
      var shell = existing ? element.parentElement : document.createElement("div");
      shell.className = "diagram-block";
      if (existing) {
        var oldControls = shell.querySelector(".diagram-controls");
        if (oldControls) oldControls.remove();
      }
      var controls = document.createElement("div");
      controls.className = "diagram-controls";
      var label = document.createElement("span");
      label.setAttribute("role", "status");
      var button = document.createElement("button");
      button.type = "button";
      var record = {element: element, shell: shell, controls: controls, label: label,
        button: button, source: source, state: element.querySelector("svg") ? "ready" : "pending",
        wanted: false, large: source.length > largeCharacters || source.split("\n").length > largeLines};
      button.addEventListener("click", function () {
        if (!resolveAll) allPromise = null;
        enqueue(record, true);
      });
      controls.append(label, button);
      if (!existing) element.parentNode.insertBefore(shell, element);
      shell.append(controls, element);
      records.push(record);
      byShell.set(shell, record);
      paint(record);
    });
    installed = true;
    if ("IntersectionObserver" in window) {
      observer = new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            var record = byShell.get(entry.target);
            if (record) enqueue(record, false);
          }
        });
      }, {rootMargin: "400px 0px"});
      records.forEach(function (record) { observer.observe(record.shell); });
    } else {
      window.addEventListener("scroll", scanVisible, {passive: true});
      window.addEventListener("resize", scanVisible, {passive: true});
    }
    if (forceAll) ensureAll();
    else scanVisible();
    settle();
  }

  function ensureAll() {
    forceAll = true;
    if (!allPromise) allPromise = new Promise(function (resolve) { resolveAll = resolve; });
    if (!installed) return allPromise;
    records.forEach(function (record) { enqueue(record, false); });
    settle();
    return allPromise;
  }

  function setTheme(value) {
    if (value === theme) return;
    theme = value === "dark" ? "dark" : "default";
    revision++;
    if (!resolveAll) allPromise = null;
    records.forEach(function (record) {
      if (record.state === "loading" || record.state === "queued") return;
      if (record.state === "ready") {
        record.state = "pending";
        record.element.textContent = record.source;
        record.element.removeAttribute("data-processed");
        paint(record);
      }
      if (forceAll || record.wanted || nearby(record)) enqueue(record, false);
    });
  }

  window.mdDiagrams = {ensureAll: ensureAll, status: status, setTheme: setTheme};
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", install);
  else install();
})();
