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
| 3 | 大型 Markdown 取消排程與首屏改善 | 進行中（分支 feature/large-markdown-render） | `2026-09-08-next-large-markdown.md` |

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
