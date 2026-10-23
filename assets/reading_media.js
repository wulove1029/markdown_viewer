/* Accessible, on-demand image inspection for the reading surface. */
(function () {
  "use strict";
  if (window.readingMedia) return;
  var dialog = null;
  var previousFocus = null;
  var previousOverflow = "";
  var original = false;

  function eligible(image) {
    return image instanceof HTMLImageElement &&
      !image.closest("a, button, [contenteditable], .mermaid, .reading-media-dialog, .annot-editor");
  }

  function install() {
    document.querySelectorAll("body img").forEach(function (image) {
      if (!eligible(image) || image.dataset.readingMedia) return;
      // Preserve authored interaction, and never move focus during setup.
      if (image.hasAttribute("tabindex") || image.hasAttribute("role")) return;
      image.dataset.readingMedia = "true";
      image.tabIndex = 0;
      image.setAttribute("role", "button");
      image.setAttribute("aria-haspopup", "dialog");
      image.setAttribute("aria-label", "放大圖片" + (image.alt ? "：" + image.alt : ""));
    });
  }

  function close() {
    if (!dialog) return;
    var closing = dialog;
    dialog = null;
    closing.close();
    closing.remove();
    document.body.style.overflow = previousOverflow;
    if (previousFocus && previousFocus.isConnected) {
      previousFocus.focus({preventScroll: true});
    }
    previousFocus = null;
  }

  function open(image) {
    if (!eligible(image) || !image.currentSrc && !image.src) return false;
    if (dialog) close();
    previousFocus = document.activeElement;
    previousOverflow = document.body.style.overflow;
    original = false;
    dialog = document.createElement("dialog");
    dialog.className = "reading-media-dialog";
    dialog.setAttribute("aria-label", image.alt || "圖片預覽");
    var toolbar = document.createElement("div");
    toolbar.className = "reading-media-toolbar";
    var caption = document.createElement("span");
    caption.className = "reading-media-caption";
    caption.textContent = image.alt || "圖片預覽";
    var mode = document.createElement("button");
    mode.type = "button";
    mode.textContent = "原始大小";
    mode.setAttribute("aria-label", "切換為原始大小");
    mode.setAttribute("aria-pressed", "false");
    var dismiss = document.createElement("button");
    dismiss.type = "button";
    dismiss.textContent = "關閉";
    dismiss.setAttribute("aria-label", "關閉圖片預覽（Esc）");
    var stage = document.createElement("div");
    stage.className = "reading-media-stage";
    stage.tabIndex = 0;
    stage.setAttribute("aria-label", "圖片內容，可使用方向鍵捲動");
    var enlarged = document.createElement("img");
    enlarged.src = image.currentSrc || image.src;
    enlarged.alt = image.alt;
    enlarged.draggable = false;
    function size() {
      enlarged.style.width = original ? String(enlarged.naturalWidth || image.naturalWidth) + "px" : "";
      stage.classList.toggle("is-original", original);
      mode.textContent = original ? "符合寬度" : "原始大小";
      mode.setAttribute("aria-label", original ? "切換為符合寬度" : "切換為原始大小");
      mode.setAttribute("aria-pressed", String(original));
    }
    enlarged.addEventListener("load", size);
    mode.addEventListener("click", function () {
      original = !original;
      size();
    });
    dismiss.addEventListener("click", close);
    dialog.addEventListener("cancel", function (event) {
      event.preventDefault();
      close();
    });
    dialog.addEventListener("keydown", function (event) {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        close();
      }
    });
    stage.addEventListener("click", function (event) {
      if (event.target === stage) close();
    });
    dialog.addEventListener("click", function (event) {
      if (event.target === dialog) close();
    });
    toolbar.append(caption, mode, dismiss);
    stage.append(enlarged);
    dialog.append(toolbar, stage);
    document.body.append(dialog);
    document.body.style.overflow = "hidden";
    dialog.showModal();
    dismiss.focus({preventScroll: true});
    return true;
  }

  document.addEventListener("click", function (event) {
    if (event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    var image = event.target;
    if (eligible(image) && image.dataset.readingMedia && open(image)) {
      event.preventDefault();
    }
  });
  document.addEventListener("keydown", function (event) {
    if (event.defaultPrevented || event.key !== "Enter" && event.key !== " ") return;
    var image = event.target;
    if (eligible(image) && image.dataset.readingMedia && open(image)) {
      event.preventDefault();
      event.stopPropagation();
    }
  });
  window.readingMedia = {install: install, open: open, close: close};
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", install);
  else install();
})();
