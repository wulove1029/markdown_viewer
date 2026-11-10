APP_NAME = "Markdown Viewer"
VERSION = "1.39.1"

# Shown in the About dialog; update alongside CHANGELOG.md on each release.
RELEASE_NOTES = [
    "「AI Agents」面板新增星圖檢視並設為預設；面板太窄時自動改顯示辦公室。",
    "面板內仍為唯讀檢視；列表頁遇到很長的名稱會自動換行，不再出現水平捲軸。",
    "內建 Watchdog 未在沒有 Python 的乾淨 Windows 上實測，內建模式的 SSH 遠端監看也未實測。",
]

GITHUB_OWNER = "wulove1029"
GITHUB_REPO = "markdown_viewer"
GITHUB_REPOSITORY = f"{GITHUB_OWNER}/{GITHUB_REPO}"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
