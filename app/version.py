APP_NAME = "Markdown Viewer"
VERSION = "1.26.0"

# Shown in the About dialog; update alongside CHANGELOG.md on each release.
RELEASE_NOTES = [
    "新增 Markdown／TXT 筆記建立與完整文字編輯流程，支援各分頁獨立草稿、Undo、游標及捲動狀態。",
    "Markdown 編輯器新增格式工具列、快速指令、浮動格式列，以及範本、圖片、附件與最近資源插入。",
    "新增當機復原與外部檔案變更保護，並保留文字編碼及換行格式；背景分頁關閉也不再造成 Qt 閃退。",
]

GITHUB_OWNER = "wulove1029"
GITHUB_REPO = "markdown_viewer"
GITHUB_REPOSITORY = f"{GITHUB_OWNER}/{GITHUB_REPO}"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
