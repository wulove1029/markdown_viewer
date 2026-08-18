APP_NAME = "Markdown Viewer"
VERSION = "1.23.0"

# Shown in the About dialog; update alongside CHANGELOG.md on each release.
RELEASE_NOTES = [
    "啟動更快：文件庫檔案樹改在背景掃描，主視窗與文件內容不再等整個文件庫掃完才出現。",
    "大型文件庫（數千個資料夾、USB 或冷快取磁碟）掃描時間縮短一個數量級，掃描中會顯示進度提示。",
    "PDF 元件改為第一次開啟 PDF 時才載入，縮短冷啟動時間。",
]

GITHUB_OWNER = "wulove1029"
GITHUB_REPO = "markdown_viewer"
GITHUB_REPOSITORY = f"{GITHUB_OWNER}/{GITHUB_REPO}"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
