APP_NAME = "Markdown Viewer"
VERSION = "1.27.0"

# Shown in the About dialog; update alongside CHANGELOG.md on each release.
RELEASE_NOTES = [
    "新增所見即所得（WYSIWYG）Markdown 編輯器：檢視中雙擊即可直接編輯，表格、標題、程式碼區塊以成品樣式呈現，Esc 返回檢視。",
    "編輯器內建完整工具列與右鍵選單：存檔、匯出 PDF／Word／HTML、插入圖片、在資料夾中顯示；匯出永遠使用畫面上最新內容。",
    "段落左側新增 Notion 式 ＋／⋮⋮ 把手，可插入新段落與拖曳搬移區塊；全程離線，儲存仍走原子寫入與備份保護。",
]

GITHUB_OWNER = "wulove1029"
GITHUB_REPO = "markdown_viewer"
GITHUB_REPOSITORY = f"{GITHUB_OWNER}/{GITHUB_REPO}"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
