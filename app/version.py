APP_NAME = "Markdown Viewer"
VERSION = "1.29.0"

# Shown in the About dialog; update alongside CHANGELOG.md on each release.
RELEASE_NOTES = [
    "新增原始 Markdown、並排預覽與 Office 視覺編輯三條明確入口；新建筆記可選模式，分頁與工具列會顯示彩色模式標籤。",
    "Office 視覺編輯改採安全預設與相容性提醒；切回原始 Markdown 前等待最新快照，避免特殊語法或最後輸入遺失。",
    "修正 Office 模式 Ctrl+滾輪與鍵盤縮放節流，採用合併輸入和標準級距，降低重複排版。",
]

GITHUB_OWNER = "wulove1029"
GITHUB_REPO = "markdown_viewer"
GITHUB_REPOSITORY = f"{GITHUB_OWNER}/{GITHUB_REPO}"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
