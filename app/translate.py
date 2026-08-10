"""Translate a text selection through a free web translation service.

Each provider is a plain function over ``(text, target, api_key)`` so it can be
exercised without Qt; only :class:`TranslateTask` knows about the event loop.
The network call runs on a worker because ``urlopen`` blocks and every caller is
a context-menu handler on the UI thread.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from dataclasses import dataclass

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

# ── QSettings keys (never rename: older configs must migrate) ────────────

PROVIDER_KEY = "translate_provider"
TARGET_KEY = "translate_target_lang"
DEEPL_KEY = "translate_deepl_key"

DEFAULT_PROVIDER = "mymemory"
DEFAULT_TARGET = "zh-TW"

# A whole-chapter selection would burn the free daily quota in one click, so
# refuse it up front instead of firing dozens of requests.
MAX_CHARS = 3000

_TIMEOUT = 15
_USER_AGENT = "MarkdownViewer (markdown-viewer translation)"


class TranslationError(RuntimeError):
    """A provider could not produce a translation."""


@dataclass(frozen=True)
class ProviderInfo:
    key: str
    label: str
    needs_api_key: bool
    note: str


PROVIDERS: tuple[ProviderInfo, ...] = (
    ProviderInfo(
        "mymemory",
        "MyMemory（免費、免註冊）",
        False,
        "官方免費層，匿名約 5,000 字/天。品質中等，適合一般段落。",
    ),
    ProviderInfo(
        "google",
        "Google 翻譯（免費、免註冊）",
        False,
        "品質較好但使用非官方端點，Google 可能限流或變更；僅供個人使用。",
    ),
    ProviderInfo(
        "deepl",
        "DeepL（需 API 金鑰）",
        True,
        "中譯品質最佳。Free 方案每月 50 萬字元，需至 deepl.com 註冊取得金鑰。",
    ),
)

TARGETS: tuple[tuple[str, str], ...] = (
    ("zh-TW", "繁體中文"),
    ("zh-CN", "简体中文"),
    ("en", "English"),
    ("ja", "日本語"),
)

_PROVIDER_KEYS = {p.key for p in PROVIDERS}
_TARGET_KEYS = {code for code, _ in TARGETS}


def provider_info(key: str) -> ProviderInfo:
    """The registry entry for *key*, falling back to the default provider."""
    for info in PROVIDERS:
        if info.key == key:
            return info
    return PROVIDERS[0]


def normalize_provider(value) -> str:
    text = str(value or "").strip()
    return text if text in _PROVIDER_KEYS else DEFAULT_PROVIDER


def normalize_target(value) -> str:
    text = str(value or "").strip()
    return text if text in _TARGET_KEYS else DEFAULT_TARGET


def target_label(code: str) -> str:
    for key, label in TARGETS:
        if key == code:
            return label
    return code


# ── text helpers ────────────────────────────────────────────────────────

# Longest first: a paragraph break is a better cut than a bare space.
_BREAKS = ("\n\n", "\n", "。", "！", "？", ". ", "! ", "? ", "; ", ", ", " ")


def split_chunks(text: str, limit: int) -> list[str]:
    """Split *text* into pieces of at most *limit* characters.

    Cuts on the latest sentence-ish boundary inside the window so a provider
    never sees half a sentence; falls back to a hard split for text with no
    break at all (a long URL, CJK without punctuation).
    """
    if limit <= 0:
        raise ValueError("limit must be positive")
    chunks: list[str] = []
    rest = text
    while len(rest) > limit:
        window = rest[:limit]
        cut = 0
        for token in _BREAKS:
            idx = window.rfind(token)
            if idx > 0:
                cut = max(cut, idx + len(token))
        if cut <= 0:
            cut = limit
        chunks.append(rest[:cut])
        rest = rest[cut:]
    if rest:
        chunks.append(rest)
    return chunks


def _join_separator(target: str) -> str:
    """Chunk joiner: CJK runs together, space-delimited languages do not."""
    return "" if target.startswith(("zh", "ja", "ko")) else " "


def _is_cjk(ch: str) -> bool:
    return "㐀" <= ch <= "鿿" or "豈" <= ch <= "﫿"


def _guess_source(text: str) -> str:
    """Crude source-language guess for providers that require one."""
    letters = [c for c in text if c.isalpha() or _is_cjk(c)]
    if not letters:
        return "en"
    cjk = sum(1 for c in letters if _is_cjk(c))
    return "zh-TW" if cjk * 5 >= len(letters) else "en"


# ── HTTP helpers ────────────────────────────────────────────────────────

def _read(req: urllib.request.Request) -> bytes:
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # nosec - fixed hosts
            return resp.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:200].strip()
        except Exception:  # noqa: BLE001 - the status code is the useful part
            pass
        if exc.code in (401, 403):
            raise TranslationError("翻譯服務拒絕存取（金鑰無效或額度用盡）") from exc
        if exc.code == 429:
            raise TranslationError("翻譯服務已達流量上限，請稍後再試") from exc
        raise TranslationError(
            f"翻譯服務回應 HTTP {exc.code}" + (f"：{detail}" if detail else "")
        ) from exc
    except TimeoutError as exc:
        raise TranslationError("翻譯服務逾時，請檢查網路連線") from exc
    except urllib.error.URLError as exc:
        raise TranslationError(f"無法連線翻譯服務：{exc.reason}") from exc


def _get_json(url: str) -> object:
    raw = _read(urllib.request.Request(url, headers={"User-Agent": _USER_AGENT}))
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except json.JSONDecodeError as exc:
        raise TranslationError("翻譯服務回應格式無法解析") from exc


def _post_json(url: str, data: dict, headers: dict) -> object:
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "User-Agent": _USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
            **headers,
        },
    )
    raw = _read(req)
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except json.JSONDecodeError as exc:
        raise TranslationError("翻譯服務回應格式無法解析") from exc


# ── providers ───────────────────────────────────────────────────────────

# MyMemory rejects anything past 500 characters per query.
_MYMEMORY_LIMIT = 480
# Keep the whole GET URL comfortably inside what the endpoint accepts.
_GOOGLE_LIMIT = 1800


def _mymemory(text: str, target: str, _api_key: str) -> str:
    source = _guess_source(text)
    if source == target:
        source = "en" if not target.startswith("en") else "zh-TW"
    out: list[str] = []
    for chunk in split_chunks(text, _MYMEMORY_LIMIT):
        query = urllib.parse.urlencode(
            {"q": chunk, "langpair": f"{source}|{target}"}
        )
        payload = _get_json(f"https://api.mymemory.translated.net/get?{query}")
        if not isinstance(payload, dict):
            raise TranslationError("MyMemory 回應格式非預期")
        status = payload.get("responseStatus")
        # The field is a string on some error paths, an int on success.
        if str(status) != "200":
            detail = str(payload.get("responseDetails") or "").strip()
            raise TranslationError(detail or f"MyMemory 回應狀態 {status}")
        data = payload.get("responseData") or {}
        translated = str(data.get("translatedText") or "")
        if not translated.strip():
            raise TranslationError("MyMemory 未回傳譯文")
        out.append(translated)
    return _join_separator(target).join(out)


def _google(text: str, target: str, _api_key: str) -> str:
    out: list[str] = []
    for chunk in split_chunks(text, _GOOGLE_LIMIT):
        query = urllib.parse.urlencode(
            {"client": "gtx", "sl": "auto", "tl": target, "dt": "t", "q": chunk}
        )
        payload = _get_json(
            f"https://translate.googleapis.com/translate_a/single?{query}"
        )
        # Shape: [[["translated","source",...], ...], ...]
        if not isinstance(payload, list) or not payload:
            raise TranslationError("Google 翻譯回應格式非預期")
        segments = payload[0]
        if not isinstance(segments, list):
            raise TranslationError("Google 翻譯回應格式非預期")
        piece = "".join(
            str(seg[0])
            for seg in segments
            if isinstance(seg, list) and seg and seg[0]
        )
        if not piece.strip():
            raise TranslationError("Google 翻譯未回傳譯文")
        out.append(piece)
    return _join_separator(target).join(out)


_DEEPL_TARGETS = {"zh-TW": "ZH-HANT", "zh-CN": "ZH-HANS", "en": "EN-US", "ja": "JA"}


def _deepl_call(url: str, key: str, chunk: str, target: str) -> str:
    payload = _post_json(
        url,
        {"text": chunk, "target_lang": target},
        {"Authorization": f"DeepL-Auth-Key {key}"},
    )
    if not isinstance(payload, dict):
        raise TranslationError("DeepL 回應格式非預期")
    items = payload.get("translations") or []
    if not items or not isinstance(items, list):
        raise TranslationError("DeepL 未回傳譯文")
    return str(items[0].get("text") or "")


def _deepl(text: str, target: str, api_key: str) -> str:
    key = (api_key or "").strip()
    if not key:
        raise TranslationError("尚未設定 DeepL API 金鑰（偏好設定 → 翻譯）")
    # Free-tier keys carry a ":fx" suffix and must use the free host.
    url = (
        "https://api-free.deepl.com/v2/translate"
        if key.endswith(":fx")
        else "https://api.deepl.com/v2/translate"
    )
    lang = _DEEPL_TARGETS.get(target, "ZH-HANT")
    out: list[str] = []
    for chunk in split_chunks(text, MAX_CHARS):
        try:
            out.append(_deepl_call(url, key, chunk, lang))
        except TranslationError:
            # Older accounts only know the plain "ZH"/"EN" codes.
            base = lang.split("-", 1)[0]
            if base == lang:
                raise
            out.append(_deepl_call(url, key, chunk, base))
            lang = base
    return _join_separator(target).join(out)


_HANDLERS = {"mymemory": _mymemory, "google": _google, "deepl": _deepl}


def translate(text: str, *, provider: str, target: str, api_key: str = "") -> str:
    """Translate *text* into *target*, raising :class:`TranslationError` on failure."""
    cleaned = (text or "").strip()
    if not cleaned:
        raise TranslationError("沒有選取任何文字")
    if len(cleaned) > MAX_CHARS:
        raise TranslationError(
            f"選取範圍過長（{len(cleaned)} 字元，上限 {MAX_CHARS}）。請分段翻譯。"
        )
    handler = _HANDLERS.get(normalize_provider(provider))
    if handler is None:  # pragma: no cover - normalize_provider guarantees a hit
        raise TranslationError(f"未知的翻譯服務：{provider}")
    result = handler(cleaned, normalize_target(target), api_key)
    if not result.strip():
        raise TranslationError("翻譯服務回傳空白結果")
    return result.strip()


# ── result cache ────────────────────────────────────────────────────────

# Re-reading a page should not re-spend the daily free quota. Bounded so a
# long session cannot grow it without limit; oldest entries are evicted first.
CACHE_LIMIT = 256

_cache: "OrderedDict[tuple[str, str, str], str]" = OrderedDict()


def _cache_key(text: str, provider: str, target: str) -> tuple[str, str, str]:
    return (
        normalize_provider(provider),
        normalize_target(target),
        (text or "").strip(),
    )


def cached_translation(text: str, provider: str, target: str) -> str | None:
    """The stored translation for this exact request, or None."""
    key = _cache_key(text, provider, target)
    result = _cache.get(key)
    if result is not None:
        _cache.move_to_end(key)  # keep hot entries alive (LRU)
    return result


def remember_translation(text: str, provider: str, target: str, result: str) -> None:
    key = _cache_key(text, provider, target)
    _cache[key] = result
    _cache.move_to_end(key)
    while len(_cache) > CACHE_LIMIT:
        _cache.popitem(last=False)


def clear_cache() -> None:
    _cache.clear()


def cache_size() -> int:
    return len(_cache)


# ── Qt worker ───────────────────────────────────────────────────────────

class _TaskSignals(QObject):
    finished = Signal(int, str)   # request id, translated text
    failed = Signal(int, str)     # request id, user-facing message


class TranslateTask(QRunnable):
    """Run one translation on the shared thread pool.

    ``request_id`` lets the receiver drop results from a superseded selection
    instead of overwriting whatever the user asked for most recently.
    """

    def __init__(
        self,
        request_id: int,
        text: str,
        *,
        provider: str,
        target: str,
        api_key: str = "",
    ):
        super().__init__()
        self.signals = _TaskSignals()
        self._id = int(request_id)
        self._text = text
        self._provider = provider
        self._target = target
        self._api_key = api_key

    def run(self):  # pragma: no cover - exercised through the UI
        try:
            result = translate(
                self._text,
                provider=self._provider,
                target=self._target,
                api_key=self._api_key,
            )
        except TranslationError as exc:
            self.signals.failed.emit(self._id, str(exc))
        except Exception as exc:  # noqa: BLE001 - a worker crash must not kill the app
            self.signals.failed.emit(self._id, f"翻譯失敗：{exc}")
        else:
            self.signals.finished.emit(self._id, result)


# QThreadPool.start() does not keep the Python wrapper alive, so a task left
# only in a local would be collected mid-flight and its signals destroyed with
# it — the result then never reaches the window. Hold a reference until the
# task reports back.
_INFLIGHT: set[TranslateTask] = set()


def start_translation(
    request_id: int,
    text: str,
    *,
    provider: str,
    target: str,
    api_key: str = "",
    on_finished,
    on_failed,
) -> None:
    """Queue a translation and route its result to the given callbacks."""
    task = TranslateTask(
        request_id, text, provider=provider, target=target, api_key=api_key
    )
    # Python owns the lifetime now; Qt must not delete it out from under us.
    task.setAutoDelete(False)
    _INFLIGHT.add(task)

    def _finished(rid, result, _task=task):
        _INFLIGHT.discard(_task)
        # Runs on the GUI thread, so the cache needs no lock of its own.
        remember_translation(text, provider, target, result)
        on_finished(rid, result)

    def _failed(rid, message, _task=task):
        _INFLIGHT.discard(_task)
        on_failed(rid, message)

    task.signals.finished.connect(_finished)
    task.signals.failed.connect(_failed)
    QThreadPool.globalInstance().start(task)


def inflight_count() -> int:
    """Number of translations still running (used by tests)."""
    return len(_INFLIGHT)
