# 2026-10-22 獨立核心驗收

驗收者：fresh reviewer `/root/independent_core_review`。工作目錄 `D:\markdown_viewer`，Windows、Python 3.13、PySide6 offscreen。先讀全域 `03-judgment.md` 與完整升級計畫，再從實作差異獨立檢查；未沿用實作者的通過結論。專案根目錄未找到 `CLAUDE.md` 或 `AGENTS.md`，依使用者提供的全域規則執行。

本 reviewer 僅新增 [回歸測試](../../tests/test_independent_core_review.py) 與本報告，未修改產品檔或 `PROGRESS.md`。產品修正由主代理完成，再由本 reviewer 實跑驗證。

結論：本次範圍內發現 5 項可重現缺陷，修正後新增的 12 個驗收案例全部通過；相關 42 個既有案例及 22 個真實 Chromium 案例另批通過。目前沒有此範圍內仍待修正的已確認缺陷。這不代表整個升級計畫的實體桌面／安裝版發布驗收完成。

## 已發現與重驗的缺陷

| 嚴重度 | 問題與可重現結果 | 產品定位 | 修正後證據 |
|---|---|---|---|
| P1 | 來源模式開啟寫作大綱後切到 Office，既存大綱仍可改隱藏來源文件。原測試得到來源 `## First`，Office 可見快照仍為 `# First`，兩者分歧；本次沒有宣稱已實際造成磁碟資料遺失。 | [window.py](../../app/window.py)、[workspace_controls.py](../../app/workspace_controls.py) `sync`、[writing_outline.py](../../app/writing_outline.py) `_resolve` | `test_writing_outline_cannot_edit_hidden_office_shadow`：切後端關閉大綱，失效 controller 的延遲操作不改隱藏文件。 |
| P2 | 關閉草稿分頁時選擇首次儲存，`save_draft` 遷移路徑後，關閉流程仍以舊 key 釋放，導致已關閉的正式檔 `TabState`／`QTextDocument` 殘留。正式檔文字原本已正確寫入。 | [drafts.py](../../app/drafts.py) `save_draft`、[window.py](../../app/window.py) `_on_tab_close` | `test_first_save_during_tab_close_releases_relocated_state` 與背景分頁案例：以遷移後 key 清理，正式檔內容正確，狀態不殘留。 |
| P2 | 來源定位等待渲染期間，直接點選另一分頁未清除 pending target；之後返回可能執行過期跳轉。 | [window.py](../../app/window.py) `_activate_tab`、`_open_document_target` | `test_pending_source_target_clears_on_user_tab_switch`：使用分頁列切換後 pending 為 `None`。 |
| P2 | 來源定位需要離開編輯時，使用者在未存內容確認選取消，pending target 仍然保留。 | [window.py](../../app/window.py) `_exit_edit_mode_after_snapshot` | `test_cancel_source_target_mode_exit_clears_pending`：保留 dirty 內容及編輯模式，同時取消 pending 定位。 |
| P2 | 啟動時回放完成的搬移交易會遷移分頁路徑，卻沒有遷移 `pinned_tabs`，固定分頁變成未固定。 | [session_state.py](../../app/session_state.py) `_recover_relocations` | `test_startup_relocation_preserves_pin_and_both_recovery_copies`：新路徑固定狀態保留；來源與目的各自已有的 recovery snapshot 均未覆寫。 |

原第一批結果為 `5 failed, 3 passed`，其中閱讀下一章的失敗來自當時正同步更新的「隱藏 dock 不刷新」行為，並非最終確認缺陷；將測試改為實際顯示 dock 後通過，不列入以上缺陷數。五項確認缺陷是四項第一批問題，加上後續確認的啟動搬移固定狀態遺漏。

## 驗收涵蓋內容

- 未命名草稿：首次儲存取消、關閉時取消、正式位置已存在時拒絕覆寫、前景與背景分頁首次儲存後關閉。
- 草稿 checkpoint 重開：最新繁中／Emoji 內容可復原為 dirty 文件，內部原始草稿檔不被覆寫。此案例是在同一程序建立新視窗並還原持久化狀態，不冒充程序強制終止驗證。
- Office 草稿首次儲存：以可控制的延遲 live snapshot／ack 模擬，ack 前正式檔不存在，ack 後磁碟內容與最新可見 Unicode 文字完全一致，Office 與主視窗路徑同步。
- 固定分頁：關閉其他分頁不會關閉固定分頁，session 還原保留固定狀態，搬移回放亦保留。
- 閱讀工具：顯示工具 dock 後，從第二章按下一章正確跳至第三章；相同大綱刷新不清除當前章節。
- 寫作大綱：來源映射、圍欄／front matter／引用排除、巢狀折疊、Unicode 標題單步 Undo、文件切換及 modeless UI smoke，另加 Office 隱藏來源防護。
- 真實 Renderer／Office：來源行到渲染區塊跳轉、預覽回推來源位置、generation 防過期、Office 格式命令、Undo／Redo、表格、連結與程式碼、合成 IME guard、可見後端路由。
- 啟動搬移：本次聚焦 session 路徑／固定狀態整合與兩份既有 recovery snapshot 不互相覆寫；交易 journal 的程序中斷耐久性由其他 reviewer 驗收。

## 實際執行命令與輸出

```powershell
$env:PY_PYTHON3='3.13'
py -3 -X utf8 -m pytest tests/test_independent_core_review.py -q -p no:cacheprovider
```

最終輸出：`12 passed in 2.65s`，exit code `0`。

```powershell
py -3 -X utf8 -m ruff check tests/test_independent_core_review.py
```

最終輸出：`All checks passed!`，exit code `0`。Fixture 重用的 F811 僅在相應參數行標註，沒有新增全檔忽略規則。

```powershell
$env:PY_PYTHON3='3.13'
py -3 -X utf8 -m pytest tests/test_drafts_reading_tools.py tests/test_writing_outline.py tests/test_session_restore.py tests/test_session_state_edit_backends.py -q -p no:cacheprovider
```

輸出：`42 passed in 8.28s`，exit code `0`。

```powershell
$env:PY_PYTHON3='3.13'
$env:RUN_WEBENGINE_TESTS='1'
$env:QT_QPA_PLATFORM='offscreen'
py -3 -X utf8 -m pytest tests/test_renderer_source_navigation.py tests/test_office_commands_webengine.py -q -p no:cacheprovider
```

輸出：`22 passed in 20.57s`，exit code `0`。Chromium stderr 出現一次 `Unable to initialize SkSurface`，這批 DOM／內容斷言仍全部通過；不將其解讀為實體 GPU 畫面驗收通過。

## 尚未在此驗收範圍驗證

真實 Microsoft 注音／倉頡候選視窗、滑鼠及鍵盤手感、100–200% DPI、雙螢幕、已安裝 EXE、強制終止後草稿重啟，以及全部性能門檻均未由本 reviewer 驗證。可依完整計畫的實體補驗步驟，用兩後端輸入含 Emoji 的繁中段落，切頁後 Undo／Redo、首次儲存與取消、關閉重開比對磁碟，再於不同 DPI／螢幕重做。這些項目不應由本報告的 offscreen／合成 IME 結果替代。
