APP_NAME = "Markdown Viewer"
VERSION = "1.30.1"

# Shown in the About dialog; update alongside CHANGELOG.md on each release.
RELEASE_NOTES = [
    "修正深色模式下「偏好設定」對話框分頁內容白底、文字看不見的問題；分頁列與群組框現在跟隨主題。",
    "（1.30.0）主工具列改為文字模式選單，外觀偏好新增預覽行寬與行距，空白工作台改為首頁；加快第二次啟動、文件樹建立與 PDF 重複開啟。",
]

GITHUB_OWNER = "wulove1029"
GITHUB_REPO = "markdown_viewer"
GITHUB_REPOSITORY = f"{GITHUB_OWNER}/{GITHUB_REPO}"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
