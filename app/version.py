APP_NAME = "Markdown Viewer"
VERSION = "1.34.0"

# Shown in the About dialog; update alongside CHANGELOG.md on each release.
RELEASE_NOTES = [
    "關聯圖支援 Markdown 相對連結、重名路徑辨識，以及文件庫／資料夾／標籤分群。",
    "新增 YAML 屬性面板、筆記改名同步 wikilink、外部異動差異與保留雙方，"
    "改善 PDF 註解編輯與匯出完成操作。",
    "改善背景改名、高亮快取與封裝體積，補強存檔警示、設定相容性及自動測試；完整變更與尚未達標項目見專案交付紀錄。",
]

GITHUB_OWNER = "wulove1029"
GITHUB_REPO = "markdown_viewer"
GITHUB_REPOSITORY = f"{GITHUB_OWNER}/{GITHUB_REPO}"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
