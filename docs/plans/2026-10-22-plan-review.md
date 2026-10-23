# 2026-10-22 升級計畫驗證紀錄

## 本次實際執行

- `git rev-parse --short HEAD` → `47f6b47`。
- Python／PySide6 版本查詢 → `3.13.5`／`6.11.0`。
- 六組既有測試 → **127 passed in 390.14s，exit 0**。命令與關鍵輸出見 [baseline.txt](evidence-2026-10-22/baseline.txt)。
- 測試包含 view mode、editor status、editor input、smart writing、atomic I/O、external conflicts；沒有涵蓋完整應用程式、真實繁中 IME 或 WebEngine。
- 本機出現 I/O 停頓，測試牆鐘時間不作為產品速度基準。

## 獨立文件驗收

Fresh agent `plan_acceptance` 初步 read-back：主文完整；已有功能、歷史數據及提議 KPI 區分正確；37–62 人日加總正確；25 個相對連結／21 個不重複目標，唯一初查缺檔為本紀錄（現已補齊）；benchmark 參數均存在。抽查確認 PDF 分批尺寸與後續 relayout、全文統計／比例同步、Properties 同步讀檔與歷史效能數值。審查提出 FTS5「三字元以下」應為「少於三字元（1–2 字元）」；已依官方文件修正，最終 fresh agent **read-back PASS**，無剩餘必要修正。

## 介面概念驗證

由另一 agent 產出介面概念，獨立於產品程式碼。最終 HTML 30,308 bytes，兩個介面方向；抽出 script 執行 `node --check`：exit 0，fragment／無網路 API 檢查通過。較早版本 Qt WebEngine 核心互動 8 項通過，1024／736／320 px 無水平溢出；其後複驗遭 Qt offscreen 載入停滯，撤除額外 resize observer，最終調整後只完成語法複驗。最終視覺高度、宿主 carousel／Tweak、實體 Windows 桌面尚未操作驗證。Office 是概念示意，不連接產品後端或保存實際文件。

## 未驗證與後續

未跑完整回歸、已安裝 EXE benchmark、實體 DPI／雙螢幕／讀屏／IME 或程序樹記憶體；不得將此次規劃宣稱為功能實作完成或效能目標達成。重跑命令與實體測試步驟已列於完整計畫第 9 節。

## 最終獨立驗收證據

`Get-Content -Encoding utf8`：計畫與證據 read-back exit 0。

`py -3 -X utf8 -` 文件檢查：`required_sections_present=True`、`total_person_days=37 62`、`local_links=25 unique=21`、`missing_links=[]`。

`rg -n` 程式抽查：`VERSION=1.35.0`；既有 PDF 分批尺寸、閱讀樣式及智慧寫作與計畫一致；兩個 benchmark 的參數存在。

`git diff --check` exit 0（現有 tracked 檔無差異；新增文件另做 read-back）；`git status --short` 僅列 `?? PROGRESS.md` 與 `?? docs/plans/`。
