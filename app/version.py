APP_NAME = "Markdown Viewer"
VERSION = "1.40.0"

# Shown in the About dialog; update alongside CHANGELOG.md on each release.
RELEASE_NOTES = [
    "「變更 Watchdog 連接埠…」可以真的換 port：正在執行的 Watchdog 會直接搬過去，重開機也不變。",
    "AI Agents 面板會提示目前實際連到哪個 Watchdog；內建的 Agent Watchdog 更新為 1.4.0。",
    "打包版的換 port 未在本機實測；電腦上另外安裝的 Watchdog 需為 1.4.0 以上才能換。",
]

GITHUB_OWNER = "wulove1029"
GITHUB_REPO = "markdown_viewer"
GITHUB_REPOSITORY = f"{GITHUB_OWNER}/{GITHUB_REPO}"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
