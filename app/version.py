APP_NAME = "Markdown Viewer"
VERSION = "1.39.3"

# Shown in the About dialog; update alongside CHANGELOG.md on each release.
RELEASE_NOTES = [
    "檢視選單新增「只顯示 AI Agents」（Ctrl+Alt+A），讓 AI Agents 面板佔滿整個視窗。",
    "「設定 Watchdog 位址…」改成預設以外的位址後會真的生效，自動啟動 Watchdog 也會用該連接埠。",
    "內建 Watchdog 未在沒有 Python 的乾淨 Windows 上實測，內建模式的 SSH 遠端監看也未實測。",
]

GITHUB_OWNER = "wulove1029"
GITHUB_REPO = "markdown_viewer"
GITHUB_REPOSITORY = f"{GITHUB_OWNER}/{GITHUB_REPO}"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
