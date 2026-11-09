APP_NAME = "Markdown Viewer"
VERSION = "1.38.2"

# Shown in the About dialog; update alongside CHANGELOG.md on each release.
RELEASE_NOTES = [
    "修正預覽剛換內容時按 Esc 沒反應，以及關閉視窗或結束程式時背景工作偶發導致的閃退。",
    "檢查或下載更新時關閉程式，會立即結束，不再卡住數十秒。",
]

GITHUB_OWNER = "wulove1029"
GITHUB_REPO = "markdown_viewer"
GITHUB_REPOSITORY = f"{GITHUB_OWNER}/{GITHUB_REPO}"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
