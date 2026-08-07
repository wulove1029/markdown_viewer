APP_NAME = "Markdown Viewer"
VERSION = "1.21.0"

# Shown in the About dialog; update alongside CHANGELOG.md on each release.
RELEASE_NOTES = [
    "PDF 支援 Ctrl+滑鼠滾輪依游標位置縮放，連續操作更加直覺。",
    "PDF 縮放加入即時預覽、背景精繪與可見區域分塊渲染，大型文件操作更順暢。",
    "PDF 大綱改為背景載入，並移除啟動時重複掃描文件庫的效能負擔。",
]

GITHUB_OWNER = "wulove1029"
GITHUB_REPO = "markdown_viewer"
GITHUB_REPOSITORY = f"{GITHUB_OWNER}/{GITHUB_REPO}"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
