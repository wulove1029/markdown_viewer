# 2026-09-08 更新下載流程重做（規格：CLAUDE-CODE-NEXT-OPTIMIZATIONS.md 第 5 節）

本檔隨做隨寫，假設 session 隨時會斷。**尚未 commit**（主對話待合併另一分支後統一處理）。

## 改動檔案清單

| 檔案 | 內容 |
| --- | --- |
| `app/updater.py` | `download_installer()` 改為固定 chunk 串流 + 邊算 SHA-256；新增 `UpdateCancelled`、`UpdateDigestUnavailable`、`has_verifiable_digest()`、`verify_installer()`、`cleanup_job_dir()`、`_watch_for_cancel()` |
| `app/update_flow.py` | 下載工作（request id / cancel event / 專用暫存目錄）、非模態可隱藏進度、取消／重試、驗證中狀態、稍後保留、立即安裝前的存檔與 Office snapshot 保護、關閉時有界收尾 |
| `app/toolbar_utilities.py` | 新增 `verifying`／`ready`／`cancelled`／`launching` 狀態與對應 tooltip／badge；下載中按鈕保持可按（用來叫回隱藏的進度視窗） |
| `app/window.py` | 只改更新接線：新屬性初始化、`_set_update_state()` 啟用條件、`_on_update_button_clicked()` 改為委派 `update_flow.on_update_button_clicked()`、移除已不用的 `UPDATE_DOWNLOADING` import |
| `tests/test_updater.py` | 改用可控假傳輸；新增串流／未知長度／慢回應／逾時／斷線／長度不符／空檔／錯誤 hash／缺失 digest／磁碟滿／取消／重試／重新驗證案例 |
| `tests/test_update_flow.py` | 新增下載中可編輯（非模態）、重複按下載、過期進度、取消、重試、稍後、未儲存取消安裝、Office snapshot、啟動失敗、關窗取消等案例；autouse fixture 真正銷毀假視窗 |
| `tests/test_window_integration.py` | 只改兩處：更新按鈕在下載中改為可按（叫回進度視窗）的測試；新增 autouse fixture 隔離 RecoveryStore（見下方「意外發現」） |

## 設計重點

- **狀態機**：`idle / checking / available / downloading / verifying / ready / cancelled / error / launching`，常數集中在 `app/toolbar_utilities.py`，由 `update_flow` 這個 controller 維護，UI 只讀狀態。
- **身分防污染**：`window._update_request_id` 單調遞增；取消／重試會先 `_next_request_id()`，因此舊 worker 之後發出的 progress／verifying／結果一律被 `_is_current_request()` 丟棄，並清掉它自己的暫存目錄。
- **暫存目錄**：每次工作 `tempfile.mkdtemp(prefix="mdviewer-update-")`；`.part` 寫在裡面，驗證通過才 `replace()` 成正式檔名。清理只刪 prefix 相符的自有目錄（`_remove_job_dir()`），不碰系統 temp 或別版本的下載。
- **取消**：`threading.Event` + 守望執行緒 `_watch_for_cancel()`。取消時直接 `response.close()`，讓卡在 `read()` 的 socket 立刻拋出，不必等 `DOWNLOAD_TIMEOUT`（20 秒，同時是 connect 與每次 read 的上限）。守望輪詢間隔 50 ms，即為最差取消延遲的主要成本。
- **未做**：HTTP Range／斷點續傳（依規格排除）。重試一律新工作、新暫存檔。
- **不跨重開**：已驗證檔只存在 `window._update_ready_installer`，沒有寫進 QSettings。

## 進度

- [x] 讀規格第 2/5/6 節與 `updater.py` / `update_flow.py` / `window.py` / 既有測試
- [x] `updater.py` 串流下載改寫
- [x] `toolbar_utilities.py` 新狀態
- [x] `update_flow.py` controller 重寫
- [x] `window.py` 接線（最小改動，29 行、僅更新流程區段）
- [x] `tests/test_updater.py` 改寫
- [x] `tests/test_update_flow.py` 改寫
- [x] 官方 Release 真下載與 hash 比對
- [x] 取消最差時間量測
- [x] 全套回歸

## 驗收證據

### 測試命令與結果

```powershell
py -3 -X utf8 -m pytest tests/test_updater.py tests/test_update_flow.py `
  tests/test_editor_data_safety.py tests/test_window_integration.py `
  -q -p no:cacheprovider --basetemp tmp/next-update-tests
```

結果見下方「最終回歸」。

### 假傳輸案例（`tests/test_updater.py`，可控 `_FakeResponse`）

| 案例 | 測試 |
| --- | --- |
| 正常分塊 + 進度單調遞增 | `test_download_streams_chunks_and_reports_progress` |
| 串流而非一次讀完 | `test_download_never_holds_whole_file_in_one_read`（10 次資料 read + 1 次 EOF） |
| 未知長度 | `test_download_unknown_length_reports_none_total` |
| 極慢回應 | `test_download_tolerates_slow_response` |
| 連線逾時 | `test_download_connect_timeout_is_reported` |
| 中途斷線 | `test_download_midstream_disconnect_fails_and_cleans_up` |
| 長度不符（截斷） | `test_download_truncated_against_content_length_fails` |
| 空檔 | `test_download_rejects_empty_body` |
| 錯誤 hash | `test_download_rejects_digest_mismatch` |
| 缺失／畸形 digest | `test_download_refuses_missing_or_malformed_digest`（4 個參數化） |
| 磁碟滿 | `test_download_reports_disk_full` |
| 取消（未連線／下載中／完成競爭） | `test_cancel_before_connect_never_opens_a_socket`、`test_cancel_mid_download_unblocks_quickly`、`test_cancel_racing_completion_leaves_no_installer` |
| 重試 | `test_retry_after_cancel_uses_a_fresh_job_dir` |
| 安裝前重新驗證 | `test_verify_installer_round_trip` |
| 可信來源／redirect／檔名 | `test_download_rejects_untrusted_url`、`..._untrusted_redirect_target`、`..._sanitizes_traversal_name`、`..._rejects_non_exe` |

上述所有失敗案例都額外斷言 `tmp_path` 內沒有殘留（暫存目錄與 `.part` 都被清掉），且只清自己 `mdviewer-update-` 前綴的目錄。測試沒有放寬 `_require_trusted_url`：fixture 一律用 `https://github.com/...` 的合法 URL，並另外驗證非可信 host 會被拒。

### UI 案例（`tests/test_update_flow.py`）

| 案例 | 測試 |
| --- | --- |
| 下載中可編輯（非模態、按鈕仍可按） | `test_download_is_non_modal_and_reports_percentage` |
| 有長度顯示百分比 | 同上（斷言 `25%`、且不出現「完成」） |
| 無長度顯示已下載量、不定進度 | `test_download_without_content_length_stays_indeterminate` |
| 驗證中不算完成 | `test_verifying_stage_is_not_reported_as_finished` |
| 重複按下載 | `test_second_download_press_reuses_the_running_job` |
| 過期進度／結果被忽略 | `test_stale_progress_and_result_are_ignored` |
| 取消 + 重試新工作 | `test_cancel_sets_event_closes_progress_and_allows_retry` |
| 進度視窗取消鈕 | `test_progress_cancel_button_stops_the_job` |
| 下載失敗回到 available | `test_download_error_restores_available_badge_and_releases_thread` |
| 取消不跳警告 | `test_cancelled_worker_error_does_not_warn` |
| 缺 digest → 官方 Release 連結 | `test_missing_digest_offers_manual_release_download`、`test_worker_digest_error_also_offers_manual_download` |
| 成功後不自動安裝 | `test_successful_download_becomes_ready_without_installing` |
| 稍後保留、下次點擊即安裝 | `test_later_keeps_file_for_this_session_and_installs_on_next_click` |
| Office 最後一次輸入保護 | `test_install_flushes_office_snapshot_before_saving`（snapshot 先於 confirm） |
| 未儲存取消安裝 | `test_install_aborts_when_user_cancels_saving`（無 `startDetached`、無 `quit`） |
| 檔案遺失／驗證失敗不安裝 | `test_install_refuses_a_missing_or_tampered_file` |
| 啟動失敗仍可用可重試 | `test_launch_failure_keeps_app_usable_and_retryable` |
| 關窗取消 + 關閉中 callback 不安裝 | `test_closing_cancels_download_and_never_installs` |
| worker 進度節流與驗證階段 | `test_download_worker_throttles_progress_and_flags_verifying` |

安裝啟動器一律以 fake 取代（`QProcess.startDetached` 與 `QApplication.quit` 都被 monkeypatch 並記錄呼叫次數／參數），測試機不會真的安裝或退出。

`tests/test_window_integration.py` 只改一個測試
（`test_update_menu_action_is_disabled_while_checking_or_downloading`
→ `test_update_entry_point_is_inert_while_checking_and_shows_progress_downloading`）：
下載中按鈕由「停用」改為「可按 → 叫回隱藏的進度視窗」，這是規格要求的行為變更。

### 取消最差時間（量測）

以假慢傳輸量測 `download_installer()` 從 `cancel.set()` 到 worker 收尾的時間，各 10 次：

| 情境 | p50 | max |
| --- | --- | --- |
| 卡在 read（模擬 60 秒 socket stall） | 0.7 ms | **0.9 ms** |
| 慢速滴流（每 chunk 0.5 秒） | 0.8 ms | **1.2 ms** |

量測後 `leftover job dirs: []`（暫存目錄全數清乾淨）。
理論上界＝守望執行緒的 50 ms 輪詢 + `response.close()`，遠低於規格的 5 秒收尾預算。
UI 端不等 worker：`cancel_update_download()` 立即 bump request id、關進度視窗、改狀態
（`test_cancel_sets_event_closes_progress_and_allows_retry` 斷言 < 0.5 秒）。

**transport 說明**：仍使用 `urllib`。原生 `urlopen(timeout=)` 只保證 connect/read 的
socket 逾時（設為 `DOWNLOAD_TIMEOUT = 20` 秒），無法主動中斷阻塞中的 `read()`，
單靠它最差取消時間會是 20 秒。因此加了 `_watch_for_cancel()` 守望執行緒，在取消時
直接 `response.close()`，讓阻塞的 `read()` 立刻拋出 → 毫秒級收尾，不需要換掉 transport。

### 官方 Release 真下載與 hash 比對（2026-09-08，已實測）

```
latest: 1.31.0  asset: MarkdownViewer_Setup_v1.31.0.exe
digest: sha256:3bdf35a6fdd4c987594772ccd72d20f1b0c31a6c0f044ac95762397aeb407e68
downloaded: 173,362,254 bytes in 14.2 s
progress callbacks: 2647   last: (173362254, 173362254)
verify_installer(): True
computed sha256: 3bdf35a6fdd4c987594772ccd72d20f1b0c31a6c0f044ac95762397aeb407e68  ← 相符
```

下載後即刪除暫存目錄，**沒有啟動安裝程式**。

## 尚未驗證（手動待辦）

1. **實體 GUI 操作**：非模態進度視窗的隱藏／叫回、Esc 與標題列 X 只隱藏不取消、
   下載中實際編輯與切頁、亮暗主題下的 badge 與 tooltip。offscreen 測試不等於實體高 DPI 通過。
2. **真正執行安裝程式**（含 UAC）：`QProcess.startDetached` 之後的行為刻意留作獨立手動驗收。
3. **封裝版（PyInstaller）**：本輪只在原始碼模式驗證。
4. **不同作者的 fresh review**：規格第 6 節要求，尚未進行。

## 已知限制

- `QProgressDialog` 的 Esc／關閉鈕原本會觸發 `cancel()`；以 `_HideOnCloseFilter` 事件過濾器
  改成只隱藏，取消只有「取消下載」按鈕一條路徑。此行為只有實機能完整確認（見上）。
- 已驗證的安裝檔只保留在本次 session 的記憶體屬性中，不寫 QSettings；重開 app 一律重新下載並重新驗證。


## 意外發現：測試會卡死的真正原因（既有問題，非本次改動引入）

四個測試檔一起跑時，`tests/test_window_integration.py::
test_tab_switch_from_split_preserves_dirty_buffer_and_restores_it` 會整個卡死。

用 `timeout` 送 SIGTERM 取得 Python stack，主執行緒停在：

```
app/window.py:3469 in _prepare_recovery_state   <- dialog.exec()
app/window.py:4420 in _load_document
app/window.py:4321 in _activate_tab
app/window.py:3931 in _open_file
app/window.py:5381 in open_path
tests/test_window_integration.py:1985 in test_tab_switch_from_split_preserves_dirty_buffer...
```

**根因**：`MainWindow` 內部 `RecoveryStore()`（`app/window.py:298`）指向真實的
`%APPDATA%/python/markdown-viewer/recovery`，`tests/test_window_integration.py`
沒有隔離它（`_clean_settings` 只隔離 QSettings）。任何被中斷的測試回合都會在那裡
留下快照；因為 `--basetemp` 固定，下一回合的 `tmp_path` 路徑完全相同，於是
`_prepare_recovery_state()` 找到「舊快照 ≠ 磁碟內容」，彈出 `RecoveryDialog.exec()`——
offscreen 平台沒人能關掉它，整個 session 就吊死。實測當時該目錄殘留 **172 個** 快照。

證據：所有用「全新 `--basetemp`」的回合都通過；所有重複用
`tmp/next-update-tests` 的回合都卡死。清空該目錄後第一回合立刻 267 passed，
但它自己又留下 2 個快照，第二回合又卡死。

協調者回報：同樣的卡死也在一個**完全不含本次改動**的 worktree（只改首頁新增流程）
重現，主執行緒同樣停在 `_prepare_recovery_state`，且當時另有高負載的效能量測程序。
因此這是既有的測試隔離缺陷，與機器負載一起決定重現機率，不是本次改動引入。
（本紀錄先前一版曾誤判為本次改動引入，已更正。）

**修正**（`tests/test_window_integration.py`）：新增 autouse fixture
`_isolated_recovery_store`，把 `window_mod.RecoveryStore` 換成指向
`tmp_path/"recovery-store"` 的實例。規格第 6 節本來就要求「測試隔離恢復儲存」。
另已把先前累積的 172 個殘留快照移到
`%APPDATA%/python/markdown-viewer/recovery-leaked-backup-20260908` 保留備查。

順帶在追查過程中做的兩處防禦性調整（保留）：

- `app/update_flow.py`：`defer_close_until_updates_finish()` 不再排
  `QTimer.singleShot(5000, window, ...)` 這種指向「即將被銷毀視窗」的延遲計時器；
  有界性改由 worker 自身保證（下載已取消 → 毫秒級收尾；檢查 worker 由 `urlopen`
  socket timeout 封頂），並在註解寫明。`_resume_deferred_close()` 加
  `_update_close_deferred` 守衛，關窗流程結束後遲到的 callback 不會再 `app.quit()`。
- `tests/test_update_flow.py`：新增 autouse fixture `_destroy_test_windows`
  真正銷毀每個假視窗；`test_closing_cancels_download_and_never_installs` 只發
  `finished_download`（正是要驗的「關閉中收到 worker callback」），不讓延後關窗跑完
  而往共用 QApplication 丟 `quit()`；worker 測試改為不啟動真 QThread 的
  `test_download_worker_throttles_progress_and_flags_verifying`。

## 最終回歸

命令（每次都重用同一個 `--basetemp`，即先前會卡死的條件）：

```powershell
py -3 -X utf8 -m pytest tests/test_updater.py tests/test_update_flow.py `
  tests/test_editor_data_safety.py tests/test_window_integration.py `
  -q -p no:cacheprovider --basetemp tmp/next-update-tests
```

| 回合 | exit code | 結果 |
| --- | --- | --- |
| 1 | 0 | 267 passed in 36.90s |
| 2 | 0 | 267 passed in 28.77s |
| 3 | 0 | 267 passed in 30.04s |

三回合後 `%APPDATA%/python/markdown-viewer/recovery` 殘留快照數 = **0**。
`test_tab_switch_from_split_preserves_dirty_buffer_and_restores_it` **三回合皆通過**。

各檔案數量：`test_updater.py` 44、`test_update_flow.py` 27、
`test_editor_data_safety.py` 56、`test_window_integration.py` 140。

`git diff --stat` 只含任務範圍檔案（`app/updater.py`、`app/update_flow.py`、
`app/toolbar_utilities.py`、`app/window.py`、`tests/test_updater.py`、
`tests/test_update_flow.py`、`tests/test_window_integration.py`）；
未追蹤的 `tools/benchmark_markdown_first_load.py`、
`tests/test_benchmark_markdown_first_load.py` 是另一個 agent 的檔案，未更動。
**本次未 commit。**


## Fresh review 修正（2026-09-08，依 `2026-09-08-next-update-download-review.md`）

### A. 全套 exit 1：殘留的舊斷言

`tests/test_toolbar_utilities.py::test_theme_and_update_states_survive_theme_refresh`
仍斷言 downloading 時按鈕 disabled。已按新行為改寫（downloading／verifying 可按、
tooltip 帶版本與「按一下查看進度」），並補上 `ready`／`cancelled` 兩個新狀態的斷言。
全 `tests/` 掃過，沒有其他同類舊斷言（`test_window_integration.py:1846` 已是新行為）。

### B. 關窗有界收尾：改成真的有界，並更正上限數字

先前只取消 download worker，check worker 完全沒有取消途徑，`CLOSE_WAIT_MS = 5000`
變成零引用死常數，且「5 秒有界」的說法不成立。

修正：

- `app/updater.py`：`check_for_update()` 新增 `cancel` 與 `timeout` 參數，套用與下載
  相同的 `_watch_for_cancel()` 守望執行緒——取消時直接 `response.close()`，
  正在讀取的檢查毫秒級收尾。新增具名常數 `CHECK_TIMEOUT = 15.0`（原本是寫死的 `timeout=15`）。
- `app/update_flow.py`：`UpdateCheckThread` 取得 `cancel_event` 與 `cancel()`；
  `defer_close_until_updates_finish()` 現在會同時取消 download 與 check 兩個 worker。
- 刪掉死常數 `CLOSE_WAIT_MS`，把真實上限寫進 `defer_close_until_updates_finish()` 的註解。

**最終上限與理由**：**20 秒**（= `max(CHECK_TIMEOUT=15s, DOWNLOAD_TIMEOUT=20s)`），
不是 5 秒。理由：取消一旦送出，只要 socket 上有資料在流動，守望執行緒關掉 socket，
worker 毫秒級結束；真正的最差情況是取消剛好落在「connect 尚未完成」的空窗，
此時沒有 socket 可關，只能等該次 connect 依 socket timeout 逾時返回。
**另有一段兩者都不涵蓋**：`socket.getaddrinfo()` 的 OS DNS 解析不吃 Python 的 timeout，
極端網路環境下可能再多數秒。已在原始碼註解與此處都寫明，不宣稱 5 秒。
不使用 `QThread.terminate()`，執行中的 QThread 絕不銷毀。

新測試：`test_closing_also_cancels_a_running_check`。

### C. 取消後立刻重試不再阻塞 GUI

`download_update()` 原本在 GUI 執行緒 `wait(2000)`，逾時後無聲失敗。改為偵測到
「上一個工作已取消但仍在收尾」時**立即返回**，並在狀態列顯示
「正在收尾上一次下載，請稍候再按一次重試。」（4 秒）。
新測試 `test_retry_during_worker_teardown_reports_instead_of_blocking`
把 fake 的 `wait()` 換成會拋 AssertionError 的版本，證明沒有任何等待。

### D. `_watch_for_cancel()` 回傳值

下載與檢查兩處都改成接住回傳的 watcher，並在 `finally` 裡 `watcher.join(timeout=1.0)`，
確保守望 daemon 不會活得比它監看的傳輸久。

### 修正後全套回歸

```powershell
py -3 -X utf8 -m pytest tests -q -p no:cacheprovider --basetemp tmp/next-update-fix
```

**exit code 0 · 1503 passed, 75 skipped in 59.02s**（先前為 exit 1）。
