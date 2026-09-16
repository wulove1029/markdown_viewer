APP_NAME = "Markdown Viewer"
VERSION = "1.33.1"

# Shown in the About dialog; update alongside CHANGELOG.md on each release.
RELEASE_NOTES = [
    "重新啟動會還原上次開啟的分頁、順序與目前文件；從檔案總管開新文件也會保留原有分頁。",
    "分頁操作後自動保存，正常關閉立即寫入，降低意外結束造成的分頁遺失。",
    "修正多份 PDF 切換與重開時的閱讀頁碼還原，包含第一頁；手動關閉全部分頁後不再誤開舊文件。",
]

GITHUB_OWNER = "wulove1029"
GITHUB_REPO = "markdown_viewer"
GITHUB_REPOSITORY = f"{GITHUB_OWNER}/{GITHUB_REPO}"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
