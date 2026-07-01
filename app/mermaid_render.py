"""Self-contained Mermaid preview HTML for QWebEngine."""

from __future__ import annotations

import json
from html import escape
from pathlib import Path

from .theme import ThemeName

_ASSETS_DIR = Path(__file__).parent.parent / "assets"
_MERMAID_JS = _ASSETS_DIR / "mermaid.min.js"


def mermaid_asset_exists() -> bool:
    return _MERMAID_JS.exists()


def _json_for_script(value: str) -> str:
    # A JSON string is safe in a script block except for a literal </script>,
    # which HTML would terminate before JavaScript sees it.
    return json.dumps(value).replace("</", "<\\/")


def _mermaid_theme(theme: ThemeName | str) -> str:
    return "dark" if theme == "dark" else "default"


def build_preview_html(source: str, theme: ThemeName | str = "light") -> str:
    """Return a complete HTML document that renders one Mermaid diagram."""
    return _build_html(source, theme, export=False)


def build_export_html(source: str, theme: ThemeName | str = "light") -> str:
    """Return a complete HTML document suitable for image export."""
    return _build_html(source, theme, export=True)


def _build_html(source: str, theme: ThemeName | str, *, export: bool) -> str:
    dark = theme == "dark"
    bg = "#171b22" if dark and not export else "#ffffff"
    fg = "#f2f5f8" if dark and not export else "#1d1f23"
    muted = "#c1c8d2" if dark and not export else "#515760"
    border = "#3a414c" if dark and not export else "#d7d8d2"
    surface = "#20252d" if dark and not export else "#f7f7f4"
    error_bg = "#3a1f22" if dark and not export else "#fff1f0"
    error_fg = "#ffb8b0" if dark and not export else "#b42318"

    asset_uri = _MERMAID_JS.resolve().as_uri() if mermaid_asset_exists() else ""
    asset_tag = f'<script src="{asset_uri}"></script>' if asset_uri else ""
    missing = "" if asset_uri else "Mermaid asset not found."
    source_js = _json_for_script(source)
    mermaid_theme = json.dumps(_mermaid_theme(theme))

    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
html, body {{
    margin: 0;
    min-height: 100%;
    background: {bg};
    color: {fg};
    font-family: "Segoe UI", "Microsoft JhengHei UI", sans-serif;
}}
body {{
    box-sizing: border-box;
    padding: 20px;
}}
.preview-shell {{
    min-height: calc(100vh - 40px);
    display: flex;
    align-items: center;
    justify-content: center;
}}
.empty, .error {{
    max-width: 720px;
    border: 1px solid {border};
    border-radius: 8px;
    padding: 14px 16px;
    background: {surface};
    color: {muted};
    white-space: pre-wrap;
}}
.error {{
    background: {error_bg};
    color: {error_fg};
}}
.mermaid {{
    max-width: 100%;
}}
.mermaid svg {{
    max-width: 100%;
    height: auto;
}}
</style>
{asset_tag}
</head>
<body>
<div class="preview-shell">
  <pre id="diagram" class="mermaid"></pre>
  <div id="message" class="empty" style="display:none"></div>
</div>
<script>
window.__mermaidStatus = {{ ready: false, ok: false, error: "", svg: "" }};
(function () {{
  const source = {source_js};
  const missing = {json.dumps(missing)};
  const diagram = document.getElementById("diagram");
  const message = document.getElementById("message");

  function finish(ok, error, svg) {{
    window.__mermaidStatus = {{
      ready: true,
      ok: !!ok,
      error: error || "",
      svg: svg || ""
    }};
  }}

  function showMessage(text, cls) {{
    diagram.style.display = "none";
    message.style.display = "block";
    message.className = cls;
    message.textContent = text;
  }}

  async function render() {{
    if (missing) {{
      showMessage(missing, "error");
      finish(false, missing, "");
      return;
    }}
    if (!source.trim()) {{
      showMessage("Start with a Mermaid template or type Mermaid source.", "empty");
      finish(false, "", "");
      return;
    }}
    if (!window.mermaid) {{
      const error = "Mermaid failed to load.";
      showMessage(error, "error");
      finish(false, error, "");
      return;
    }}
    diagram.textContent = source;
    diagram.style.display = "block";
    message.style.display = "none";
    try {{
      window.mermaid.initialize({{
        startOnLoad: false,
        securityLevel: "strict",
        theme: {mermaid_theme}
      }});
      await window.mermaid.run({{ querySelector: "#diagram" }});
      const svg = diagram.querySelector("svg");
      if (!svg) {{
        throw new Error("Mermaid did not produce an SVG.");
      }}
      if (!svg.getAttribute("xmlns")) {{
        svg.setAttribute("xmlns", "http://www.w3.org/2000/svg");
      }}
      finish(true, "", new XMLSerializer().serializeToString(svg));
    }} catch (err) {{
      const text = err && (err.str || err.message) ? (err.str || err.message) : String(err);
      showMessage(text, "error");
      finish(false, text, "");
    }}
  }}

  if (document.readyState === "loading") {{
    document.addEventListener("DOMContentLoaded", render);
  }} else {{
    render();
  }}
}})();
</script>
</body>
</html>"""


def preview_error_html(message: str, theme: ThemeName | str = "light") -> str:
    safe = escape(message)
    dark = theme == "dark"
    bg = "#171b22" if dark else "#ffffff"
    fg = "#ffb8b0" if dark else "#b42318"
    return f"""<!doctype html>
<html lang="zh-Hant">
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:20px;background:{bg};color:{fg};
font-family:'Segoe UI','Microsoft JhengHei UI',sans-serif;">
<pre style="white-space:pre-wrap">{safe}</pre>
</body>
</html>"""
