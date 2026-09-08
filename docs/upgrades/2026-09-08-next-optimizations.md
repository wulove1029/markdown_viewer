# 後續優化實作進度總表（2026-09-08）

規格：`docs/CLAUDE-CODE-NEXT-OPTIMIZATIONS.md`。起點 HEAD `5c1b3d1`（v1.31.0），工作樹乾淨。
環境：Windows 11、Python 3.14.4、PySide6 6.11.0。

本輪授權：實作 + 本機驗證。不推 tag、不公開發布。

## 批次與狀態

| 批次 | 內容 | 狀態 | 紀錄檔 |
| --- | --- | --- | --- |
| T | 全套測試基線（改動前） | 完成：1457 passed / 75 skipped / 0 failed，exit 0，52.7 s | `2026-09-08-next-test-baseline.md` |
| 0 | 大型 Markdown 基線量測工具與基線數據 | 完成並 commit（review PASS，已修預設輸出路徑） | `2026-09-08-next-baseline.md` |
| 1 | 首頁新增入口 | 完成並合併 `efeae06`（review PASS，補測試後全套 1526 passed） | `2026-09-08-next-home-new.md` |
| 2 | 更新下載流程 | 完成並 commit `26d17e0`（review 兩輪 PASS，全套 1503 passed） | `2026-09-08-next-update-download.md` |
| 3 | 大型 Markdown 取消排程與首屏改善 | 完成並合併 `8f5fc8b`（review PASS，修 token stdin／指標／搜尋提示後） | `2026-09-08-next-large-markdown.md` |

每批：實作 agent → fresh review agent → 獨立 commit。狀態欄由主對話更新。

## 事件紀錄

- 2026-09-08：讀取規格、核對工作樹、派出 T/0/1/2。
- 2026-09-08：T 完成（1457 passed／75 skipped，exit 0）。
- 2026-09-08：Claude Code 程序中斷，三個 agent 未交付。核對：批次 2 改動在主樹（未 commit，僅缺最終回歸數字）；批次 1 改動在 worktree（未 commit，缺全套回歸）；批次 0 工具與測試已寫，量測 JSON 與紀錄檔未產出。已續派三者收尾。
- 2026-09-08：批次 1 worktree 全套測試卡在 `test_tab_switch_from_split_preserves_dirty_buffer_and_restores_it`（window.py `_prepare_recovery_state` 的 `RecoveryDialog.exec()` modal）。批次 2 先前也遇到同一測試卡死。基線未卡；兩批都新增了 test_window_integration.py 測試，優先懷疑測試隔離污染（recovery store／視窗未清）。已通知兩個 agent。
- 2026-09-08：批次 2 交付：四檔回歸 3 次 exit 0／267 passed；真 Release 下載 hash 相符未安裝；取消最差 1.2 ms。根因：`RecoveryStore()` 指向真實 %APPDATA%，測試未隔離，已加 autouse fixture；實作者把 172 個殘留快照搬到 `recovery-leaked-backup-20260908`（待 review 確認全為測試路徑）。已派 opus fresh review。
- 2026-09-08：批次 1 commit `78fcfd6`（worktree 分支），全套 1472 passed；已派 fresh review。批次 2 review：核心 PASS，退回 4 項（舊測試 test_toolbar_utilities 未同步、關窗有界收尾與 5 秒不符、重試在 GUI wait(2000)、watcher 未接收）；172 個殘留快照抽查全為測試路徑，無使用者資料。
- 2026-09-08：批次 2 四項修正完成（關窗上限實為 20 秒 = max(CHECK 15s, DOWNLOAD 20s)，DNS 不涵蓋，已誠實註記），全套 1503 passed，複驗中。批次 1 review 可合併，退回補兩類測試（renderer 攔截層、對話框鍵盤與 540 px）。
- 2026-09-08：批次 2 複驗四項 PASS，commit `26d17e0`；計畫文件與基線 commit `1087b22`。
| 4 | PDF 內嵌 Adobe 註解唯讀顯示（使用者 2026-09-08 新增需求） | 進行中（分支 feature/pdf-adobe-annotations） | `2026-09-08-pdf-embedded-annotations.md` |

- 2026-09-08：批次 0 交付：5 MB 長文冷轉換 p50 2.35 s、1 MB 292 ms、100 KB 32 ms；5 MB 解析中開 100 KB 需等 3.2 s（parser RLock 全程持有，取消無法中止 render）。已派 review；批次 3 以 opus 在分支 feature/large-markdown-render 開工（Agent 工具的 isolation worktree 因磁碟機代號大小寫問題失敗，改手動 git worktree）。
- 2026-09-08：使用者新增需求：讀取 PDF 內嵌 Adobe 註解。Explore 確認 PdfView 用 QPdfDocument 自繪、PyMuPDF 僅做大綱、無現成註解讀取；已派 sonnet 在分支 feature/pdf-adobe-annotations 實作唯讀面板。
- 2026-09-08：批次 1 合併 `efeae06`，主樹全套 1526 passed／75 skipped。批次 0 review PASS，修預設輸出路徑後 commit。
- 2026-09-08：批次 1 worktree 目錄 .claude/worktrees/agent-aa205a789c883b4c9 被其他程序占用無法刪除（git 登錄已 prune、分支已刪），稍後手動刪目錄即可。
- 2026-09-08：批次 4 PDF 註解 commit `e11abc4`（全套 1517 passed）；實作者發現既有 race（背景 PyMuPDF 執行緒偶爾抑制密碼提示），已派 review 判斷緩解是否可靠。
- 2026-09-08：批次 3 交付 `959f1c0`：≥256 KB 走可 kill 的 loopback-socket worker process，≥2 MB 先渲染 512 KB 區塊邊界前綴。5 MB 首屏 2346→228 ms（快速模式，覆蓋 10.5%；完整預覽冷開 +6%）、5 MB 解析中開 100 KB 3215→36 ms、100 KB +0.7%、全套 1542 passed。未打包實測。已派 opus review（重點：pickle over socket 安全、程序回收、frozen 入口、部分載入資料安全）。注意該分支複製了基線工具舊版，合併時 tools/benchmark_markdown_first_load.py 會衝突，需保留主樹的預設輸出路徑與 p95 註記修正。
- 2026-09-08：批次 4 review 可合併，修正 QThreadPool 改全域池與色塊顯示（`a9ac66c`），合併 `520fd01`。待人工：用真實 Acrobat 註解 PDF 實測。
- 2026-09-08：批次 3 review 可合併但退回 4 項：token 走 argv 改 stdin、壞指標改在返回瞬間讀、部分載入搜尋要提示、註解與文件修正；並先 merge main 解決基線工具衝突。其餘已知限制（縮排 code block 邊界、256 MB frame 上限、封裝版未驗、5 MB 完整預覽冷開 +6%）記入紀錄。
- 2026-09-08：批次 3 修正 `5bfa53f`（token 改 stdin、阻塞指標改返回瞬間取樣：新架構 blocked=0.0／31.6 ms，對照組 in-process blocked=1.0／3161 ms；部分載入搜尋提示；文件），合併 `8f5fc8b`。主樹全套 1592 passed／75 skipped，WebEngine 組 10 passed，無殘留 worker 程序與 recovery 殘留。

## 最終狀態（2026-09-08）

全部批次已合併到 main，本輪未推 tag、未發布。

尚未驗證（需人工或封裝）：
- PyInstaller 封裝版：render worker 走主 exe `--render-worker` 入口、frozen 下 stdin 取 token、視窗不彈黑窗；5 MB 目視首屏。
- 實體 GUI：更新下載非模態視窗的隱藏／叫回、Esc 與 X 只隱藏；新增筆記對話框在高 DPI 亮暗主題；PDF 註解面板用真實 Acrobat 產生的 PDF。
- 真正執行安裝程式（含 UAC）。
- 已知限制：5 MB 完整預覽冷開較基線慢約 6%（子程序啟動）；256 KB 以下小檔仍共用 in-process parser lock；縮排式 code block 未納入前綴切割邊界；`partial_search_missed` 訊號尚未接到 window.py。
- 2026-09-08：使用者以 Desktop\dm00293821.pdf（真實 Acrobat 註解）實測：PDFium Annotations 旗標把 IRT 回覆的 Text 圖示畫成隨縮放放大的紫色方塊。修正 `f64891f`：改自繪 overlay（app/pdf_annotation_overlay.py）、回覆不畫、討論串面板、分頁計數與狀態列提示；全套 1614 passed。截圖確認 200% 只剩橘色高亮。派 review。
- 2026-09-08：`f64891f` review 通過（全套 1614 passed）；面板截圖確認父／回覆縮排。非阻塞備註：overlay 未按頁分桶、FreeText／Ink 自繪品質未用真實檔驗證。
- 2026-09-08：使用者指出頁面看不到備註文字（Acrobat 會自動顯示 Popup 卡片）。`c41b8b8`：有內容／回覆的標記加 16 px 泡泡標記、點擊彈出卡片、Popup /Open=true 自動顯示於 popup_rect（需乘 transformation_matrix）並畫引線、側欄雙向同步；全套 1627 passed。截圖確認標記與卡片；卡片短內容仍出捲軸，交 review 判定。
- 2026-09-08：review 不通過 3 項（卡片高度算錯出捲軸、手動卡片縮放不跟隨、側欄回寫無測試）。`d934b0e` 修正：QLabel 加入後立即 show 再以固定寬度算高、縮放後共用 relayout 重定位、補測試；全套 1634 passed。截圖確認卡片 210 px 無捲軸、引線可見。複驗中。
- 2026-09-08：`d934b0e` 複驗三項 PASS，全套 1634 passed。PDF 註解功能待使用者實機確認（卡片自動彈出、引線、中文顯示、側欄雙向同步）。
- 2026-09-08：使用者實機確認卡片顯示；新回饋：要可拖動、可回覆／編輯（寫回 PDF）、外觀改 Acrobat 風格。範圍擴大為可寫（增量儲存、作者設定、唯讀／加密停用、失敗明確提示、寫入前暫存備份）。已派實作。
- 2026-09-08：`3f9122f`：Acrobat 風格可拖動卡片、新增回覆／編輯／刪除（增量儲存寫回 PDF）、作者設定、唯讀／加密停用、暫存備份；實測 QPdfDocument 載入中 PyMuPDF 增量儲存可成功，故不重載。全套 1652 passed。亮暗截圖確認。派 opus review（資料安全）。
- 2026-09-08：`3f9122f` opus review 通過（無毀檔路徑）；4 小項修正 `4344911`（刪除前確認連帶回覆、備份唯一檔名、例外包裝、鎖檔文字），全套 1659 passed。PDF 註解功能本輪完成；未做：卡片「…」選單、螢光本身內容就地編輯。
- 2026-09-08：使用者回饋卡片拖動延遲＋引線殘影。`e32579e`：引線髒區舊∪新外擴 3 px、移除 QGraphicsDropShadowEffect 改自繪陰影、拖動只 move；60 次拖動平均 3.4 ms／最大 5.2 ms；全套 1664 passed。截圖無殘影。派 review。
- 2026-09-08：`e32579e` review 通過（重跑拖動 3.0–3.4 ms 平均）。WA_TranslucentBackground 在實體視窗判低風險，待使用者肉眼複驗。
