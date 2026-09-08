"""Tests for update version comparison and the hardened streaming download."""

import builtins
import hashlib
import threading
import time

import pytest

from app import updater
from app.updater import (
    UpdateCancelled,
    UpdateDigestUnavailable,
    UpdateError,
    UpdateInfo,
    _expected_sha256,
    _is_newer,
    _require_trusted_url,
    _version_tuple,
    download_installer,
    verify_installer,
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


# --- controllable fake transport ----------------------------------------


class _FakeResponse:
    """A urlopen() stand-in that behaves like a real socket-backed response.

    ``read(amt)`` blocks for ``delay`` seconds per chunk and unblocks early
    when ``close()`` is called, which is exactly how the production cancel path
    interrupts a stalled transfer.
    """

    def __init__(
        self,
        chunks,
        *,
        url,
        length="auto",
        delay=0.0,
        fail_after=None,
        header_url=None,
    ):
        self._chunks = list(chunks)
        self._index = 0
        self._url = url
        self._delay = delay
        self._fail_after = fail_after
        self._closed = threading.Event()
        self.read_calls = 0
        if length == "auto":
            self._length = sum(len(c) for c in self._chunks)
        else:
            self._length = length
        self.header_url = header_url

    # urllib response API -------------------------------------------------
    def read(self, amt=None):
        self.read_calls += 1
        if self._closed.is_set():
            raise ValueError("I/O operation on closed file.")
        if self._delay and self._closed.wait(self._delay):
            raise ValueError("I/O operation on closed file.")
        if self._closed.is_set():
            raise ValueError("I/O operation on closed file.")
        if self._fail_after is not None and self._index >= self._fail_after:
            raise ConnectionResetError("connection reset by peer")
        if self._index >= len(self._chunks):
            return b""
        chunk = self._chunks[self._index]
        self._index += 1
        return chunk

    def getheader(self, name, default=None):  # noqa: N802 (Qt/urllib naming)
        if name.lower() == "content-length" and self._length is not None:
            return str(self._length)
        return default

    def geturl(self):
        return self.header_url or self._url

    def close(self):
        self._closed.set()

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        self.close()
        return False


def _install_response(monkeypatch, response=None, *, raises=None):
    created = []

    def _urlopen(*_a, **_k):
        if raises is not None:
            raise raises
        created.append(response)
        return response

    monkeypatch.setattr(updater.urllib.request, "urlopen", _urlopen)
    return created


def _info(name="MarkdownViewer_Setup_v1.6.0.exe", digest=None, payload=None):
    if payload is not None and digest is None:
        digest = "sha256:" + hashlib.sha256(payload).hexdigest()
    return UpdateInfo(
        has_update=True,
        current_version="1.5.0",
        latest_version="1.6.0",
        release_url="https://github.com/o/r/releases/tag/v1.6.0",
        asset_name=name,
        asset_url="https://github.com/o/r/releases/download/v1.6.0/" + name,
        asset_digest=digest,
    )


def _chunks(payload, size=8):
    return [payload[i:i + size] for i in range(0, len(payload), size)]


# --- happy path & streaming --------------------------------------------


def test_download_streams_chunks_and_reports_progress(tmp_path, monkeypatch):
    payload = b"installer-bytes" * 40
    info = _info(payload=payload)
    _install_response(
        monkeypatch, _FakeResponse(_chunks(payload, 64), url=info.asset_url)
    )

    seen = []
    target = download_installer(
        info, dest_dir=tmp_path, progress=lambda d, t: seen.append((d, t))
    )

    assert target.read_bytes() == payload
    assert target.name == "MarkdownViewer_Setup_v1.6.0.exe"
    # A private per-job directory, not the shared temp root.
    assert target.parent.parent == tmp_path
    assert target.parent.name.startswith("mdviewer-update-")
    # No .part survives a successful run.
    assert [p.name for p in target.parent.iterdir()] == [target.name]
    assert seen[0] == (0, len(payload))
    assert seen[-1] == (len(payload), len(payload))
    assert [d for d, _ in seen] == sorted(d for d, _ in seen)


def test_download_never_holds_whole_file_in_one_read(tmp_path, monkeypatch):
    payload = b"z" * 5000
    info = _info(payload=payload)
    response = _FakeResponse(_chunks(payload, 500), url=info.asset_url)
    _install_response(monkeypatch, response)

    download_installer(info, dest_dir=tmp_path, chunk_size=500)
    # 10 data reads plus the trailing empty read that signals EOF.
    assert response.read_calls == 11


def test_download_unknown_length_reports_none_total(tmp_path, monkeypatch):
    payload = b"no-content-length" * 10
    info = _info(payload=payload)
    _install_response(
        monkeypatch,
        _FakeResponse(_chunks(payload, 16), url=info.asset_url, length=None),
    )

    seen = []
    target = download_installer(
        info, dest_dir=tmp_path, progress=lambda d, t: seen.append((d, t))
    )
    assert target.read_bytes() == payload
    assert {t for _, t in seen} == {None}


def test_download_tolerates_slow_response(tmp_path, monkeypatch):
    payload = b"slow-but-alive"
    info = _info(payload=payload)
    _install_response(
        monkeypatch,
        _FakeResponse(_chunks(payload, 4), url=info.asset_url, delay=0.05),
    )
    target = download_installer(info, dest_dir=tmp_path)
    assert target.read_bytes() == payload


# --- failure modes ------------------------------------------------------


def test_download_connect_timeout_is_reported(tmp_path, monkeypatch):
    info = _info(payload=b"x")
    _install_response(monkeypatch, raises=TimeoutError("timed out"))
    with pytest.raises(UpdateError, match="Unable to download update"):
        download_installer(info, dest_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_download_midstream_disconnect_fails_and_cleans_up(tmp_path, monkeypatch):
    payload = b"a" * 64
    info = _info(payload=payload)
    _install_response(
        monkeypatch,
        _FakeResponse(_chunks(payload, 8), url=info.asset_url, fail_after=3),
    )
    with pytest.raises(UpdateError, match="interrupted"):
        download_installer(info, dest_dir=tmp_path, chunk_size=8)
    assert list(tmp_path.iterdir()) == []


def test_download_truncated_against_content_length_fails(tmp_path, monkeypatch):
    payload = b"b" * 100
    info = _info(payload=payload)
    # Server advertises 100 bytes but only sends 40.
    _install_response(
        monkeypatch,
        _FakeResponse([payload[:40]], url=info.asset_url, length=100),
    )
    with pytest.raises(UpdateError, match="incomplete"):
        download_installer(info, dest_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_download_rejects_empty_body(tmp_path, monkeypatch):
    info = _info(digest="sha256:" + hashlib.sha256(b"").hexdigest())
    _install_response(
        monkeypatch, _FakeResponse([], url=info.asset_url, length=None)
    )
    with pytest.raises(UpdateError, match="empty"):
        download_installer(info, dest_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_download_rejects_digest_mismatch(tmp_path, monkeypatch):
    info = _info(digest="sha256:" + ("b" * 64))
    _install_response(
        monkeypatch, _FakeResponse([b"tampered"], url=info.asset_url)
    )
    with pytest.raises(UpdateError, match="integrity"):
        download_installer(info, dest_dir=tmp_path)
    # neither the .part nor its job directory may survive
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("digest", [None, "", "sha256:zzz", "md5:" + "a" * 32])
def test_download_refuses_missing_or_malformed_digest(tmp_path, monkeypatch, digest):
    info = _info(digest=digest)
    _install_response(
        monkeypatch,
        _FakeResponse([b"x"], url=info.asset_url),
    )
    with pytest.raises(UpdateDigestUnavailable) as excinfo:
        download_installer(info, dest_dir=tmp_path)
    # The caller needs the official page so the user can download manually.
    assert excinfo.value.release_url == info.release_url
    assert list(tmp_path.iterdir()) == []


def test_download_reports_disk_full(tmp_path, monkeypatch):
    payload = b"c" * 64
    info = _info(payload=payload)
    _install_response(
        monkeypatch, _FakeResponse(_chunks(payload, 8), url=info.asset_url)
    )

    real_open = builtins.open

    def _full_open(path, mode="r", *a, **k):
        handle = real_open(path, mode, *a, **k)
        if "w" in str(mode):
            def _boom(_data):
                raise OSError(28, "No space left on device")
            handle.write = _boom
        return handle

    monkeypatch.setattr(updater, "open", _full_open, raising=False)
    with pytest.raises(UpdateError, match="Unable to save"):
        download_installer(info, dest_dir=tmp_path, chunk_size=8)
    assert list(tmp_path.iterdir()) == []


def test_download_sanitizes_traversal_name(tmp_path, monkeypatch):
    payload = b"x"
    name = "..\\..\\Startup\\MarkdownViewer_Setup_evil.exe"
    info = _info(name=name, payload=payload)
    _install_response(monkeypatch, _FakeResponse([payload], url=info.asset_url))

    target = download_installer(info, dest_dir=tmp_path)
    # collapsed to a bare basename inside the private job dir, not an escape
    assert target.parent.parent == tmp_path
    assert target.name == "MarkdownViewer_Setup_evil.exe"


def test_download_rejects_non_exe(tmp_path, monkeypatch):
    info = _info(name="totally_not_setup.zip", payload=b"x")
    _install_response(monkeypatch, _FakeResponse([b"x"], url=info.asset_url))
    with pytest.raises(UpdateError):
        download_installer(info, dest_dir=tmp_path)


def test_download_rejects_untrusted_url(tmp_path):
    info = UpdateInfo(
        has_update=True, current_version="1.5.0", latest_version="1.6.0",
        release_url="", asset_name="MarkdownViewer_Setup.exe",
        asset_url="https://evil.example/MarkdownViewer_Setup.exe",
    )
    with pytest.raises(UpdateError):
        download_installer(info, dest_dir=tmp_path)


def test_download_rejects_untrusted_redirect_target(tmp_path, monkeypatch):
    payload = b"x"
    info = _info(payload=payload)
    _install_response(
        monkeypatch,
        _FakeResponse(
            [payload], url=info.asset_url, header_url="https://evil.example/e.exe"
        ),
    )
    with pytest.raises(UpdateError, match="untrusted host"):
        download_installer(info, dest_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_download_defaults_to_system_temp_dir(tmp_path, monkeypatch):
    payload = b"y" * 16
    info = _info(payload=payload)
    _install_response(monkeypatch, _FakeResponse([payload], url=info.asset_url))
    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(tmp_path))
    # Old-style single-argument call still works.
    target = download_installer(info)
    assert target.parent.parent == tmp_path


# --- cancellation -------------------------------------------------------


def test_cancel_before_connect_never_opens_a_socket(tmp_path, monkeypatch):
    info = _info(payload=b"x")
    opened = []
    monkeypatch.setattr(
        updater.urllib.request,
        "urlopen",
        lambda *a, **k: opened.append(1),
    )
    event = threading.Event()
    event.set()
    with pytest.raises(UpdateCancelled):
        download_installer(info, dest_dir=tmp_path, cancel=event)
    assert opened == []
    assert list(tmp_path.iterdir()) == []


def test_cancel_mid_download_unblocks_quickly(tmp_path, monkeypatch):
    """A stalled read must not hold the worker until the socket times out."""
    payload = b"d" * 4096
    info = _info(payload=payload)
    # Each read parks for 30s unless the socket is closed by the cancel watch.
    response = _FakeResponse(
        _chunks(payload, 64), url=info.asset_url, delay=30.0
    )
    _install_response(monkeypatch, response)

    event = threading.Event()
    result = {}

    def _run():
        started = time.monotonic()
        try:
            download_installer(
                info, dest_dir=tmp_path, cancel=event, chunk_size=64
            )
        except BaseException as exc:  # noqa: BLE001 - recorded for assertions
            result["error"] = exc
        result["elapsed"] = time.monotonic() - started

    worker = threading.Thread(target=_run)
    worker.start()
    time.sleep(0.3)          # let it block inside read()
    cancel_started = time.monotonic()
    event.set()
    worker.join(10)
    cancel_elapsed = time.monotonic() - cancel_started

    assert worker.is_alive() is False
    assert isinstance(result["error"], UpdateCancelled)
    # Worst case is bounded by the 50 ms cancel-watch poll, far under the 5 s
    # shutdown budget and the 30 s simulated socket stall.
    assert cancel_elapsed < 2.0, cancel_elapsed
    assert list(tmp_path.iterdir()) == []


def test_cancel_racing_completion_leaves_no_installer(tmp_path, monkeypatch):
    payload = b"e" * 32
    info = _info(payload=payload)
    event = threading.Event()

    class _RacingResponse(_FakeResponse):
        def read(self, amt=None):
            chunk = super().read(amt)
            if not chunk:
                # Cancel lands in the same instant the transfer completes.
                event.set()
            return chunk

    _install_response(
        monkeypatch, _RacingResponse(_chunks(payload, 8), url=info.asset_url)
    )
    with pytest.raises(UpdateCancelled):
        download_installer(
            info, dest_dir=tmp_path, cancel=event, chunk_size=8
        )
    assert list(tmp_path.iterdir()) == []


def test_retry_after_cancel_uses_a_fresh_job_dir(tmp_path, monkeypatch):
    payload = b"f" * 40
    info = _info(payload=payload)
    event = threading.Event()
    event.set()
    with pytest.raises(UpdateCancelled):
        download_installer(info, dest_dir=tmp_path, cancel=event)

    _install_response(
        monkeypatch, _FakeResponse(_chunks(payload, 8), url=info.asset_url)
    )
    target = download_installer(info, dest_dir=tmp_path, cancel=threading.Event())
    assert target.read_bytes() == payload
    # Exactly one job dir remains: the cancelled attempt cleaned up after
    # itself and no Range/resume state was reused.
    assert [p.name for p in tmp_path.iterdir()] == [target.parent.name]


# --- install-time re-verification ---------------------------------------


def test_verify_installer_round_trip(tmp_path):
    path = tmp_path / "MarkdownViewer_Setup.exe"
    path.write_bytes(b"payload")
    digest = "sha256:" + hashlib.sha256(b"payload").hexdigest()
    assert verify_installer(path, digest) is True

    path.write_bytes(b"payload-tampered")
    assert verify_installer(path, digest) is False

    path.unlink()
    assert verify_installer(path, digest) is False
    assert verify_installer(path, None) is False
