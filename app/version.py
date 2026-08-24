APP_NAME = "Markdown Viewer"
VERSION = "1.24.0"

# Shown in the About dialog; update alongside CHANGELOG.md on each release.
RELEASE_NOTES = [
    "閱讀模式的表格可用網格編輯器直接修改、增刪列欄、設定對齊，並支援試算表資料整片貼入。",
    "就地編輯遇到外部檔案變更、重新載入或模式切換時會先保留內容並確認，不再無聲丟失編輯。",
    "修正 PDF 單一長頁匯出仍被切成多頁的問題，超長文件現在會依完整內容高度輸出成一頁。",
]

GITHUB_OWNER = "wulove1029"
GITHUB_REPO = "markdown_viewer"
GITHUB_REPOSITORY = f"{GITHUB_OWNER}/{GITHUB_REPO}"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
