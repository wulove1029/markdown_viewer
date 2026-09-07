APP_NAME = "Markdown Viewer"
VERSION = "1.30.2"

# Shown in the About dialog; update alongside CHANGELOG.md on each release.
RELEASE_NOTES = [
    "修正 PDF 的 Ctrl+滾輪縮放會影響 Markdown 的問題：PDF 與文字內容縮放現在各自獨立記憶，Ctrl+=／- 只作用於目前畫面上的文件類型。",
    "首次啟動會把舊設定中 PDF 留下的縮放值搬到 PDF 專用設定，Markdown 縮放還原為 100%。",
    "偏好設定的「內容縮放」在目前值不在級距內時顯示最接近的級距，不再固定顯示 100%。",
]

GITHUB_OWNER = "wulove1029"
GITHUB_REPO = "markdown_viewer"
GITHUB_REPOSITORY = f"{GITHUB_OWNER}/{GITHUB_REPO}"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
