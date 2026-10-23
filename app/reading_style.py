"""Validated reading preferences, with compatible legacy width/spacing keys."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, replace
from typing import Any

WIDTH_KEY = "reading_width"
SPACING_KEY = "reading_spacing"
PRESET_KEY = "reading_preset"
FONT_KEY = "reading_font"
FONT_SIZE_KEY = "reading_font_size"
CUSTOM_WIDTH_KEY = "reading_custom_width"
CUSTOM_LINE_HEIGHT_KEY = "reading_custom_line_height"
PARAGRAPH_SPACING_KEY = "reading_paragraph_spacing"
PAPER_KEY = "reading_paper"

# The original names and values remain valid for existing installations.
WIDTHS = {"comfortable": "860px", "wide": "1200px", "full": "100%", "compact": "760px"}
SPACINGS = {"compact": "1.6", "comfortable": "1.85", "relaxed": "2.1"}
FONTS = {
    "sans": '"Microsoft JhengHei", "PingFang TC", "Noto Sans TC", "Segoe UI", sans-serif',
    "serif": '"Noto Serif TC", "Source Han Serif TC", "PMingLiU", "Georgia", serif',
    "system": '"Segoe UI", "Microsoft JhengHei UI", "PingFang TC", "Noto Sans TC", sans-serif',
}


@dataclass(frozen=True)
class ReadingPreferences:
    preset: str = "comfortable"
    font: str = "sans"
    font_size: int = 18
    width: str = "comfortable"
    spacing: str = "comfortable"
    custom_width: int = 860
    custom_line_height: float = 1.85
    paragraph_spacing: float = 0.9
    paper: str = "follow"

    @property
    def width_css(self) -> str:
        return f"{self.custom_width}px" if self.width == "custom" else WIDTHS[self.width]

    @property
    def line_height(self) -> float:
        if self.spacing == "custom":
            return self.custom_line_height
        return float(SPACINGS[self.spacing])

    def to_settings(self) -> dict[str, Any]:
        return {SETTINGS_FIELDS[key]: value for key, value in asdict(self).items()}


SETTINGS_FIELDS = {
    "preset": PRESET_KEY,
    "font": FONT_KEY,
    "font_size": FONT_SIZE_KEY,
    "width": WIDTH_KEY,
    "spacing": SPACING_KEY,
    "custom_width": CUSTOM_WIDTH_KEY,
    "custom_line_height": CUSTOM_LINE_HEIGHT_KEY,
    "paragraph_spacing": PARAGRAPH_SPACING_KEY,
    "paper": PAPER_KEY,
}
PRESETS = {
    "comfortable": ReadingPreferences(),
    "compact": ReadingPreferences(
        preset="compact", font_size=16, width="compact", spacing="compact", paragraph_spacing=0.65,
    ),
    "wide": ReadingPreferences(preset="wide", width="wide"),
}

# These screen-only palettes are independent of the surrounding desktop theme.
# Body, muted text, links and focus rings are tested against the paper surface.
WARM_LIGHT = {
    "page-bg": "#f7f0e3", "surface": "#fff8ec", "surface-alt": "#eee4d3",
    "surface-hover": "#e9dcc6", "border": "#d3c4ac", "border-strong": "#9f8968",
    "text": "#40372c", "text-muted": "#665847", "text-subtle": "#72614d",
    "heading": "#30281f", "accent": "#805026", "accent-hover": "#603817",
    "accent-soft": "#e4d3b6", "accent-text": "#ffffff",
    "inline-code-bg": "#ebdfcc", "inline-code-text": "#883b25",
    "quote-bg": "#eee3cf", "quote-border": "#805026", "table-header-bg": "#e9dcc6",
}
WARM_DARK = {
    "page-bg": "#25221e", "surface": "#2e2923", "surface-alt": "#373027",
    "surface-hover": "#41382d", "border": "#564a3a", "border-strong": "#97816a",
    "text": "#e9dfcd", "text-muted": "#cbbda7", "text-subtle": "#b4a48d",
    "heading": "#fff2dc", "accent": "#e5b77c", "accent-hover": "#f4d1a2",
    "accent-soft": "#57432d", "accent-text": "#211a13",
    "inline-code-bg": "#423629", "inline-code-text": "#ffc4a0",
    "quote-bg": "#362d23", "quote-border": "#e5b77c", "table-header-bg": "#40362a",
}


def _choice(value: Any, choices, default: str) -> str:
    return value if isinstance(value, str) and value in choices else default


def _number(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if not math.isfinite(number):
        return default
    return max(minimum, min(maximum, number))


def normalize_reading_preferences(preferences: ReadingPreferences) -> ReadingPreferences:
    """Constrain persisted/imported values before interpolation into CSS."""
    return ReadingPreferences(
        preset=_choice(preferences.preset, (*PRESETS, "custom"), "comfortable"),
        font=_choice(preferences.font, FONTS, "sans"),
        font_size=round(_number(preferences.font_size, 18, 14, 28)),
        width=_choice(preferences.width, (*WIDTHS, "custom"), "comfortable"),
        spacing=_choice(preferences.spacing, (*SPACINGS, "custom"), "comfortable"),
        custom_width=round(_number(preferences.custom_width, 860, 400, 1800)),
        custom_line_height=round(_number(preferences.custom_line_height, 1.85, 1.35, 2.4), 2),
        paragraph_spacing=round(_number(preferences.paragraph_spacing, 0.9, 0.4, 1.6), 2),
        paper=_choice(preferences.paper, ("follow", "warm"), "follow"),
    )


def preset_preferences(name: str, *, paper: str = "follow") -> ReadingPreferences:
    """Apply typography as a unit, keeping the user's independent paper choice."""
    return replace(PRESETS.get(name, PRESETS["comfortable"]), paper=paper)


def load_reading_preferences(settings) -> ReadingPreferences:
    """Read QSettings without rewriting or moving any legacy settings."""
    preset = settings.value(PRESET_KEY, "comfortable")
    defaults = preset_preferences(preset if isinstance(preset, str) else "comfortable")
    values = {
        field: settings.value(key, getattr(defaults, field))
        for field, key in SETTINGS_FIELDS.items()
    }
    result = normalize_reading_preferences(ReadingPreferences(**values))
    # Old versions stored width and spacing separately. Preserve both and label
    # their combination honestly instead of silently applying the new preset.
    expected = preset_preferences(result.preset, paper=result.paper)
    if result.preset != "custom" and replace(result, preset=expected.preset) != expected:
        result = replace(result, preset="custom")
    return result


def reading_css_from_settings(settings) -> str:
    return reading_css(preferences=load_reading_preferences(settings))


def reading_css(width="comfortable", spacing="comfortable", *, preferences=None) -> str:
    """Generate safe screen CSS; the original two positional arguments still work."""
    prefs = normalize_reading_preferences(
        preferences or ReadingPreferences(width=width, spacing=spacing)
    )
    rules = [
        ":root { "
        f"--reading-font-size: {prefs.font_size}px; "
        f"--reading-font-family: {FONTS[prefs.font]}; "
        f"--reading-paragraph-spacing: {prefs.paragraph_spacing:g}em; "
        "}",
        "body { "
        f"--reading-width: {prefs.width_css}; "
        f"--reading-line-height: {prefs.line_height:g}; "
        "}",
    ]
    if prefs.paper == "warm":
        for selector, palette in (
            (":root, body", WARM_LIGHT),
            ("html.theme-dark, body.theme-dark", WARM_DARK),
        ):
            tokens = " ".join(f"--{key}: {value};" for key, value in palette.items())
            rules.append(f"{selector} {{ {tokens} }}")
    return "@media screen {\n" + "\n".join(rules) + "\n}"
