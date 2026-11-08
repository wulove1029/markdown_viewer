"""Shared hardening for the WebEngine pages that render untrusted Markdown.

Markdown can arrive from external workspaces or downloads, so both the
preview (``renderer.py``) and the WYSIWYG editor (``wysiwyg_view.py``) treat
document content as hostile:

* a Content-Security-Policy keeps page script from opening network
  connections, frames, plugins or form submissions;
* the remote-file request guard refuses ``file://host/...`` (UNC) and Win32
  device-namespace (``\\\\.\\UNC\\host``) requests at the network layer, so
  neither an ``<img>`` nor CSS can make Windows open an SMB connection (and
  leak an NTLM hash) to a host chosen by the document;
* :func:`is_network_path_link` lets the Markdown converter drop such link
  targets before they ever reach HTML.

This module stays importable without Qt WebEngine (the Markdown converter
also runs in render worker processes); Qt is imported lazily.  See
``docs/upgrades/2026-11-08-security-hardening.md``.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import unquote

# Applied to every preview document.  It intentionally leaves script-src,
# style-src and img-src alone: the preview owns several inline scripts
# (Mermaid/KaTeX loaders, copy buttons) and raw HTML is disabled in the
# converter, so the policy only removes data-exfiltration channels.
PREVIEW_CSP = (
    "connect-src 'none'; frame-src 'none'; object-src 'none'; "
    "base-uri 'none'; form-action 'none'"
)

# The WYSIWYG host only runs scripts carrying the per-load nonce plus the
# scripts those create ('strict-dynamic': Vditor's own lazy loader).  'self'
# is deliberately absent: on a file:// page it admits *every* file: URL, so
# e.g. <iframe srcdoc><script src=file:///...> from a document would run.
# Inline event handlers and javascript: URLs never run.  ``connect-src
# data:`` keeps Vditor's "copy diagram as PNG" (it fetches its own data: URI)
# working without allowing any network or file access.
WYSIWYG_CSP_TEMPLATE = (
    "script-src 'nonce-{nonce}' 'strict-dynamic'; object-src 'none'; frame-src 'none'; "
    "child-src 'none'; base-uri 'none'; form-action 'none'; connect-src data:"
)

_SCRIPT_TAG = re.compile(r"<script\b(?![^>]*\bnonce=)", re.IGNORECASE)
# The WHATWG URL parser deletes ASCII tab/newline anywhere and trims C0
# control characters and spaces at both ends.
_URL_IGNORED = re.compile(r"[\t\n\r]")
_URL_LEADING = "".join(chr(code) for code in range(0x21))
# After backslashes became slashes and "." segments collapsed: a UNC path
# ("//host"), a Win32 device namespace path ("/./", "/?/") or a bare
# "/UNC/" root -- all refused, fail closed.
_REMOTE_OR_DEVICE_PATH = re.compile(r"^(?://|/+[.?](?:/|$)|/+unc(?:/|$))", re.IGNORECASE)


def csp_meta(policy: str) -> str:
    """Return a ``<meta>`` element that installs *policy* for a document."""
    return f'<meta http-equiv="Content-Security-Policy" content="{policy}">'


def new_script_nonce() -> str:
    """A fresh CSP nonce (base64url, 144 bits) for one host-page load."""
    return secrets.token_urlsafe(18)


def add_script_nonce(html: str, nonce: str) -> str:
    """Stamp ``nonce=`` on every ``<script`` tag of a trusted template."""
    return _SCRIPT_TAG.sub(f'<script nonce="{nonce}"', html)


def wysiwyg_csp(nonce: str) -> str:
    """Build the WYSIWYG host policy for one load's *nonce*."""
    return WYSIWYG_CSP_TEMPLATE.format(nonce=nonce)


def _collapse_dot_segments(path: str) -> str:
    previous = None
    while previous != path:
        previous = path
        path = path.replace("/./", "/")
        if path.endswith("/."):
            path = path[:-1]
    return path


def _is_remote_or_device_path(path: str) -> bool:
    """*path* uses "/" separators; True for UNC / device-namespace roots."""
    return bool(_REMOTE_OR_DEVICE_PATH.match(_collapse_dot_segments(path)))


def is_network_path_link(url: str) -> bool:
    """True for link targets that can resolve to a UNC (SMB) location.

    Relative to a ``file://`` document ``//host/…`` becomes
    ``file://host/…``; ``//./UNC/host/…`` and ``//?/UNC/host/…`` are the Win32
    device-namespace spellings of the same thing.  Percent-encoding is decoded
    first (markdown-it normalizes backslashes to ``%5C``), backslashes count
    as slashes and dot segments are collapsed, so mixed or padded spellings
    are caught too.  ``file:`` targets with a host or such a path are refused.
    """
    value = _URL_IGNORED.sub("", unquote(url or "")).lstrip(_URL_LEADING)
    value = value.replace("\\", "/")
    if value[:5].lower() == "file:":
        rest = value[5:]
        if rest.startswith("//"):
            host, _separator, path = rest[2:].partition("/")
            if host:
                return True
            value = "/" + path
        else:
            value = rest
    return _is_remote_or_device_path(value)


def document_host(path: str | Path | None) -> str:
    """Host part of *path*'s ``file:`` URL ("" for local drive paths)."""
    if not path:
        return ""
    from PySide6.QtCore import QUrl

    return QUrl.fromLocalFile(str(path)).host()


def blocks_file_request(url: Any, allowed_host: str = "") -> bool:
    """Should a request for the QUrl *url* be refused?

    Fails closed: an empty or invalid URL is refused (Chromium hands over
    ``file://./UNC/host/…``, which QUrl cannot represent).  A ``file:`` URL is
    refused when it names a host other than *allowed_host* -- the share the
    current document lives on, passed only for resource loads -- or when its
    path is a UNC / device-namespace path.  Other schemes are left to the
    page's own settings and CSP.
    """
    if url.isEmpty() or not url.isValid():
        return True
    if url.scheme().lower() != "file":
        return False
    host = url.host()
    if host and host.casefold() != (allowed_host or "").casefold():
        return True
    from PySide6.QtCore import QUrl

    path = unquote(url.path(QUrl.ComponentFormattingOption.FullyEncoded))
    return _is_remote_or_device_path(path.replace("\\", "/"))


@lru_cache(maxsize=1)
def remote_file_guard_type():
    """Return the ``QWebEngineUrlRequestInterceptor`` subclass (lazy Qt import)."""
    from PySide6.QtWebEngineCore import (
        QWebEngineUrlRequestInfo,
        QWebEngineUrlRequestInterceptor,
    )

    resource = QWebEngineUrlRequestInfo.ResourceType
    # The current document's own share may serve what the note displays;
    # navigations, frames, scripts, plugins, XHR etc. never get the exception.
    share_resources = frozenset({
        resource.ResourceTypeImage,
        resource.ResourceTypeStylesheet,
        resource.ResourceTypeFontResource,
        resource.ResourceTypeMedia,
    })

    class RemoteFileRequestGuard(QWebEngineUrlRequestInterceptor):
        """Block UNC / device-namespace ``file:`` requests (fail closed)."""

        def __init__(self, allowed_host: Callable[[], str] | None = None, parent=None):
            super().__init__(parent)
            self._allowed_host = allowed_host

        def allowed_host(self) -> str:
            if self._allowed_host is None:
                return ""
            try:
                return self._allowed_host() or ""
            except Exception:  # a guard must never raise into Chromium
                return ""

        def interceptRequest(self, info) -> None:  # Qt override
            try:
                allowed = (
                    self.allowed_host() if info.resourceType() in share_resources else ""
                )
                blocked = blocks_file_request(info.requestUrl(), allowed)
            except Exception:  # fail closed
                blocked = True
            if blocked:
                info.block(True)

    return RemoteFileRequestGuard


def install_remote_file_guard(page, allowed_host: Callable[[], str] | None = None):
    """Attach a remote-file request guard owned by *page* and return it."""
    guard = remote_file_guard_type()(allowed_host, page)
    page.setUrlRequestInterceptor(guard)
    return guard
