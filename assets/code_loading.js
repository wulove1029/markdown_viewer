/* Complete source text stays available while offscreen syntax markup waits. */
(function () {
  'use strict';
  var data = document.currentScript && document.currentScript.previousElementSibling;
  if (!data) return;
  var payload = JSON.parse(data.textContent);
  var lines = payload.lines;
  var blocks = Array.isArray(payload) ? payload : payload.blocks;
  payload = null;
  data.remove();
  var pending = new Set(document.querySelectorAll('pre[data-md-code]'));
  var observer;
  var frozen = false;
  function restore(pre) {
    if (!pending.has(pre)) return;
    if (!pre.isConnected) { pending.delete(pre); return; }
    var selection = window.getSelection();
    // Never replace a selection, an annotation, or a live editor's nodes.
    if ((selection && !selection.isCollapsed && selection.containsNode(pre, true)) ||
        pre.querySelector('mark.annot') || pre.isContentEditable ||
        pre.closest('[contenteditable="true"]')) return;
    var markup = blocks[Number(pre.dataset.mdCode)];
    pre.innerHTML = typeof markup === 'string' ? markup : markup.map(function (line) {
      return lines[line];
    }).join('');
    blocks[Number(pre.dataset.mdCode)] = null;
    delete pre.dataset.mdCode;
    pending.delete(pre);
    if (!pending.size) lines = null;
    if (observer) observer.unobserve(pre);
  }
  function ensureAll() { Array.from(pending).forEach(restore); }
  function observePending() {
    if (observer) pending.forEach(function (pre) {
      observer.unobserve(pre); observer.observe(pre);
    });
  }
  window.mdCode = {
    ensureAll: ensureAll,
    freeze: function () { frozen = true; },
    resume: function () {
      frozen = false;
      observePending();
    },
    pending: function () { return pending.size; }
  };
  if ('IntersectionObserver' in window) {
    observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!frozen && entry.isIntersecting) restore(entry.target);
      });
    }, {rootMargin: '800px 0px'});
    pending.forEach(function (pre) { observer.observe(pre); });
  } else ensureAll();
  document.addEventListener('selectionchange', function () {
    var selection = window.getSelection();
    if (!frozen && (!selection || selection.isCollapsed)) observePending();
  });
  window.addEventListener('beforeprint', ensureAll);
})();
