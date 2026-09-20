# Markdown Viewer — 開發環境建置指南

換新電腦時，依照以下步驟重建開發環境。

---

## 必要軟體

| 軟體         | 版本需求               | 下載                              |
| ------------ | ---------------------- | --------------------------------- |
| Python       | 3.13（與 CI／發版一致） | https://www.python.org/downloads/ |
| Git          | 任意版本               | https://git-scm.com/              |
| Inno Setup 6 | 打包安裝檔才需要       | https://jrsoftware.org/isdl.php   |

> **Windows 注意**：安裝 Python 時勾選 **「Add Python to PATH」**。
> 安裝後用 `py -3 --version` 確認版本，不要用 `python3`（Windows Store stub）。

---

## 1. Clone 專案

```bash
git clone <你的 repo URL>
cd markdown_viewer
```

---

## 2. 安裝 Python 套件

```bash
py -3 -X utf8 -m pip install -r requirements.lock
```

`requirements.lock` 固定 Windows／Python 3.13 的執行、測試與打包相依版本（含間接相依）。
`requirements.txt` 保留開發用途的最低版本，`requirements-dev.txt` 包含 pytest、pytest-qt、pytest-cov、Ruff、mypy、Pillow 與 PyInstaller。
更新 lock 時在乾淨 Python 3.13 虛擬環境安裝這兩份清單，以 `pip freeze` 產生新 lock，執行完整測試與打包驗證後提交；不要從個人全域環境 freeze。

Windows 同時安裝多版 Python 時先設定 `$env:PY_PYTHON3='3.13'`，再以 `py -3 -X utf8 -c "import sys; print(sys.version)"` 確認。CI 也會斷言版本，避免 launcher 選到較新的次版本；設定依據見 [Python launcher 官方文件](https://docs.python.org/3.13/using/windows.html#customizing-default-python-versions)。

主要執行期套件包含：

```
PySide6>=6.11
markdown-it-py>=3.0
mdit_py_plugins>=0.4
linkify-it-py>=2.0
Pygments>=2.17
PyMuPDF>=1.24
python-pptx>=1.0
python-docx>=1.1
```

> PySide6 內含 QtPdf / QtPdfWidgets（PDF 閱讀）與 QtWebEngine（Markdown 渲染），
> 不需額外安裝。KaTeX 數學字型已離線打包於 `assets/katex/`。

---

## 3. 執行程式（開發模式）

本輪完整測試的命令、預設／WebEngine 兩組數字、跳過原因與封裝證據見 [2026-09-21 基準](docs/upgrades/2026-09-21-test-baseline.md)。

```bash
py -3 main.py
```

帶入 .md 檔案直接開啟：

```bash
py -3 main.py path/to/file.md
```

---

## 4. 重建圖示（選用）

只有替換 `ICON/icon.png` 時才需要執行，用來重新產生 `ICON/icon.ico`。

需額外安裝 Pillow：

```bash
py -3 -m pip install Pillow
py -3 tools/build_icon.py
```

---

## 5. 打包成 .exe（選用）

### 5-1. 安裝 PyInstaller

```bash
py -3 -m pip install pyinstaller
```

### 5-2. 執行打包

```bash
py -3 -m PyInstaller markdown_viewer.spec
```

輸出在 `dist/MarkdownViewer/`。

### 5-3. 製作 Windows 安裝檔（本機手動打包）

1. 安裝 **Inno Setup 6**
2. 用 Inno Setup 開啟 `installer.iss`
3. 按 **Build → Compile**
4. 安裝檔輸出至 `installer_output/MarkdownViewer_Setup_v<版號>.exe`

> 正式發布不需要手動打包，請走下方第 6 節的自動發布流程。

---

## 6. 發布新版本（自動化流程）

發布由 GitHub Actions（`.github/workflows/release.yml`）自動完成。
**單純提升版號不會觸發發布，真正的觸發點是推送 `v*.*.*` 格式的 git tag。**

### 6-1. 提升版號

```bash
py -3 tools/bump_version.py 1.2.3
```

此腳本會同步更新兩個檔案的版號：

- `app/version.py` 的 `VERSION`
- `installer.iss` 的 `MyAppVersion`

> **⚠️ 腳本不會自動更新的兩處，每次發版務必手動同步：**
>
> 1. `CHANGELOG.md`：在最上方新增本版條目（Added / Changed / Fixed）。
> 2. `app/version.py` 的 `RELEASE_NOTES`：改成本版的重點修正摘要（1～3 條）。
>    這個清單會顯示在「說明 → 關於 Markdown Viewer」對話框的「本版更新」區塊，
>    忘記改的話，關於視窗會一直顯示上一版的內容。

### 6-2. 提交並推送

```bash
git add -A
git commit -m "Bump version to 1.2.3"
git push
```

此時 push CI 會執行測試、Ruff 與 mypy；全部通過後才推正式版本 tag。

### 6-3. 打 tag 觸發發布

```bash
git tag v1.2.3
git push origin v1.2.3
```

tag 推上去後，GitHub Actions 會在 `windows-latest` 上自動執行：

1. 安裝 Python 3.13 與 `requirements.lock`，先執行測試、Ruff、mypy，失敗停止
2. 安裝 Inno Setup，從 tag 名稱同步版號（本機仍須先同步 CHANGELOG／RELEASE_NOTES）
3. 重建圖示 → PyInstaller 打包 → Inno Setup 編譯安裝檔
4. 從當版 `RELEASE_NOTES` 產生發布說明，將 `installer_output/*.exe` 上傳為 GitHub Release

完成後可在 GitHub Releases 頁面下載 `MarkdownViewer_Setup_v1.2.3.exe`。

---

## 專案結構

以下按功能分群列代表入口，避免把全部模組逐一列出而再次過時。

| 範圍 | 入口與責任 |
|---|---|
| 啟動與視窗 | `main.py`：日誌、URL scheme、單一實例 IPC；`app/window.py`：Qt 組裝、分頁與編輯協調 |
| 編輯與渲染 | `app/editor.py`、`renderer.py`、`render_service.py`、`md_converter.py`：來源編輯、背景解析與預覽；`wysiwyg_view.py`：Office 影子文件橋接 |
| 分頁與復原 | `app/tab_state.py`、`session_state.py`、`recovery.py`、`recovery_browser.py`；編輯唯一真值仍為分頁的 `QTextDocument` |
| PDF | `app/pdf_flow.py` 協調；`pdf_view.py` 閱讀與畫布；`pdf_notes.py`／`pdf_highlights.py` sidecar；`pdf_annotation_writer.py` 內嵌註解；`pymupdf_loader.py` 共用延遲載入 |
| 文件庫與連結 | `app/document_libraries.py`、`file_browser.py`、`global_search.py`、`links.py`、`graph_model.py`／`graph_view.py`；`file_ops.py` 與 `backlink_rename.py` 負責搬移／改名交易 |
| 標籤與屬性 | `app/tag_flow.py`、`tag_index.py`、`doc_tags.py`；`frontmatter_properties.py`／`properties_panel.py` 保守修改 YAML 屬性 |
| 工具流程 | `app/translation_flow.py`、`export_actions.py`、`update_flow.py`；沿用自由函式接收視窗的模式，公開 property 提供共用狀態 |
| 介面與圖表 | `app/theme.py` 共用樣式；`format_commands.py` 格式命令；`mermaid_workspace.py` 與各圖表 model／canvas |
| 離線資源 | `assets/vditor/`、`vditor_host.html`／`vditor_glue.js`、`katex/`、`mermaid.min.js`、預覽 CSS／JS 與 SVG 圖示；不要憑副檔名刪除動態載入資源 |
| 品質與發布 | `tests/`、`pyproject.toml`、`requirements.lock`；`.github/workflows/ci.yml`／`release.yml`；`markdown_viewer.spec`／`installer.iss` |
| 工具與證據 | `tools/`：benchmark、smoke、版號／發布說明工具；`docs/upgrades/`／`docs/audits/`：驗收與仍開放事項；`ICON/`：原始與產生圖示 |

視窗拆分的共享狀態契約見 `translation_flow.py`／`tag_flow.py`／`pdf_flow.py`；WYSIWYG、模式切換與分頁持久化仍維持原有協調，不可把網頁內容改成保存真值。

---

## 常見問題

**Q: `py -3` 找不到指令？**
重新安裝 Python 並勾選「Install launcher for all users」。

**Q: `PySide6` 安裝失敗？**
確認 pip 是最新版：`py -3 -m pip install --upgrade pip`

**Q: 執行時出現 `No module named 'PySide6'`？**
代表 IDE 使用的 Python 與 `py -3` 不同。在 VS Code 按 `Ctrl+Shift+P` → `Python: Select Interpreter`，選與 `py -3` 相同的路徑。

**Q: 圖示在 Windows 沒有更新？**
清除圖示快取：

```bash
ie4uinit.exe -show
```

或重新登出/登入 Windows。
