APP_NAME = "Markdown Viewer"
VERSION = "1.35.0"

# Shown in the About dialog; update alongside CHANGELOG.md on each release.
RELEASE_NOTES = [
    "改善換檔與分頁切換：共用標籤解析、取消過期預覽，並保留閱讀位置與未儲存的編輯內容。",
    "PDF 返回原頁可重用有界預覽與頁面尺寸快取，大型純文字改為背景讀取及分批建立內容。",
    "改善複雜 Markdown 的部分預覽與大型 HTML 載入，"
    "補強來源／Office 返回預覽及跨檔取消；量測與限制見交付紀錄。",
]

GITHUB_OWNER = "wulove1029"
GITHUB_REPO = "markdown_viewer"
GITHUB_REPOSITORY = f"{GITHUB_OWNER}/{GITHUB_REPO}"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
