"""Reading preference migration, safe values and theme contrast."""

from dataclasses import replace

import pytest
from PySide6.QtCore import QSettings

from app.reading_style import (
    FONTS,
    PRESETS,
    WARM_DARK,
    WARM_LIGHT,
    ReadingPreferences,
    load_reading_preferences,
    normalize_reading_preferences,
    reading_css,
    reading_css_from_settings,
)


def test_empty_settings_use_comfortable_defaults_without_writing(tmp_path):
    settings = QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat)
    assert load_reading_preferences(settings) == PRESETS["comfortable"]
    assert settings.allKeys() == []


def test_legacy_width_and_spacing_are_preserved_without_migration_writes(tmp_path):
    settings = QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat)
    settings.setValue("reading_width", "wide")
    settings.setValue("reading_spacing", "relaxed")
    before = settings.allKeys()
    preferences = load_reading_preferences(settings)
    assert (preferences.width_css, preferences.line_height) == ("1200px", 2.1)
    assert preferences.preset == "custom"
    assert settings.allKeys() == before
    assert "--reading-width: 1200px;" in reading_css_from_settings(settings)


@pytest.mark.parametrize("preset", PRESETS.values())
def test_preset_roundtrip_through_qsettings(tmp_path, preset):
    path = str(tmp_path / "prefs.ini")
    settings = QSettings(path, QSettings.Format.IniFormat)
    for key, value in preset.to_settings().items():
        settings.setValue(key, value)
    settings.sync()
    assert load_reading_preferences(QSettings(path, QSettings.Format.IniFormat)) == preset


def test_malformed_settings_are_bounded_and_cannot_inject_css(tmp_path):
    settings = QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat)
    for key, value in {
        "reading_font": '"; } body { display: none; }',
        "reading_font_size": "inf",
        "reading_custom_width": -100,
        "reading_custom_line_height": "nan",
        "reading_paragraph_spacing": 100,
        "reading_width": "broken",
    }.items():
        settings.setValue(key, value)
    preferences = load_reading_preferences(settings)
    assert preferences.font == "sans"
    assert preferences.font_size == 18
    assert preferences.custom_width == 400
    assert preferences.custom_line_height == 1.85
    assert preferences.paragraph_spacing == 1.6
    css = reading_css_from_settings(settings)
    assert "display: none" not in css
    assert "nan" not in css


def test_custom_values_are_used_and_print_remains_independent():
    preferences = ReadingPreferences(
        preset="custom", width="custom", custom_width=940, spacing="custom",
        custom_line_height=1.7, font_size=21, paragraph_spacing=1.1, paper="warm",
    )
    css = reading_css(preferences=preferences)
    assert css.startswith("@media screen {")
    assert "@media print" not in css
    assert "--reading-width: 940px;" in css
    assert "--reading-line-height: 1.7;" in css
    assert "--reading-font-size: 21px;" in css
    assert "--reading-paragraph-spacing: 1.1em;" in css
    assert "html.theme-dark, body.theme-dark" in css
    assert WARM_LIGHT["page-bg"] in css
    assert WARM_DARK["page-bg"] in css
    assert "--page-bg" not in reading_css(preferences=replace(preferences, paper="follow"))


def test_original_css_arguments_stay_compatible():
    assert "--reading-width: 100%;" in reading_css("full", "relaxed")
    assert "--reading-line-height: 2.1;" in reading_css("full", "relaxed")
    normalized = normalize_reading_preferences(ReadingPreferences(
        font_size=99, custom_line_height=0, custom_width="invalid",
    ))
    assert normalized.font_size == 28
    assert normalized.custom_line_height == 1.35
    assert normalized.custom_width == 860


def _contrast(foreground, background):
    def luminance(color):
        channels = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
        channels = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
                    for v in channels]
        return sum(v * weight for v, weight in zip(channels, (0.2126, 0.7152, 0.0722)))

    values = sorted((luminance(foreground), luminance(background)))
    return (values[1] + 0.05) / (values[0] + 0.05)


@pytest.mark.parametrize("palette", [WARM_LIGHT, WARM_DARK])
def test_warm_paper_text_links_and_selection_meet_contrast_targets(palette):
    for foreground in ("text", "text-muted", "text-subtle", "heading", "accent"):
        assert _contrast(palette[foreground], palette["page-bg"]) >= 4.5
    assert _contrast(palette["text"], palette["accent-soft"]) >= 4.5
    assert _contrast(palette["inline-code-text"], palette["inline-code-bg"]) >= 4.5
    assert _contrast(palette["text-muted"], palette["quote-bg"]) >= 4.5
    assert _contrast(palette["accent-text"], palette["accent"]) >= 4.5


def test_every_font_choice_contains_traditional_chinese_fallbacks():
    assert "Microsoft JhengHei" in FONTS["sans"]
    assert "Noto Sans TC" in FONTS["system"]
    assert "PMingLiU" in FONTS["serif"]
