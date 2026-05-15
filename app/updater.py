from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import tempfile
import urllib.error
import urllib.request

from .version import LATEST_RELEASE_API, VERSION


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
            )

    raise UpdateError("A newer release exists, but no installer asset was found.")


def download_installer(update: UpdateInfo) -> Path:
    if not update.asset_url or not update.asset_name:
        raise UpdateError("No installer is available for this update.")

    target = Path(tempfile.gettempdir()) / update.asset_name
    request = urllib.request.Request(
        update.asset_url,
        headers={"User-Agent": "MarkdownViewer-Updater"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            target.write_bytes(response.read())
    except Exception as exc:
        raise UpdateError(f"Unable to download update: {exc}") from exc

    if target.stat().st_size == 0:
        raise UpdateError("Downloaded installer is empty.")
    return target
