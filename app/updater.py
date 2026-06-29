from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request

from .version import LATEST_RELEASE_API, VERSION

# Only ever download/execute an installer served over HTTPS from GitHub. This
# blocks a MITM or a redirect to an attacker host from feeding us an arbitrary
# executable that the auto-updater would otherwise run with admin rights.
_TRUSTED_HOSTS = {"github.com", "api.github.com"}
_TRUSTED_HOST_SUFFIXES = (".github.com", ".githubusercontent.com")
_SHA256_RE = re.compile(r"[0-9a-fA-F]{64}")


class UpdateError(RuntimeError):
    pass


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


def _is_newer(latest: str, current: str) -> bool:
    latest_parts = _version_tuple(latest)
    current_parts = _version_tuple(current)
    width = max(len(latest_parts), len(current_parts))
    latest_parts += (0,) * (width - len(latest_parts))
    current_parts += (0,) * (width - len(current_parts))
    return latest_parts > current_parts


def check_for_update(current_version: str = VERSION) -> UpdateInfo:
    request = urllib.request.Request(
        LATEST_RELEASE_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "MarkdownViewer-Updater",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            release = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return UpdateInfo(False, current_version, current_version, "")
        raise UpdateError(f"Unable to check for updates: {exc}") from exc
    except Exception as exc:
        raise UpdateError(f"Unable to check for updates: {exc}") from exc

    latest_version = str(release.get("tag_name") or "").lstrip("vV")
    if not latest_version:
        raise UpdateError("Latest release does not include a version tag.")

    html_url = str(release.get("html_url") or "")
    if not _is_newer(latest_version, current_version):
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


def download_installer(update: UpdateInfo) -> Path:
    if not update.asset_url or not update.asset_name:
        raise UpdateError("No installer is available for this update.")

    _require_trusted_url(update.asset_url)

    # Never trust the asset name as a path: collapse it to a bare basename so a
    # crafted name like "..\\..\\Startup\\evil.exe" cannot escape the temp dir.
    safe_name = Path(update.asset_name).name
    if not safe_name.lower().endswith(".exe") or "setup" not in safe_name.lower():
        raise UpdateError("Refusing to download an unexpected installer asset.")

    request = urllib.request.Request(
        update.asset_url,
        headers={"User-Agent": "MarkdownViewer-Updater"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            _require_trusted_url(response.geturl())
            data = response.read()
    except UpdateError:
        raise
    except Exception as exc:
        raise UpdateError(f"Unable to download update: {exc}") from exc

    if not data:
        raise UpdateError("Downloaded installer is empty.")

    expected = _expected_sha256(update.asset_digest)
    if expected:
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected:
            raise UpdateError(
                "Installer integrity check failed (SHA-256 mismatch); aborting."
            )

    target = Path(tempfile.gettempdir()) / safe_name
    target.write_bytes(data)
    return target
