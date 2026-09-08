# 獨立驗收紀錄（2026-09-08）

範圍：設定視窗尺寸證據與匯出流程描述；未修改產品程式。

## 設定視窗

- 已重新執行 `py -3 -X utf8 docs/audits/evidence-2026-09-08/capture_settings.py`，exit code 0。
- 重現 requested size 800×540、actual size 800×757、minimumSizeHint 405×757、按鈕列底部 y=745、QScrollArea 數量 0。
- 已開啟並人工檢查 `settings-behavior.png`：中文可正常顯示，完整 800×757 圖中可見「確定」與「取消」。
- probe 嘗試載入 Microsoft JhengHei 與 Segoe UI 字型；本次截圖無中文字形缺失。
- 合理結論是小邏輯工作區有超高風險：1366×768@100% 只有 768 邏輯像素（尚未扣除桌面工作列與視窗框）；1920×1080@150% 為 720 邏輯像素的計算示例。
- 未執行實體螢幕 150% 縮放驗收，不得將上述計算或 offscreen 截圖當成實體裁切已重現。

## 匯出流程

靜態程式碼覆核 `app/export_actions.py`：

- PDF：成功後有 QMessageBox，提供「開啟 PDF」按鈕；只在使用者點擊後透過 QDesktopServices 開啟，不是自動開啟（第 494–503 行）。
- Word：成功後只有 5 秒狀態列訊息（第 208–210 行）。
- PPT：成功後只有 5 秒狀態列訊息（第 152–154 行）。
- HTML：只允許 Office／WYSIWYG 編輯模式（第 213–228 行），成功後也是 5 秒狀態列訊息（第 245 行）。
- 上述各格式未在本子任務實際完成匯出；此項標為程式碼驗證。

## 驗收狀態

已通過本範圍的證據覆核；總報告另待 read-back。

## 總報告與分報告 read-back

已讀取：

- `docs/audits/2026-09-08-consumer-experience-audit.md`
- `docs/audits/2026-09-08-consumer-flows.md`
- `docs/audits/2026-09-08-reliability.md`

驗證結果：

- 版本 `app/version.py` 為 1.30.2，`git rev-parse --short HEAD` 為 `96da981`，與報告一致。
- 總報告初始 8 個 Markdown 證據連結均存在；後續新增的搜尋與可靠性探針／結果、獨立驗收紀錄也存在。
- 已對照關鍵原始碼：搜尋範圍、Ctrl+P 候選、同名列 tooltip、新增筆記流程、首頁入口、模式隱藏按鈕、復原啟動清單、匯出、更新、外部衝突及安裝設定。
- 已檢視首頁與深色閱讀截圖，分別為 1360 與 1000 px 寬；主報告所述入口吻合畫面。
- 獨立重新執行 `consumer-flows-probe.py`，exit code 0：只搜到 `document.md` 與 `nested/nested.md`；快速開啟候選只有同層三種文字檔；Return callback=0、滑鼠 callback=1；兩筆同名列皆為 `Meeting.md`。
- 獨立重新執行 `reliability-reproduce.py`，exit code 0：附件移動前有效、後無效且原資源仍在；sidecar 衝突時主檔移動而來源註記留存；有效草稿 1、啟動恢復分頁 0。
- 同一 reliability probe 呼叫手動開啟所用的復原 preparation，將對話框選擇替身設為 Restore，成功取回 `unsaved draft`，編輯文件標記 modified，原檔仍為 `disk version`。此為函式層重現，並非真實殺程序再點擊復原的完整 UI 流程。
- 獨立重新執行 `reliability-update-ui-probe.py`，exit code 0：ApplicationModal、進度範圍 0–0、可見按鈕空清單；假 worker 未連網。
- 舊效能 JSON 的 scope 明示不含 first paint／packaged startup；1,000,000 與 5,000,000 規模冷轉換 p50 各為 2261.6289 ms、20086.5795 ms，與報告約 2.26／20.09 秒相符。報告已明說不是本次量測。
- 可靠性 pytest 原始日誌為 69 passed in 0.66s。192 passed in 14.50s 目前僅從主報告與進度 read-back；已請主代理補存既有原始輸出／完整命令，無要求重跑。
- 未發現關鍵結論過度宣稱；報告已區分程式碼、offscreen、隔離探針、舊量測與設計建議，並列明真人／實體 DPI／安裝版／完整當機流程未驗。

已請主代理修正文案與定位：

1. 全域搜尋只連接滑鼠的來源應為 `app/global_search.py:204`，不是第 216 行（該行是輸入框的 returnPressed）。操作流程分報告也宜以第 204 行直接定位。
2. 可精確化 `app/window.py:5177` → `:5178`、`:3694` → `:3695`。
3. 統一少量簡體字為繁體字，包含「图片／时／与／无需／属于／主题」。
4. 可靠性分報告建議補記已重現的手動復原 preparation，並保留函式層限定。

狀態：已完成最終 read-back，總報告與兩份分報告驗收通過。

## 最終修訂確認

- 已讀回總報告的 `app/global_search.py:204`、`app/window.py:5178` 與 `:3695`；流程分報告也已區分清單滑鼠點擊與輸入框 Enter。
- 已確認總報告與分報告的繁體文案修正，進度檔使用「主題」。
- 可靠性 R3 已補齊舊分頁、`last_file` 與啟動參數的情境限制，並明示手動復原 preparation 使用 stub 選擇 Restore、未驗完整對話框流程。
- 已讀回 `main-validation.txt`：保存完整主線 pytest／畫面探針命令與工具結果摘要，明示不是逐字終端紀錄。命令內共 10 個 tests／tools 路徑均存在；無以此聲稱獨立重跑主線 192 項測試。
- 最終總報告共有 13 個證據連結，全部存在；無未解決的關鍵事實或證據範圍問題。
- `git status --short` 只有新增 `docs/audits/`，產品程式未修改。
