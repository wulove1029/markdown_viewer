APP_NAME = "Markdown Viewer"
VERSION = "1.38.0"

# Shown in the About dialog; update alongside CHANGELOG.md on each release.
RELEASE_NOTES = [
    "修正快速切換文件、在分割視窗即時預覽中輸入，或資料夾改名後立刻關閉視窗時，程式偶發閃退。",
    "大型 PDF 匯出的中文修正改在背景進行，畫面不再停頓；外部改寫檔案後，搜尋與標籤也會即時更新。",
    "AI Agent 監看：連不到 Watchdog 時，可依它登記的 install.json 自動啟動（可從選單關閉）。",
]

GITHUB_OWNER = "wulove1029"
GITHUB_REPO = "markdown_viewer"
GITHUB_REPOSITORY = f"{GITHUB_OWNER}/{GITHUB_REPO}"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
