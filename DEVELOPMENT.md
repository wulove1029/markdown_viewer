# Markdown Viewer — 開發環境建置指南

換新電腦時，依照以下步驟重建開發環境。

---

## 必要軟體

| 軟體         | 版本需求               | 下載                              |
| ------------ | ---------------------- | --------------------------------- |
| Python       | 3.11 以上（建議 3.14） | https://www.python.org/downloads/ |
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
py -3 -m pip install -r requirements.txt
```

`requirements.txt` 包含：

```
PyQt6>=6.6
PyQt6-WebEngine>=6.6
markdown-it-py>=3.0
mdit_py_plugins>=0.4
Pygments>=2.17
```

---

## 3. 執行程式（開發模式）

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

### 6-2. 提交並推送

```bash
git add -A
git commit -m "Bump version to 1.2.3"
git push
```

此時 CI 不會有任何動作。

### 6-3. 打 tag 觸發發布

```bash
git tag v1.2.3
git push origin v1.2.3
```

tag 推上去後，GitHub Actions 會在 `windows-latest` 上自動執行：

1. 安裝 Python 3.13、相依套件、PyInstaller、Inno Setup
2. 從 tag 名稱反推版號，再跑一次 `bump_version.py` 同步（以 tag 為準，即使本機忘了改版號也沒關係）
3. 重建圖示 → PyInstaller 打包 → Inno Setup 編譯安裝檔
4. 將 `installer_output/*.exe` 上傳為 GitHub Release

完成後可在 GitHub Releases 頁面下載 `MarkdownViewer_Setup_v1.2.3.exe`。

---

## 專案結構

```
markdown_viewer/
├── main.py                  # 程式進入點
├── requirements.txt         # Python 套件清單
├── markdown_viewer.spec     # PyInstaller 設定
├── installer.iss            # Inno Setup 安裝檔腳本
├── .github/
│   └── workflows/
│       └── release.yml      # tag 觸發的自動發布流程
├── ICON/
│   ├── icon.png             # 原始圖示（RGBA PNG）
│   └── icon.ico             # 多尺寸 ICO（自動產生）
├── app/
│   ├── version.py           # 版號（由 bump_version.py 更新）
│   ├── window.py            # 主視窗
│   ├── ribbon.py            # 左側圖示列
│   ├── left_panel.py        # 左側面板（檔案/最近/目錄）
│   ├── file_browser.py      # 檔案瀏覽樹
│   ├── recent_files.py      # 最近開啟清單
│   ├── toc.py               # 目錄（Table of Contents）
│   ├── renderer.py          # Markdown 渲染（QWebEngineView）
│   └── md_converter.py      # Markdown → HTML 轉換
├── assets/
│   └── obsidian-light.css   # 渲染樣式
└── tools/
    ├── build_icon.py        # PNG → ICO 轉換工具
    └── bump_version.py      # 版號同步工具
```

---

## 常見問題

**Q: `py -3` 找不到指令？**
重新安裝 Python 並勾選「Install launcher for all users」。

**Q: `PyQt6` 安裝失敗？**
確認 pip 是最新版：`py -3 -m pip install --upgrade pip`

**Q: 執行時出現 `No module named 'PyQt6'`？**
代表 IDE 使用的 Python 與 `py -3` 不同。在 VS Code 按 `Ctrl+Shift+P` → `Python: Select Interpreter`，選與 `py -3` 相同的路徑。

**Q: 圖示在 Windows 沒有更新？**
清除圖示快取：

```bash
ie4uinit.exe -show
```

或重新登出/登入 Windows。
