APP_NAME = "Markdown Viewer"
VERSION = "1.39.2"

# Shown in the About dialog; update alongside CHANGELOG.md on each release.
RELEASE_NOTES = [
    "「AI Agents」面板只保留星圖，連不上的電腦與連線錯誤不再顯示；面板最窄 385 像素。",
    "電腦忙碌時匯出 Word 或 PowerPoint，Mermaid 圖表與數學式不再可能被默默改成原始碼。",
    "內建 Watchdog 未在沒有 Python 的乾淨 Windows 上實測，內建模式的 SSH 遠端監看也未實測。",
]

GITHUB_OWNER = "wulove1029"
GITHUB_REPO = "markdown_viewer"
GITHUB_REPOSITORY = f"{GITHUB_OWNER}/{GITHUB_REPO}"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
