APP_NAME = "Markdown Viewer"
VERSION = "1.37.1"

# Shown in the About dialog; update alongside CHANGELOG.md on each release.
RELEASE_NOTES = [
    "修正匯出的 PDF 在複製、搜尋或擷取中文時，「一」「二」「目」等字變成外觀相同的部首字元。",
    "AI Agent 監看的 Watchdog 啟動檔不再預設為特定電腦的路徑；未設定時面板會說明如何指定。",
]

GITHUB_OWNER = "wulove1029"
GITHUB_REPO = "markdown_viewer"
GITHUB_REPOSITORY = f"{GITHUB_OWNER}/{GITHUB_REPO}"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
