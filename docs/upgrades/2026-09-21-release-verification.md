# 1.34.0 發布驗證

- 來源提交：`5c0c3cb1cec116cd7ae970652974840e3745ff83`。
- 正式 tag：`v1.34.0`，已推送至 origin；發布時 main 同步相同來源，其後僅補交付文件。
- [Release workflow](https://github.com/wulove1029/markdown_viewer/actions/runs/35530053948)：成功；1812 passed／82 skipped，99.83s；Ruff、mypy、PyInstaller 及 Inno Setup 編譯均通過（Inno 編譯 192.641s）。
- [main CI](https://github.com/wulove1029/markdown_viewer/actions/runs/35530045007)：成功；1894 collected，1812 passed／82 skipped，84.22s；Ruff 與 mypy 五檔通過。
- [tag CI](https://github.com/wulove1029/markdown_viewer/actions/runs/35530054007)：成功。
- [GitHub Release](https://github.com/wulove1029/markdown_viewer/releases/tag/v1.34.0)：已於台北時間 2026-09-21 02:53:53 正式發布，為 latest，非草稿、非預覽版。說明逐行比對當版三條 RELEASE_NOTES 相同（僅 Windows CRLF 換行差異）。
- [安裝檔下載](https://github.com/wulove1029/markdown_viewer/releases/download/v1.34.0/MarkdownViewer_Setup_v1.34.0.exe)：`MarkdownViewer_Setup_v1.34.0.exe`，166,971,281 bytes（159.24 MiB）；已完整下載，大小與 SHA-256 均符合 GitHub 資產紀錄，驗證程序 exit 0。PE 產品名稱為 Markdown Viewer，ProductVersion 為 1.34.0。
- GitHub 公布 SHA-256：`8fd104e97f6a2889b1a94bb3f156eaa6c05c5c91374645e9fca88b207af66d4d`。

獨立 reviewer 以 GitHub API 讀回三個 workflow、tag 解參照、latest 狀態、資產大小／digest 與更新說明，並抽查發布原始 log，覆核通過。
下載完成後，reviewer 另以 Get-FileHash 獨立核對本機安裝檔，SHA-256、大小與產品版號均符合；最終文件 read-back 通過。

本機驗證檔案位於 `%TEMP%\mdv-published-1.34.0\`，包含安裝程式及 `verification.json`。發布原始 log：`%TEMP%\mdv-release-1.34.0.log`；main CI log：`%TEMP%\mdv-release-main-ci.log`。未在使用者的正式環境執行安裝；實體安裝、高 DPI、多螢幕及 SmartScreen 仍依開放清單補驗。

發布前測試與未達標項目見 [測試基準](2026-09-21-test-baseline.md)、[逐項交付索引](2026-09-21-delivery-index.md) 及 [仍開放清單](2026-09-21-remaining-work.md)。
