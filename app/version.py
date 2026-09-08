APP_NAME = "Markdown Viewer"
VERSION = "1.31.0"

# Shown in the About dialog; update alongside CHANGELOG.md on each release.
RELEASE_NOTES = [
    "啟動時列出待復原草稿，也可從檔案選單重新查看、繼續編輯或另存副本。",
    "搬移文件同步處理註記、備份與未儲存草稿，重算支援的相對連結；無法安全處理時保留原檔並提示。",
    "全文搜尋支援 Markdown 與 TXT、鍵盤操作及來源定位；快速開啟涵蓋文件庫並顯示路徑，偏好設定可捲動。",
]

GITHUB_OWNER = "wulove1029"
GITHUB_REPO = "markdown_viewer"
GITHUB_REPOSITORY = f"{GITHUB_OWNER}/{GITHUB_REPO}"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
