APP_NAME = "Markdown Viewer"
VERSION = "1.38.1"

# Shown in the About dialog; update alongside CHANGELOG.md on each release.
RELEASE_NOTES = [
    "修正開啟文件的同時關閉視窗或分頁時，程式偶發閃退；其他背景工作的同類問題也一併修正。",
    "PDF 註解讀取、存檔權限檢查與翻譯完成後會釋放記憶體，長時間使用不再持續成長。",
]

GITHUB_OWNER = "wulove1029"
GITHUB_REPO = "markdown_viewer"
GITHUB_REPOSITORY = f"{GITHUB_OWNER}/{GITHUB_REPO}"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
