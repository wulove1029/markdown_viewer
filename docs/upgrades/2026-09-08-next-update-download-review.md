# 更新下載流程 — 獨立驗收（2026-09-08）

驗收者：fresh reviewer，未參與實作。範圍＝主工作樹未 commit 的 `app/updater.py`、`app/update_flow.py`、
`app/toolbar_utilities.py`、`app/window.py` 與對應測試。規格＝`docs/CLAUDE-CODE-NEXT-OPTIMIZATIONS.md` §5、§6。

## 結論

**暫不可 commit**：全套測試有 1 個既有測試失敗（`tests/test_toolbar_utilities.py::test_theme_and_update_states_survive_theme_refresh`），
是本次刻意行為變更未同步既有測試造成，可穩定重現。修掉該測試後即可提交。

## 逐條結果

### 1. download_installer 串流／驗證 — PASS

- `app/updater.py:246-383`：固定 `DOWNLOAD_CHUNK_SIZE=64KB`（`updater.py:28`）串流；
  `job_dir = tempfile.mkdtemp(prefix="mdviewer-update-")`（`updater.py:288`），寫 `<name>.exe.part`。
- 邊寫邊 `hasher.update(chunk)`；完成後才比對 `expected`，通過才 `part.replace(target)`（`updater.py:376-378`）。
- Content-Length 存在且 `downloaded != total` → `UpdateError`（`updater.py:365-369`）。
- 空檔（`downloaded == 0`）、hash mismatch、寫入 `OSError`（磁碟滿）皆 raise；`except BaseException:` →
  `_remove_job_dir(job_dir)`（`updater.py:380-383`），只刪自己 `mdviewer-update-` 前綴的目錄（`updater.py:200-208`）。
- 缺失／畸形 digest：在建立任何檔案前就 `raise UpdateDigestUnavailable`（`updater.py:276-281`），
  不會被標成驗證通過；`verify_installer()` 在無 digest 時直接回 False（`updater.py:163-166`）。
- 測試：`test_download_truncated_against_content_length_fails`、`test_download_rejects_empty_body`、
  `test_download_rejects_digest_mismatch`、`test_download_reports_disk_full`、
  `test_download_refuses_missing_or_malformed_digest`、`test_download_never_holds_whole_file_in_one_read`。

### 2. 安全檢查未放寬 — PASS

- `_require_trusted_url(update.asset_url)`（`updater.py:266`）與 redirect 後
  `_require_trusted_url(response.geturl())`（`updater.py:311`）都保留；`_TRUSTED_HOSTS` / HTTPS 判斷未被 diff 觸及。
- 檔名仍 `Path(asset_name).name` + 必須 `.exe` 且含 `setup`（`updater.py:270-273`）。
- 測試 fixture 只 monkeypatch `urllib.request.urlopen`，URL 一律用真實 github 網域
  （`tests/test_updater.py:168, 179-181`）；另有 `test_download_rejects_untrusted_url`、
  `test_download_rejects_untrusted_redirect_target` 正向確認未放寬。

### 3. 取消五情境 — PASS（有一個可接受的上限缺口，見挑錯 B）

- 未連線：`urlopen` 前 `cancel.is_set()` 預檢（`updater.py:283-284`）；`test_cancel_before_connect_never_opens_a_socket`。
- 等待 read／下載中：`_watch_for_cancel()`（`updater.py:210-234`）以 0.05s 輪詢並 `response.close()` 讓阻塞的 read 立刻拋錯；
  迴圈每個 chunk 前也檢查 cancel（`updater.py:318-319`）。`test_cancel_mid_download_unblocks_quickly`。
- 驗證中／完成競爭：hash 是串流內建，迴圈結束後再檢查一次 cancel（`updater.py:361-362`），
  取消時不 rename、job dir 被清掉。`test_cancel_racing_completion_leaves_no_installer`。
- race 審查：`response` 是每次呼叫的區域物件，watcher 只 close 自己那個 → 不會關掉下一個工作的連線；
  `done.set()` 在 `finally`（`updater.py:352`），watcher 最多再存活 0.05s 後自行結束；重複 close 被 try/except 吃掉。
  `_watch_for_cancel` 在 `urlopen` 成功之後才啟動，故不存在「response 尚未建立就 close」的 race。

### 4. controller — PASS

- request id：`_next_request_id()` / `_is_current_request()`（`update_flow.py:140-150`），progress、verifying、
  結果三種 callback 都帶 bound `request`（`update_flow.py:569-578`）；舊 worker 的成功結果會被
  `cleanup_job_dir()` 丟棄（`update_flow.py:718-723`）。`test_stale_progress_and_result_are_ignored`。
- 節流：`UpdateDownloadThread._on_progress` 以 `PROGRESS_INTERVAL_MS=150` 節流後才 emit signal，
  GUI 更新在主執行緒的 slot（`update_flow.py:300-314`、`665-703`）。`test_download_worker_throttles_progress_and_flags_verifying`。
- 非模態：`setWindowModality(NonModal)`（`update_flow.py:496`）＋ `_HideOnCloseFilter` 讓 Esc／關閉只隱藏不取消。
- 成功後不自動安裝：`on_update_download_done()` 只設 `UPDATE_READY` 並 `prompt_install_ready()` 詢問
  （`update_flow.py:745-756`）。`test_successful_download_becomes_ready_without_installing`。
- 「稍後」：保留 `_update_ready_installer` 於本 session，不落 QSettings（`window.py:312-315` 註解、
  `update_flow.py:791-797`）。`test_later_keeps_file_for_this_session_and_installs_on_next_click`。
- 「立即安裝」：`_request_live_wysiwyg_snapshot(..., purpose="安裝更新")` → `_confirm_close_all_edits()`；
  任一步取消都回到 `UPDATE_READY`、不啟動不退出（`update_flow.py:816-838`）。
  `test_install_flushes_office_snapshot_before_saving`、`test_install_aborts_when_user_cancels_saving`。
- 啟動前重新驗證：`install_ready_update()` 與 `_launch_installer()` 各做一次 `verify_installer()`
  （`update_flow.py:805`、`846`）。startDetached 失敗 → 保留檔案、回 READY、可重試（`update_flow.py:858-868`）。

### 5. 關閉 app — 條件 PASS（見挑錯 B）

- `defer_close_until_updates_finish()`（`update_flow.py:222-266`）先 `cancel_update_download(quiet=True)`，
  再 `event.ignore()` + `hide()`，靠 `thread.finished` 恢復；沒有在執行中銷毀 QThread
  （thread parent 是 QApplication，且只 `deleteLater`）。
- 關閉中的 callback：`on_update_download_done()` 在 `_closing(window)` 時 return，不會 `prompt_install_ready`
  （`update_flow.py:751-755`）；`install_ready_update()`／`_launch_installer()` 開頭也擋 `_closing`。
  `test_closing_cancels_download_and_never_installs`。
- **缺口**：移除 5 秒 QTimer 後，「檢查更新」worker 的上限是 `check_for_update()` 的 `urlopen(timeout=15)`
  （`updater.py:120`），不是 5 秒；且 socket timeout 不涵蓋 `getaddrinfo`，慢 DNS 下實際上限更高。
  `CLOSE_WAIT_MS = 5000`（`update_flow.py:61`）已成死常數，全專案無任何引用，其註解宣稱的
  「5 秒有界」與程式碼不符。功能上仍有界（不會永久殘留），但與規格 §5「worker 5 秒內收尾」有落差。

### 6. window.py 改動範圍 — PASS

`git diff app/window.py` 僅 3 個 hunk：移除 `UPDATE_DOWNLOADING` import、新增更新流程狀態欄位
（`window.py:306-318`）、`_update_action` 啟用條件與 `_on_update_button_clicked()` 改為委派
`update_flow.on_update_button_clicked()`。無其他模組改動。

### 7. 實跑

```
py -3 -X utf8 -m pytest tests/test_updater.py tests/test_update_flow.py \
  tests/test_editor_data_safety.py tests/test_window_integration.py \
  -q -p no:cacheprovider --basetemp tmp/next-update-review
→ 267 passed in 27.70s, exit 0

py -3 -X utf8 -m pytest tests -q -p no:cacheprovider --basetemp tmp/next-update-review-full
→ 1 failed, 1500 passed, 75 skipped in 53.20s, exit 1
   FAILED tests/test_toolbar_utilities.py::test_theme_and_update_states_survive_theme_refresh
```

單獨重跑同樣失敗（0.09s）：`tests/test_toolbar_utilities.py:95`
`assert controls.update_button.isEnabled() is False` → 實際 True。
成因＝本次刻意把 `_UPDATE_BUSY_STATES` 縮成只剩 `UPDATE_CHECKING`（`toolbar_utilities.py:37`），
讓 downloading 時按鈕仍可點以重開進度視窗；`tests/test_window_integration.py` 的對應測試已更新，
但 `tests/test_toolbar_utilities.py` 這支舊測試沒有一起更新。屬「規格變更未同步既有測試」，不是隨機失敗。

### 8. 測試隔離 — PASS

- `tests/test_window_integration.py:383-397` 新增 autouse `_isolated_recovery_store`，
  monkeypatch `window_mod.RecoveryStore` → `recovery_mod.RecoveryStore(directory=tmp_path/"recovery-store")`。
  `MainWindow` 唯一的建構點是 `app/window.py:298` 的 `self._recovery_store = RecoveryStore()`（模組層名稱），
  patch 生效。
- 真實路徑 = `QStandardPaths.AppDataLocation / "markdown-viewer" / "recovery"`（`app/recovery.py:29-33`），
  本機為 `%APPDATA%\python\markdown-viewer\recovery`。
- 跑完全套後該目錄已不存在（只剩 `tag_index.json` 與備份目錄），**無新的測試殘留**。
  （驗收中途曾出現 1 個 11:16 的快照，其 `source_path` 指向
  `E:\markdown_viewer\.claude\worktrees\agent-aa205a…\tmp\next-home-tests3\…`，
  來自另一個 worktree 的並行 agent，該 worktree 沒有這支 fixture；非本次改動的漏洞。）

### 9. 172 個外洩快照抽查 — PASS（無真實文件）

目錄：`C:\Users\USER01\AppData\Roaming\python\markdown-viewer\recovery-leaked-backup-20260908`，共 172 個 `.json`。
以腳本讀出每個快照的 `source_path`（schema 見 `app/recovery.py`）：

- 172/172 全部指向 tmp／pytest 測試路徑，**非 tmp 路徑數 = 0**。
- 分布：141 個 `C:\Users\USER01\AppData\Local\Temp\pytest-of-USER01\pytest-1\…`，
  其餘為 `E:\markdown_viewer\tmp\nutS2 / nutXA / nutF1 / nutF2 / next-update-tests / upgrade-full-20260908\…`
  與 1 個 `C:\Users\USER01\AppData\Local\Temp\mdv-work-965e416df0514fe6\…`。
- 檔名幾乎清一色來自 `test_tab_switch_from_split_pre0`。
- **沒有任何一個快照指向使用者真實文件。** 該備份目錄可安全刪除（非本次驗收授權範圍）。

## 挑錯清單

A.（阻擋提交）`tests/test_toolbar_utilities.py::test_theme_and_update_states_survive_theme_refresh` 失敗。
行為變更是刻意的，但既有測試未同步 → 違反 §6「先讀既有測試並按行為補案例」。

B.（中）`CLOSE_WAIT_MS = 5000`（`update_flow.py:61`）定義後從未使用；其註解宣稱關窗等待「有界 5 秒」，
但檢查 worker 的實際上限是 `urlopen(timeout=15)`（`updater.py:120`）＋不受 timeout 保護的 DNS 解析。
規格 §5 要求「worker 5 秒內收尾」。建議二選一：把 check 的 timeout 降到 5 秒，
或恢復一個以 `CLOSE_WAIT_MS` 為期限的收尾 fallback（並保留實作者對舊 singleShot 的修正）。

C.（低）`download_update()` 在「取消後立刻重試」時於 GUI 執行緒 `waiter(2000)` 阻塞最多 2 秒
（`update_flow.py:534-540`）；若 2 秒內舊 worker 未結束，會走到 `show_update_progress()` 回 False 後
直接 return，使用者的重試點擊沒有任何回饋。建議加一則狀態列訊息。

D.（低）`_watch_for_cancel()` 的回傳值在 `download_installer()` 內未被接收也未 join
（`updater.py:307`）。因 `done.set()` 在 `finally`，最多多活 0.05 秒，不構成洩漏，但與
函式簽章回傳 watcher 的意圖不一致，易誤導後續維護。

E.（觀察）規格 §5 要求「記錄最差取消時間」。測試 `test_cancel_mid_download_unblocks_quickly` 有量測邊界，
但未見把最差取消時間落檔成數據；建議在交付紀錄補上實測值。

無其他「宣稱做了但實際沒做」的規格項；HTTPS／可信來源／redirect／安全檔名／digest 缺失處理／
非模態／不自動安裝／稍後保留／安裝前重驗／startDetached 失敗可重試 皆已實作並有對應測試。

---

## 複驗（同日，A–D 修正後）

範圍僅限 A–D 四項與全套回歸；§1–§9 的原結論不受影響（相關程式碼僅在下列位置變動）。

### A — PASS

`tests/test_toolbar_utilities.py:90-119` 已按新行為改寫：`UPDATE_CHECKING` 仍斷言
`isEnabled() is False`（正確，這是唯一真正 busy 的狀態），`UPDATE_DOWNLOADING` /
`UPDATE_VERIFYING` 改為 `is True` 並比對新 tooltip（含版本與「按一下查看進度」），
另補上 `UPDATE_READY`（badge True、「已下載並驗證」）與 `UPDATE_CANCELLED`（「按一下重試」）。
全檔 `grep "isEnabled() is False"` 只剩第 92 行（CHECKING），無舊斷言殘留。

### B — PASS

- `app/updater.py:116-159`：`check_for_update(current_version, *, cancel=None, timeout=CHECK_TIMEOUT)`，
  取得 response 後掛 `_watch_for_cancel()`（`:143`），例外時若 cancel 已設則轉成 `UpdateCancelled`
  （`:150-151`），`finally` 內 `done.set()` + `watcher.join(timeout=1.0)`（`:153-156`），
  讀完後再檢查一次 cancel（`:158-159`）。寫死的 `timeout=15` 已抽成具名常數 `CHECK_TIMEOUT = 15.0`（`:37`）。
- `app/update_flow.py:278-294`：`UpdateCheckThread` 取得 `cancel_event` 與 `cancel()`，
  `run()` 把事件傳進 `check_for_update(cancel=...)`。
- `app/update_flow.py:228-233`：`defer_close_until_updates_finish()` 先 `cancel_update_download(quiet=True)`，
  緊接著取消 check worker，才做 isRunning 快照。新測試
  `tests/test_update_flow.py:952 test_closing_also_cancels_a_running_check` 覆蓋。
- `CLOSE_WAIT_MS` 已刪除：`grep -rn CLOSE_WAIT_MS app/ tests/` 零命中（僅剩交付紀錄中的說明文字）。
- 誠實性：`update_flow.py:260-271` 註解明寫「20 s 是誠實上限（CHECK_TIMEOUT 15s / DOWNLOAD_TIMEOUT 20s），
  不是先前草稿宣稱的 5 s」，並點名 `getaddrinfo` 的 OS DNS 解析是兩個 timeout 都不涵蓋的一段；
  `updater.py:33-37` 的常數註解、`docs/upgrades/2026-09-08-next-update-download.md:238-262`
  三處說法一致，沒有互相矛盾或美化。

### C — PASS

`app/update_flow.py:546-558`：取消後立刻重試的路徑已移除 `waiter(2000)`，改為在狀態列顯示
「正在收尾上一次下載，請稍候再按一次重試。」（4 秒）後 return。GUI 執行緒不再有任何阻塞等待，
使用者的點擊也有明確回饋。

### D — PASS

兩處呼叫端都接住並 join watcher：`updater.py:140-156`（check）與
`updater.py:340, 387-395`（download，`finally` 內 `done.set()` → `response.close()` →
`watcher.join(timeout=1.0)`，附註解說明不讓守望執行緒活過它所監看的傳輸）。
簽章意圖與呼叫端一致，D 的誤導性已消除。

### 全套回歸

```
py -3 -X utf8 -m pytest tests -q -p no:cacheprovider --basetemp tmp/next-update-review2
→ 1503 passed, 75 skipped in 49.66s, exit 0
```

（較前次多 3 個測試：A 的補強與 `test_closing_also_cancels_a_running_check` 等。）
跑完後真實 `%APPDATA%\python\markdown-viewer\` 下仍只有 `tag_index.json` 與備份目錄，
**無新的 recovery 殘留**。

### 複驗結論

A–D 四項全部 PASS，全套 exit 0，無新增問題。原報告的 E（把最差取消時間落檔成實測數據）
仍是建議事項、不阻擋提交。**可以 commit。**
