"""Conservative filters for optional packaged resources."""


def filter_webengine_locales(entries):
    """Keep Traditional/Simplified Chinese and the English fallback only."""
    keep = {"zh-TW.pak", "zh-CN.pak", "en-US.pak"}
    result = []
    for entry in entries:
        destination = entry[0].replace("\\", "/")
        if ("/qtwebengine_locales/" in destination
                and destination.endswith(".pak")
                and destination.rsplit("/", 1)[-1] not in keep):
            continue
        result.append(entry)
    return result
