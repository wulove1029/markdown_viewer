from tools.package_policy import filter_webengine_locales


def test_locale_filter_keeps_required_locales_and_other_resource_packs():
    entries = [(f"PySide6/translations/qtwebengine_locales/{locale}.pak", "source", "DATA")
               for locale in ("zh-TW", "zh-CN", "en-US", "fr", "ja")]
    other = ("PySide6/resources/qtwebengine_resources.pak", "source", "DATA")
    result = filter_webengine_locales(entries + [other])
    assert result == entries[:3] + [other]
