"""Tests for update version comparison and hardened installer download."""

import hashlib
from contextlib import contextmanager

import pytest

from app import updater
from app.updater import (
    UpdateError,
    UpdateInfo,
    _expected_sha256,
    _is_newer,
    _require_trusted_url,
    _version_tuple,
    download_installer,
)


# --- version comparison -------------------------------------------------

def test_version_tuple_strips_v_prefix():
    assert _version_tuple("v1.5.0") == (1, 5, 0)


@pytest.mark.parametrize(
    "latest,current,expected",
    [
        ("1.5.1", "1.5.0", True),
        ("1.5.0", "1.5.0", False),
        ("1.5", "1.5.0", False),       # unequal length, zero-padded equal
        ("1.10.0", "1.9.0", True),     # numeric, not lexical
        ("1.4.0", "1.5.0", False),     # never offer a downgrade
        ("v2.0.0", "1.9.9", True),
    ],
)
def test_is_newer(latest, current, expected):
    assert _is_newer(latest, current) is expected


# --- digest parsing -----------------------------------------------------

def test_expected_sha256_parses_prefixed():
    h = "a" * 64
    assert _expected_sha256(f"sha256:{h}") == h


def test_expected_sha256_lowercases():
    assert _expected_sha256("SHA256:" + "A" * 64) == "a" * 64


@pytest.mark.parametrize("value", [None, "", "sha256:zzz", "deadbeef", "sha1:" + "a" * 40])
def test_expected_sha256_rejects_bad(value):
    assert _expected_sha256(value) is None


# --- trusted-url gate ---------------------------------------------------

@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/o/r/releases/download/v1/Setup.exe",
        "https://objects.githubusercontent.com/x/Setup.exe",
        "https://release-assets.githubusercontent.com/x/Setup.exe",
    ],
)
def test_require_trusted_url_accepts_github(url):
    _require_trusted_url(url)  # no raise


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/o/r/Setup.exe",          # not https
        "https://evil.com/Setup.exe",               # wrong host
        "https://github.com.attacker.net/Setup.exe",  # suffix spoof
    ],
)
def test_require_trusted_url_rejects(url):
    with pytest.raises(UpdateError):
        _require_trusted_url(url)


# --- download_installer integrity & sanitization ------------------------

def _patch_urlopen(monkeypatch, payload: bytes, final_url: str):
    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return payload

        def geturl(self):
            return final_url

    monkeypatch.setattr(updater.urllib.request, "urlopen", lambda *a, **k: _Resp())


def _info(name="MarkdownViewer_Setup_v1.6.0.exe", digest=None):
    return UpdateInfo(
        has_update=True,
        current_version="1.5.0",
        latest_version="1.6.0",
        release_url="https://github.com/o/r/releases/tag/v1.6.0",
        asset_name=name,
        asset_url="https://github.com/o/r/releases/download/v1.6.0/" + name,
        asset_digest=digest,
    )


def test_download_verifies_matching_digest(tmp_path, monkeypatch):
    payload = b"installer-bytes"
    digest = "sha256:" + hashlib.sha256(payload).hexdigest()
    _patch_urlopen(monkeypatch, payload, _info().asset_url)
    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(tmp_path))

    target = download_installer(_info(digest=digest))
    assert target.read_bytes() == payload
    assert target.name == "MarkdownViewer_Setup_v1.6.0.exe"


def test_download_rejects_digest_mismatch(tmp_path, monkeypatch):
    _patch_urlopen(monkeypatch, b"tampered", _info().asset_url)
    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(tmp_path))
    bad = "sha256:" + ("b" * 64)
    with pytest.raises(UpdateError, match="integrity"):
        download_installer(_info(digest=bad))
    # nothing should have been written to the temp dir
    assert not list(tmp_path.iterdir())


def test_download_sanitizes_traversal_name(tmp_path, monkeypatch):
    payload = b"x"
    name = "..\\..\\Startup\\MarkdownViewer_Setup_evil.exe"
    info = _info(name=name)
    _patch_urlopen(monkeypatch, payload, info.asset_url)
    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(tmp_path))

    target = download_installer(info)
    # collapsed to a bare basename inside the temp dir, not an escaped path
    assert target.parent == tmp_path
    assert target.name == "MarkdownViewer_Setup_evil.exe"


def test_download_rejects_non_exe(tmp_path, monkeypatch):
    info = _info(name="totally_not_setup.zip")
    _patch_urlopen(monkeypatch, b"x", info.asset_url)
    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(tmp_path))
    with pytest.raises(UpdateError):
        download_installer(info)


def test_download_rejects_untrusted_url(tmp_path, monkeypatch):
    info = UpdateInfo(
        has_update=True, current_version="1.5.0", latest_version="1.6.0",
        release_url="", asset_name="MarkdownViewer_Setup.exe",
        asset_url="https://evil.example/MarkdownViewer_Setup.exe",
    )
    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(tmp_path))
    with pytest.raises(UpdateError):
        download_installer(info)
