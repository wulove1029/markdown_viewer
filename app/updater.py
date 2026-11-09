from __future__ import annotations

import logging

log = logging.getLogger(__name__)

import hashlib
import json
import os
import queue
import re
import shutil
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .version import LATEST_RELEASE_API, VERSION

# Only ever download/execute an installer served over HTTPS from GitHub. This
# blocks a MITM or a redirect to an attacker host from feeding us an arbitrary
# executable that the auto-updater would otherwise run with admin rights.
_TRUSTED_HOSTS = {"github.com", "api.github.com"}
_TRUSTED_HOST_SUFFIXES = (".github.com", ".githubusercontent.com")
_SHA256_RE = re.compile(r"[0-9a-fA-F]{64}")

#: Most bytes taken from the socket per read; hashing and writing stay cheap.
DOWNLOAD_CHUNK_SIZE = 64 * 1024
#: Socket timeout for the connect *and* for every read. urllib installs it on
#: the underlying socket, so a stalled server cannot park a transfer forever.
DOWNLOAD_TIMEOUT = 20.0
#: Socket timeout for the release-metadata check (connect and every read).
CHECK_TIMEOUT = 15.0
#: How often a worker waiting on the network looks at its cancel event. The
#: socket work runs on a helper thread (see _Transfer), so this, and not the
#: socket timeouts above, bounds how long a cancelled worker keeps running.
CANCEL_POLL_S = 0.05
_JOB_DIR_PREFIX = "mdviewer-update-"


class UpdateError(RuntimeError):
    pass


class UpdateCancelled(UpdateError):
    """The caller set the cancel event before the download finished."""


class UpdateDigestUnavailable(UpdateError):
    """The release asset has no usable SHA-256, so we refuse to auto-install.

    The installer runs with admin rights, so "arrived over TLS" is not enough
    on its own: with no digest to compare against we cannot tell a good asset
    from a swapped one, and the caller must fall back to a manual download from
    the official release page.
    """

    def __init__(self, message: str, release_url: str = "") -> None:
        super().__init__(message)
        self.release_url = release_url


@dataclass(frozen=True)
class UpdateInfo:
    has_update: bool
    current_version: str
    latest_version: str
    release_url: str
    asset_name: str | None = None
    asset_url: str | None = None
    asset_digest: str | None = None


def _require_trusted_url(url: str) -> None:
    """Raise UpdateError unless *url* is HTTPS on a trusted GitHub host."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https":
        raise UpdateError(f"Refusing non-HTTPS update URL: {url!r}")
    host = (parts.hostname or "").lower()
    if host in _TRUSTED_HOSTS or any(host.endswith(s) for s in _TRUSTED_HOST_SUFFIXES):
        return
    raise UpdateError(f"Refusing update download from untrusted host: {host!r}")


def _expected_sha256(digest: str | None) -> str | None:
    """Extract a 64-hex SHA-256 from a GitHub asset ``digest`` (``sha256:...``)."""
    if not digest:
        return None
    value = digest.strip()
    if value.lower().startswith("sha256:"):
        value = value.split(":", 1)[1].strip()
    return value.lower() if _SHA256_RE.fullmatch(value) else None


def _version_tuple(version: str) -> tuple[int, ...]:
    cleaned = version.strip().lstrip("vV")
    parts = re.findall(r"\d+", cleaned)
    return tuple(int(part) for part in parts) if parts else (0,)


def is_newer_version(latest: str, current: str) -> bool:
    """Return whether *latest* is semantically newer than *current*."""
    latest_parts = _version_tuple(latest)
    current_parts = _version_tuple(current)
    width = max(len(latest_parts), len(current_parts))
    latest_parts += (0,) * (width - len(latest_parts))
    current_parts += (0,) * (width - len(current_parts))
    return latest_parts > current_parts


def _is_newer(latest: str, current: str) -> bool:
    """Backward-compatible private alias used by existing callers/tests."""
    return is_newer_version(latest, current)


def check_for_update(
    current_version: str = VERSION,
    *,
    cancel: threading.Event | None = None,
    timeout: float = CHECK_TIMEOUT,
) -> UpdateInfo:
    """Fetch the latest release metadata.

    ``cancel`` may be set at any time: the check then raises UpdateCancelled
    within ``CANCEL_POLL_S``, whether it is still resolving, connecting,
    waiting for headers or reading a slow body (see _Transfer).
    """
    request = urllib.request.Request(
        LATEST_RELEASE_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "MarkdownViewer-Updater",
        },
    )

    if cancel is not None and cancel.is_set():
        raise UpdateCancelled("Update check cancelled.")

    transfer = _Transfer(request, timeout, DOWNLOAD_CHUNK_SIZE)
    try:
        body = bytearray()
        kind, value = transfer.next(cancel)
        if kind == "open":
            transfer.proceed()
            while True:
                kind, value = transfer.next(cancel)
                if kind != "data" or not value:
                    break
                body += value
        if kind == "cancelled":
            raise UpdateCancelled("Update check cancelled.")
        if kind in ("open_error", "read_error"):
            raise value
        release = json.loads(bytes(body).decode("utf-8"))
    except UpdateCancelled:
        raise
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return UpdateInfo(False, current_version, current_version, "")
        raise UpdateError(f"Unable to check for updates: {exc}") from exc
    except Exception as exc:
        if cancel is not None and cancel.is_set():
            raise UpdateCancelled("Update check cancelled.") from exc
        raise UpdateError(f"Unable to check for updates: {exc}") from exc
    finally:
        transfer.abandon()

    if cancel is not None and cancel.is_set():
        raise UpdateCancelled("Update check cancelled.")

    # Stable clients must not offer draft or prerelease metadata, even if a
    # proxy or a changed endpoint supplies it instead of the latest release.
    if release.get("draft") or release.get("prerelease"):
        return UpdateInfo(False, current_version, current_version, "")

    latest_version = str(release.get("tag_name") or "").lstrip("vV")
    if not latest_version:
        raise UpdateError("Latest release does not include a version tag.")

    html_url = str(release.get("html_url") or "")
    if not is_newer_version(latest_version, current_version):
        return UpdateInfo(False, current_version, latest_version, html_url)

    for asset in release.get("assets", []):
        name = str(asset.get("name") or "")
        download_url = str(asset.get("browser_download_url") or "")
        if name.lower().endswith(".exe") and "setup" in name.lower() and download_url:
            return UpdateInfo(
                True,
                current_version,
                latest_version,
                html_url,
                asset_name=name,
                asset_url=download_url,
                # GitHub serves a per-asset content digest ("sha256:...") over
                # the same TLS channel as the version, so we can verify the
                # binary's integrity before ever executing it.
                asset_digest=str(asset.get("digest") or "") or None,
            )

    raise UpdateError("A newer release exists, but no installer asset was found.")


def has_verifiable_digest(update: UpdateInfo) -> bool:
    """Whether *update* carries a digest we can actually check against."""
    return _expected_sha256(update.asset_digest) is not None


def verify_installer(path, expected_digest: str | None) -> bool:
    """Return whether *path* still exists and matches *expected_digest*.

    Re-run this immediately before launching: a verified download can be
    deleted, truncated or swapped while the user is deciding what to do.
    """
    expected = _expected_sha256(expected_digest)
    if not expected:
        return False
    target = Path(path)
    try:
        if not target.is_file():
            return False
        hasher = hashlib.sha256()
        with open(target, "rb") as handle:
            for block in iter(lambda: handle.read(DOWNLOAD_CHUNK_SIZE), b""):
                hasher.update(block)
    except OSError:
        return False
    return hasher.hexdigest() == expected


def _content_length(response) -> int | None:
    """Return a usable Content-Length, or None when the server omits it."""
    raw = None
    getter = getattr(response, "getheader", None)
    if callable(getter):
        raw = getter("Content-Length")
    if raw is None:
        headers = getattr(response, "headers", None)
        if headers is not None:
            try:
                raw = headers.get("Content-Length")
            except Exception:
                raw = None
    try:
        length = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return length if length >= 0 else None


def _remove_job_dir(job_dir: Path | None) -> None:
    """Delete only the throwaway directory that this job created."""
    if job_dir is None:
        return
    # Refuse to recurse into anything we did not mint ourselves; a bug here
    # would otherwise wipe the system temp dir or another version's download.
    if not job_dir.name.startswith(_JOB_DIR_PREFIX):
        return
    shutil.rmtree(job_dir, ignore_errors=True)


def cleanup_job_dir(path) -> None:
    """Public helper: drop the private temp dir that produced *path*."""
    if not path:
        return
    _remove_job_dir(Path(path).parent)


def _close_quietly(response) -> None:
    try:
        response.close()
    except Exception:
        log.warning("Closing an update response failed", exc_info=True)


class _Transfer:
    """Open *request* and read its body on a daemon helper thread.

    The calling worker only waits on a queue, ``CANCEL_POLL_S`` at a time, so
    a cancel returns it at once whatever the socket is doing: resolving,
    connecting, waiting for headers or receiving a trickle. Closing a
    response cannot interrupt a read already blocked on its socket, so an
    abandoned helper keeps its socket timeout, stops before its next read,
    closes the response and ends on its own. It holds no Qt object.

    Events: ``("open", response)``, then ``("data", bytes)`` up to an empty
    chunk; or ``("open_error" | "read_error", exc)``. The body is read only
    after ``proceed()``, once the caller has vetted the response.
    """

    def __init__(self, request, timeout: float, chunk_size: int) -> None:
        self._events: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=8)
        self._stop = threading.Event()
        self._go = threading.Event()
        self._lock = threading.Lock()
        self._response: Any = None
        threading.Thread(
            target=self._run,
            args=(request, timeout, chunk_size),
            name="update-transfer",
            daemon=True,
        ).start()

    def next(self, cancel: threading.Event | None) -> tuple[str, Any]:
        """The helper's next event, or ``("cancelled", None)`` once *cancel* is set."""
        while True:
            if cancel is not None and cancel.is_set():
                self.abandon()
                return "cancelled", None
            try:
                return self._events.get(timeout=CANCEL_POLL_S)
            except queue.Empty:
                continue

    def proceed(self) -> None:
        self._go.set()

    def abandon(self) -> None:
        """Stop the helper without waiting for it; harmless once it has ended."""
        self._stop.set()
        with self._lock:
            response, self._response = self._response, None
        if response is not None:
            # close() can block behind a read in progress: never on the worker.
            threading.Thread(
                target=_close_quietly, args=(response,), name="update-close", daemon=True
            ).start()

    def _put(self, event: tuple[str, Any]) -> bool:
        while not self._stop.is_set():
            try:
                self._events.put(event, timeout=CANCEL_POLL_S)
                return True
            except queue.Full:
                continue
        return False

    def _run(self, request, timeout: float, chunk_size: int) -> None:
        try:
            response = urllib.request.urlopen(request, timeout=timeout)
        except Exception as exc:
            self._put(("open_error", exc))
            return
        with self._lock:
            # abandon() sets _stop before it takes the lock: either it finds
            # this response, or this sees _stop and keeps it to close below.
            stored = not self._stop.is_set()
            if stored:
                self._response = response
        try:
            if self._stop.is_set() or not self._put(("open", response)):
                return
            while not self._go.wait(CANCEL_POLL_S):
                if self._stop.is_set():
                    return
            # read1 returns what one receive brings, so a trickle reaches the
            # caller as it arrives and a stop is noticed between receives.
            read = getattr(response, "read1", None) or response.read
            while not self._stop.is_set():
                try:
                    chunk = read(chunk_size)
                except Exception as exc:
                    self._put(("read_error", exc))
                    return
                if not self._put(("data", chunk)) or not chunk:
                    return
        finally:
            with self._lock:
                owned = not stored or self._response is response
                if self._response is response:
                    self._response = None
            if owned:  # otherwise abandon() took it and closes it
                _close_quietly(response)


def download_installer(
    update: UpdateInfo,
    *,
    progress: Callable[[int, int | None], None] | None = None,
    cancel: threading.Event | None = None,
    dest_dir=None,
    chunk_size: int = DOWNLOAD_CHUNK_SIZE,
    timeout: float = DOWNLOAD_TIMEOUT,
) -> Path:
    """Stream the installer into a private temp dir, hashing as it arrives.

    ``progress(downloaded, total)`` is invoked on the calling thread after each
    chunk (``total`` is None when the server omits Content-Length) and
    ``cancel`` may be set at any moment to abort. The returned path exists only
    when the transfer completed *and* the SHA-256 matched.
    """
    if not update.asset_url or not update.asset_name:
        raise UpdateError("No installer is available for this update.")

    _require_trusted_url(update.asset_url)

    # Never trust the asset name as a path: collapse it to a bare basename so a
    # crafted name like "..\\..\\Startup\\evil.exe" cannot escape the temp dir.
    safe_name = Path(update.asset_name).name
    if not safe_name.lower().endswith(".exe") or "setup" not in safe_name.lower():
        raise UpdateError("Refusing to download an unexpected installer asset.")

    expected = _expected_sha256(update.asset_digest)
    if not expected:
        raise UpdateDigestUnavailable(
            "This release publishes no usable SHA-256 digest, so the installer "
            "cannot be verified automatically.",
            update.release_url or "",
        )

    if cancel is not None and cancel.is_set():
        raise UpdateCancelled("Update download cancelled.")

    base_dir = (
        Path(dest_dir) if dest_dir is not None else Path(tempfile.gettempdir())
    )
    base_dir.mkdir(parents=True, exist_ok=True)
    job_dir = Path(tempfile.mkdtemp(prefix=_JOB_DIR_PREFIX, dir=str(base_dir)))
    part = job_dir / (safe_name + ".part")

    request = urllib.request.Request(
        update.asset_url,
        headers={"User-Agent": "MarkdownViewer-Updater"},
    )
    try:
        transfer = _Transfer(request, timeout, chunk_size)
        kind, value = transfer.next(cancel)
        if kind == "cancelled":
            raise UpdateCancelled("Update download cancelled.")
        if kind == "open_error":
            if cancel is not None and cancel.is_set():
                raise UpdateCancelled("Update download cancelled.") from value
            if isinstance(value, UpdateError):
                raise value
            raise UpdateError(f"Unable to download update: {value}") from value

        response = value
        hasher = hashlib.sha256()
        downloaded = 0
        total: int | None = None
        try:
            _require_trusted_url(response.geturl())
            total = _content_length(response)
            transfer.proceed()
            if progress is not None:
                progress(0, total)
            with open(part, "wb") as handle:
                while True:
                    if cancel is not None and cancel.is_set():
                        raise UpdateCancelled("Update download cancelled.")
                    kind, value = transfer.next(cancel)
                    if kind == "cancelled":
                        raise UpdateCancelled("Update download cancelled.")
                    if kind == "read_error":
                        if cancel is not None and cancel.is_set():
                            raise UpdateCancelled(
                                "Update download cancelled."
                            ) from value
                        raise UpdateError(
                            f"Update download interrupted: {value}"
                        ) from value
                    chunk = value
                    if not chunk:
                        break
                    hasher.update(chunk)
                    try:
                        handle.write(chunk)
                    except OSError as exc:
                        # Out of disk, quota, or a vanished temp dir: the
                        # partial file is useless, so fail instead of hashing
                        # bytes that never reached the disk.
                        raise UpdateError(
                            f"Unable to save the update download: {exc}"
                        ) from exc
                    downloaded += len(chunk)
                    if progress is not None:
                        progress(downloaded, total)
                try:
                    handle.flush()
                    os.fsync(handle.fileno())
                except OSError as exc:
                    raise UpdateError(
                        f"Unable to save the update download: {exc}"
                    ) from exc
        finally:
            # The helper has closed the response after the last chunk; a
            # transfer that ended early is stopped and closed off this thread.
            transfer.abandon()

        if cancel is not None and cancel.is_set():
            raise UpdateCancelled("Update download cancelled.")
        if downloaded == 0:
            raise UpdateError("Downloaded installer is empty.")
        if total is not None and downloaded != total:
            raise UpdateError(
                "Downloaded installer is incomplete "
                f"({downloaded} of {total} bytes); aborting."
            )
        if hasher.hexdigest() != expected:
            raise UpdateError(
                "Installer integrity check failed (SHA-256 mismatch); aborting."
            )

        # Only a fully transferred, digest-matched file earns the real name.
        target = job_dir / safe_name
        part.replace(target)
        return target
    except BaseException:
        _remove_job_dir(job_dir)
        raise
