# 工作階段還原修正：獨立驗收

日期：2026-09-16。

驗收者：fresh agent `review_session_restore`。依 `03-judgment.md` R2，獨立檢查未提交修改並另行實跑；未修改產品程式碼。後續收到測試隔離異常回報，另修正測試 fixture 並補驗；第一輪測試的暫存設定設計不足以證明沒有影響原生設定，相關更正見下方。

## 結論

獨立驗收通過，未發現阻擋本次修正的問題。此次變更符合「重新啟動保留上次分頁，從文件啟動也保留工作階段」的需求。

本報告不代替主代理的全回歸；該證據由主交付紀錄補齊。已讀回主代理完成的五程序 smoke 報告並檢查隔離工具，結果見下方。本批未進行升版、提交、推送或發布。

## 檢查範圍與結果

| 驗收項目 | 獨立核對結果 |
| --- | --- |
| 啟動還原與文件參數 | 空白啟動還原分頁及目前文件；文件參數與舊清單合併；Windows 大小寫變體不重複建分頁；只載入目標文件，其餘維持延後載入。 |
| 自動保存與正常關閉 | 開啟、切換、排序、關閉均由分頁事件觸發 checkpoint；close event 不必等待 250 ms 即寫入；取消關閉後仍可繼續保存。 |
| 還原期間的事件重入 | 自行攔截每次 `_add_tab`，各執行 300 ms Qt 事件處理；每個中間時點仍是完整原始清單，未出現半份工作階段覆寫。 |
| 分離視窗 | 實際執行 `_detach_tab`，再操作／關閉分離視窗；主視窗的清單保持正確，沒有被子視窗覆寫。 |
| 空白、缺失、重複分頁 | 明確空清單不復活 `last_file`；缺失文件過濾後仍依文件身分選擇原本活躍分頁；重複路徑過濾正確。 |
| PDF 閱讀頁碼 | 真實 `PdfView`／`QPdfDocument` 的多文件切換與還原測試通過，包含第 1 頁；自行反覆切換不同頁數文件並取消加密 PDF 密碼提示，其他文件的頁碼紀錄沒有被載入訊號污染。 |
| 檔案安全 | PDF 均為測試生成。讀碼確認本次工作階段保存只寫 QSettings，未加入來源文件寫入。第一輪 fixture 的隔離生命週期有缺口；第二輪修正及獨立隔離補驗詳見下方。 |

## 實跑證據

第一輪獨立合併執行，exit code 0；這是功能證據，不是完整設定隔離的證據：

```text
$env:QT_QPA_PLATFORM='offscreen'
py -3 -X utf8 -m pytest tests/test_session_restore.py tests/test_startup_recovery.py tests/test_editor_workspace_integration.py tmp/test_session_restore_review_probe.py -q -p no:cacheprovider
42 passed in 11.31s
```

另行 probe 單獨執行，exit code 0：

```text
py -3 -X utf8 -m pytest tmp/test_session_restore_review_probe.py -q -p no:cacheprovider
5 passed in 3.92s
```

`git diff --check`：exit code 0。

probe 位於忽略提交的 `tmp/test_session_restore_review_probe.py`。窗口協調測試使用真實 `MainWindow` 與 Qt 分頁／事件迴圈；Markdown renderer 與側欄採既有測試替身；PDF probe 改用真實 `PdfView`。這些測試不宣稱替代實際安裝版的人工驗收。

## 第二輪：測試生命週期與隔離更正

主代理的跨程序 smoke 發現 `QSettings.setDefaultFormat(IniFormat)` 不會把帶 organization/application 參數的建構式改成 INI；此點使原 smoke 的隔離失效。另由程式碼與安全重現確認：既有窗口 fixture 的 teardown 只有 `close()`，但 close 可以被確認對話框取消或等待非同步 snapshot。此時窗口與 session timer 仍活著，fixture 還原 QSettings monkeypatch 後，timer 能呼叫已恢復的設定建構式。

已修正 `tests/test_window_integration.py` 與 `tests/test_editor_workspace_integration.py` 的 fixture：共同使用 `_dispose_window`，先走既有 close，再停止子 QTimer、排程並立即處理 `DeferredDelete`，最後斷言 Qt 物件已釋放。這只調整測試清理；產品在使用者取消關閉後繼續自動保存的行為保留。

加入正式 regression test：close 被取消時，fixture 仍會釋放窗口，原本已啟動的 session timer 不會再回呼。另行 probe 以兩個暫存 INI factory 模擬 monkeypatch 還原：舊式清理可重現逃逸呼叫，修正後沒有；過程完全不建構 NativeFormat 設定。

補驗 runner `tmp/run_session_review_isolated.py` 在匯入應用程式前，把 QSettings 全域建構式改成強制顯式 INI 的子類，並核對每個 instance 的 `format()`／`fileName()`。每個測試 teardown 完成後，再處理 350 ms 事件並檢查沒有預設設定建構呼叫逃逸。

首輪隔離補驗：`19 passed in 18.31s`，exit code 0；`NATIVE_SETTINGS_CONSTRUCTORS=0`、`redirected_default_calls=0`。

完整的 fixture 與功能合併補驗，exit code 0：

```text
py -3 -X utf8 tmp/run_session_review_isolated.py -q -p no:cacheprovider tests/test_session_restore.py tests/test_window_integration.py tests/test_editor_workspace_integration.py tmp/test_session_restore_review_probe.py
182 passed in 95.19s (0:01:35)
NATIVE_SETTINGS_CONSTRUCTORS=0; redirected_default_calls=2
```

上述 2 次預設設定建構也被最外層子類導向暫存 INI；每個測試結束後的 350 ms audit 全部通過，沒有設定建構呼叫逃逸至 teardown 之後。新的 probe 包含舊清理方式可重現、強制釋放後不再出現的對照。

另讀回 `docs/upgrades/evidence-2026-09-16-session/session-restart-smoke.json`，五個獨立程序的 save／restore／file-launch／abrupt／after-abrupt 均通過，4 份 PDF 雜湊保持不變；該輪 smoke 開始與结束時原生工作階段設定也保持相同。這不表示更早的失敗 smoke 沒有影響原生設定。

獨立檢查 `tools/verify_session_restart.py`：子程序在匯入任何 app 模組前取代 `QtCore.QSettings`，所有應用程式設定均以明確檔名與 `IniFormat` 建構、關閉 fallback，並核對 `fileName()`。新工具已排除最初 `setDefaultFormat` 不影響 org/app 建構式的隔離錯誤。

## 已知界線

- 自動 checkpoint 合併間隔為 250 ms；正常關閉有立即保存，強制終止或斷電仍可能遺失最後不足該間隔的操作。
- 此處保存分頁與已有的 PDF 頁碼紀錄；未把未儲存編輯直接寫回來源文件，也未改變既有草稿復原機制。
- 分離視窗不另外復原自身分頁，沿用主視窗工作階段的既有範圍。
