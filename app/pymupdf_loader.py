"""One lazy, thread-safe PyMuPDF cache shared by PDF readers and writers."""

import logging
from threading import Lock

_UNSET = object()
_module = _UNSET
_lock = Lock()


def load_pymupdf():
    """Return the optional native module, caching unavailable imports as None."""
    global _module
    with _lock:
        if _module is _UNSET:
            try:
                import pymupdf
            except Exception:
                logging.getLogger(__name__).warning("PyMuPDF is unavailable", exc_info=True)
                _module = None
            else:
                _module = pymupdf
        return _module
