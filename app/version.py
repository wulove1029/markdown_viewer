APP_NAME = "Markdown Viewer"
VERSION = "1.30.0"

# Shown in the About dialog; update alongside CHANGELOG.md on each release.
RELEASE_NOTES = [
    "主工具列改為文字模式選單，可直接切換閱讀、Markdown、並排預覽與 Office 編輯；外觀偏好新增預覽行寬與行距，空白工作台改為可開檔的首頁。",
    "加快第二次啟動、文件樹建立與 PDF 重複開啟；標籤未變更時不再重寫索引，Markdown 快取改為有容量上限的 LRU。",
    "修正淺色介面下模式下拉選單黑底深色字、選項無法辨識的問題。",
]

GITHUB_OWNER = "wulove1029"
GITHUB_REPO = "markdown_viewer"
GITHUB_REPOSITORY = f"{GITHUB_OWNER}/{GITHUB_REPO}"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
