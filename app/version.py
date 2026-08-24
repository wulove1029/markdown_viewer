APP_NAME = "Markdown Viewer"
VERSION = "1.25.0"

# Shown in the About dialog; update alongside CHANGELOG.md on each release.
RELEASE_NOTES = [
    "文件庫資料夾樹新增清楚的展開箭頭與階層導引線，並以不同圖示區分文件庫、收合與展開資料夾。",
    "目錄改用真正的標題層級縮排與緊湊列距，目前章節更醒目，長標題也不再產生水平捲軸。",
    "修正 PDF 大綱第一頁無法點擊跳轉的問題，並改善目錄在亮暗主題與高 DPI 下的顯示。",
]

GITHUB_OWNER = "wulove1029"
GITHUB_REPO = "markdown_viewer"
GITHUB_REPOSITORY = f"{GITHUB_OWNER}/{GITHUB_REPO}"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
