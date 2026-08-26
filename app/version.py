APP_NAME = "Markdown Viewer"
VERSION = "1.28.0"

# Shown in the About dialog; update alongside CHANGELOG.md on each release.
RELEASE_NOTES = [
    "Markdown 編輯器改用 Office Viewer 4.2 的固定 Vditor fork，對齊單列工具列、左側大綱、區塊把手與程式碼語言／主題控制。",
    "首次載入直接使用正確文件與主題；輸入改採 UTF-16 增量同步，並分文件恢復一般文字及長程式碼的游標、選取與捲動。",
    "儲存、匯出、切換分頁、改名與關閉皆先取得非同步最終快照，保留原子寫入與備份保護且不漏掉最後輸入。",
]

GITHUB_OWNER = "wulove1029"
GITHUB_REPO = "markdown_viewer"
GITHUB_REPOSITORY = f"{GITHUB_OWNER}/{GITHUB_REPO}"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
