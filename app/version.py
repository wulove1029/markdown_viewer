APP_NAME = "Markdown Viewer"
VERSION = "1.22.0"

# Shown in the About dialog; update alongside CHANGELOG.md on each release.
RELEASE_NOTES = [
    "選取文字後按右鍵可直接翻譯，閱讀模式、PDF 與編輯器皆支援，預設使用免註冊的免費服務。",
    "譯文視窗可即時切換翻譯服務與目標語言，並快取結果，重看同一段不再重複耗用額度。",
    "PDF 支援點兩下選取字詞、點三下選取整行，與閱讀模式操作一致。",
]

GITHUB_OWNER = "wulove1029"
GITHUB_REPO = "markdown_viewer"
GITHUB_REPOSITORY = f"{GITHUB_OWNER}/{GITHUB_REPO}"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
