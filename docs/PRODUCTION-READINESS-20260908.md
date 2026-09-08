# 1.31.0 消費者體驗升級驗收

日期：2026-09-08。狀態：第一批程式、回歸與本機封裝驗收完成；安裝程式建置成功，尚未安裝或公開發布。

安裝程式：`E:\markdown_viewer\installer_output\consumer-upgrade-1.31.0\MarkdownViewer_Setup_v1.31.0.exe`。執行目錄：`E:\markdown_viewer\dist\consumer-upgrade-1.31.0\MarkdownViewer`。

## 本批交付

- 啟動與檔案選單可發現待復原草稿，包含原檔已不存在的內容；支援繼續、稍後、另存副本。
- 文件搬移整合主檔、註記、高亮、備份與未儲存草稿，搬移前取得 Office 最後輸入，重算可安全解析的相對連結，防止目的地草稿衝突。
- 全文搜尋涵蓋 Markdown／TXT、顯示來源問題與搜尋範圍，支援鍵盤操作；原始碼／TXT 來源行及閱讀預覽來源區塊定位。
- 快速開啟涵蓋文件庫快取，同名文件可依文件庫與路徑辨認，掃描完成會更新候選項目。
- 設定分頁可捲動，亮暗主題與 540 像素高度下的操作按鈕已檢查。

## 驗證證據

| 項目 | 結果 |
| --- | --- |
| 完整測試 | 1,457 passed、75 skipped，43.73 秒 |
| 開啟 WebEngine 的搜尋／renderer 測試 | 49 passed，含真 Chromium 導航 |
| 搬移專項 | 58 passed，含真實 E 槽至 C 槽、不改來源的拒絕案例與回復故障 |
| 不同作者覆核 | 導覽 7 項、window 整合 6 項、復原 25 項通過 |
| Qt 畫面 | 五分頁亮暗 680×540 截圖；復原清單 820×540，來源不變 |
| PyInstaller | 成功產生獨立執行目錄 |
| Inno Setup | 編譯成功，243.656秒；已產生安裝檔與SHA-256記錄 |
| 封裝版第二實例開檔 | exit 0，傳送的完整路徑一致，約 2.90 秒 |

數字來自不同驗證回合，勿相加為不重複測試數量。搜尋／renderer 作者之外的 root 已讀回相關邏輯並實跑驗收；另一代理原定追加覆核因用量限制未完成，不列為已通過項目。

完整測試記錄：[final-full-tests.log](upgrades/evidence-2026-09-08/final-full-tests.log)。
封裝記錄：[package-build.log](upgrades/evidence-2026-09-08/package-build.log)。
安裝程式記錄：[installer-build.log](upgrades/evidence-2026-09-08/installer-build.log)、[installer-artifact.json](upgrades/evidence-2026-09-08/installer-artifact.json)。
第二實例證據：[packaged-smoke.json](upgrades/evidence-2026-09-08/packaged-smoke.json)。
搬移故障證據：[relocation-independent-probe.json](upgrades/evidence-2026-09-08/relocation-independent-probe.json)。
詳細進度：[2026-09-08-progress.md](upgrades/2026-09-08-progress.md)。

## 驗證範圍與操作限制

- 75 項略過不等於通過；本批另開 WebEngine 執行相關測試，未宣稱所有選配整合都已驗證。
- 封裝 smoke 僅驗證第二實例傳送路徑；尚未驗證安裝後完整冷啟動、實體螢幕高 DPI 與既有個人設定的實際升級。
- 含相對資源的跨磁碟搬移，以及 wiki-links、CSS 等不能可靠重算的情況，會保留原檔並拒絕搬移。附件留在原位置，不會複製整個附件資料夾。
- OS 阻止回復時會指出保留資料與目的地；未實作程序崩潰後自動回放交易或交易 manifest。
- 大型 Markdown 首次渲染重構、首頁新增入口、更新下載流程不在本批交付。

## 安裝後人工驗收步驟

1. 儲存並關閉舊版，再執行 1.31.0 安裝程式；開啟「關於」確認版本。
2. 加入測試文件庫，放入不同資料夾的同名 Markdown／TXT；按 Ctrl+P 確認路徑可辨認，搜尋重複關鍵字並開啟第二筆結果。
3. 在測試 Markdown 輸入未儲存內容後移動文件，確認內容仍在、附件正常、儲存後重開一致。
4. 使用既有待復原草稿測試「稍後」與檔案選單重新開啟；先另存副本確認內容，再決定是否繼續編輯。
5. 在常用 DPI 與螢幕尺寸切換亮暗設定，捲至每頁底部，確認「確定／取消」可操作。
