APP_NAME = "Markdown Viewer"
VERSION = "1.32.0"

# Shown in the About dialog; update alongside CHANGELOG.md on each release.
RELEASE_NOTES = [
    "PDF 內嵌註解直接顯示在頁面上，可拖動卡片、側欄討論串，並能新增回覆、編輯或刪除自己的註解寫回檔案。",
    "大型 Markdown 改用可中止的子程序解析，2 MB 以上先顯示文字前綴，5 MB 首屏約 0.2 秒且切換文件不必等。",
    "更新下載改為非模態串流下載，附 SHA-256 校驗、可取消與重試，下載完成後由你決定何時安裝。",
]

GITHUB_OWNER = "wulove1029"
GITHUB_REPO = "markdown_viewer"
GITHUB_REPOSITORY = f"{GITHUB_OWNER}/{GITHUB_REPO}"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
