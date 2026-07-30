APP_NAME = "Markdown Viewer"
VERSION = "1.20.1"

# Shown in the About dialog; update alongside CHANGELOG.md on each release.
RELEASE_NOTES = [
    "閱讀模式原地編輯改為「點三下」觸發，點兩下保留給選取文字複製。",
    "修正原地編輯儲存後畫面先跳回頂端、再跳回原位置的閃爍問題。",
]

GITHUB_OWNER = "wulove1029"
GITHUB_REPO = "markdown_viewer"
GITHUB_REPOSITORY = f"{GITHUB_OWNER}/{GITHUB_REPO}"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
