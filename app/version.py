APP_NAME = "Markdown Viewer"
VERSION = "1.37.0"

# Shown in the About dialog; update alongside CHANGELOG.md on each release.
RELEASE_NOTES = [
    "新增外部開啟入口：命令列、markdownviewer:// 連結與單一視窗轉送，可直接跳到指定標題或行。",
    "新增無視窗命令列匯出（Word／PPT／PDF／HTML）與 AI Agent 監看面板；切回視窗時自動重掃文件庫。",
    "強化不受信任 Markdown 的安全防護，支援 ==螢光標記== 與 [TOC] 目錄，並修正預覽連結點擊崩潰。",
]

GITHUB_OWNER = "wulove1029"
GITHUB_REPO = "markdown_viewer"
GITHUB_REPOSITORY = f"{GITHUB_OWNER}/{GITHUB_REPO}"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
