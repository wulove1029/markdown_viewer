APP_NAME = "Markdown Viewer"
VERSION = "1.39.0"

# Shown in the About dialog; update alongside CHANGELOG.md on each release.
RELEASE_NOTES = [
    "內建 AI Agent 監看程式：不必另外安裝 Watchdog 或 Python，打開「AI Agents」面板就能看。",
    "面板直接顯示新的辦公室頁面，深淺色跟隨主題；面板內為唯讀檢視。",
    "內建 Watchdog 尚未在沒有安裝 Python 的乾淨 Windows 上實測，內建模式下的 SSH 遠端監看也未實測。",
    "解除安裝不會刪除 Watchdog 的資料資料夾（~/.aiwatch），因為它與自行安裝的 Watchdog 共用。",
]

GITHUB_OWNER = "wulove1029"
GITHUB_REPO = "markdown_viewer"
GITHUB_REPOSITORY = f"{GITHUB_OWNER}/{GITHUB_REPO}"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
