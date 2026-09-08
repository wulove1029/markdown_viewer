# 2026-09-08 首頁新增入口實作進度

任務來源：`docs/CLAUDE-CODE-NEXT-OPTIMIZATIONS.md` 第 4、6 節。分支
`worktree-agent-aa205a789c883b4c9`，隔離 worktree
`E:\markdown_viewer\.claude\worktrees\agent-aa205a789c883b4c9`。

## 現況盤點（開工前讀碼）

- `app/new_note_dialog.py::NewNoteDialog` 已具備檔名／類型／編輯方式／瀏覽，
  已相當接近規格要求的「單一合併對話框」，缺口是不支援 folder=None（尚無位置）。
- `app/window.py::_new_note()` 既有優先序：明確資料夾 -> 目前選取 -> 第一個
  文件庫；缺「上次成功位置」；且無任何位置時會先跳一個獨立
  `QFileDialog.getExistingDirectory`，不符合規格「同一視窗瀏覽」。
- `app/window.py::_home_action()` 已用 `self._current_file is not None` 擋掉
  有文件開啟／待復原頁（`_load_document` 在顯示待復原頁前就設定
  `self._current_file = path`）。
- `app/renderer.py::_DocumentPage.acceptNavigationRequest` 對
  `https://markdown-viewer.invalid/home/*` 的攔截已同時檢查
  `_view._current_path is None` 與 `nav_type ==
  NavigationTypeLinkClicked`，因此文件正文同網址、待復原頁（無 actions 區塊）、
  過期程式化導覽都不會誤觸——這部分已足夠健壯，未修改判斷邏輯，只加測試鎖住行為。
- `app/file_ops.py::create_document` 舊實作先 `path.exists()` 預檢，再用
  `atomic_write_bytes`（`os.replace`，非獨占）寫入，預檢後的建立競爭會被
  覆寫過去建立的檔案——這是規格要求要修的漏洞。

## 已完成的修改

1. `app/file_ops.py::create_document`：改為預檢後用 `path.open("xb")`
   獨占建立，`FileExistsError` 轉為既有的「已存在同名檔案」`OSError`訊息。
2. `app/new_note_dialog.py::NewNoteDialog`：`folder` 參數改為
   `str | Path | None`；`None` 時資料夾標籤顯示「請選擇資料夾（按「瀏覽…」
   選擇建立位置）」、建立鈕停用、`_browse_folder` 起始目錄退回空字串（不落回
   程式目錄）；`_attempt_create`/`_revalidate`/`target_path`/`folder()` 都
   對 `None` 分支處理。
3. `app/window.py::_new_note`：加入 `_last_new_note_folder()`（讀
   QSettings `last_new_note_folder`，`Path.is_dir()` 存在才用，離線/不存在
   靜默略過，只在對話框開啟當下查一次，不逐鍵探測）與
   `_remember_new_note_folder()`（只在 `dialog.created_path()` 非 None 後呼叫）。
   位置優先序改為：明確資料夾 -> 目前選取 -> 上次成功位置 -> 第一個文件庫；
   全部落空時直接把 `folder=None` 傳進 `NewNoteDialog`，移除原本的獨立
   `QFileDialog` 前置步驟。
4. `app/window.py::_home_action`：新增 `elif action == "new": self._new_note()`
   分支，共用既有 guard。
5. `app/renderer.py`：新增 `_home_actions_html()` 純函式（可離線單元測試，不需
   WebEngine），`show_empty()` 改呼叫它；新增「新增筆記」為第一個
   （`home-action-primary`）動作連結並標示 `Ctrl+N`。

## 測試

```
py -3 -X utf8 -m pytest tests/test_file_ops.py tests/test_text_support.py tests/test_window_integration.py -q -p no:cacheprovider --basetemp tmp/next-home-tests
```
187 passed, exit 0。

```
py -3 -X utf8 -m pytest tests/test_editor_workspace_integration.py -q -p no:cacheprovider --basetemp tmp/next-home-tests
```
5 passed, exit 0。

## 全套測試卡住的根因（與本功能改動無關）

第一次跑 `pytest tests -q --basetemp tmp/next-home-tests` 在 88% 處卡住 2 分鐘以上，
stack dump 停在 `app/window.py:3479`（`_prepare_recovery_state` 內
`RecoveryDialog(...).exec()`），測試是既有、未被本任務碰過的
`test_tab_switch_from_split_preserves_dirty_buffer_and_restores_it`。

診斷：`MainWindow.__init__`（`app/window.py:299`）用 `RecoveryStore()`
（`app/recovery.py`），沒有指定 `directory`，預設落在真實的
`%APPDATA%/python/markdown-viewer/recovery`（依 `py -3` 啟動時的
org/app name 解析），測試套件從未隔離這個路徑。該既有測試會刻意留一個
「未儲存」的 dirty buffer 來驗證分頁切換行為，這會啟動 750ms 的
`_recovery_timer`；只要之後任何測試呼叫 `qapp.processEvents()`
（套件裡到處都有）在那 750ms 之後執行，計時器就會觸發，把草稿快照寫進
真實 AppData 目錄，檔名用來源絕對路徑的 sha256 命名。

因為今天我重複用同一個 `--basetemp tmp/next-home-tests*` 跑了好幾次全套
測試，同一個測試 id 對應的 tmp 路徑字串是固定的，所以「上一輪」洩漏在
AppData 的快照，會被「這一輪」用同一路徑重新開檔時比對到內容不同，
觸發 `_prepare_recovery_state` 顯示一個沒有人能在 offscreen 測試環境按掉
的真實 modal `RecoveryDialog`，整個 pytest 行程被卡死到手動 kill 為止。

證據：清掉 AppData 下的 `recovery` 資料夾（確認每個殘留檔案的
`source_path` 都指向本 worktree的 `tmp/next-home-tests*/...` 測試路徑，
不是使用者真實文件，才刪除）後，全套測試立刻恢復乾淨。之後另一名 agent
在主樹 `tests/test_window_integration.py` 加了 autouse fixture
`_isolated_recovery_store`，把 `window_mod.RecoveryStore` monkeypatch 成
指向 `tmp_path / "recovery-store"`；本 worktree 已同步移植同一個 fixture
（見「已完成的修改」第 6 點）。

結論：這是既有、與本任務的 `_home_action`／`_new_note`／
`create_document`（`open(..., "xb")`）改動無關的測試隔離缺口，只是被我
今天重複使用固定 `--basetemp` 的跑法意外觸發。加上 fixture 隔離後，
`--basetemp` 即使重複使用也不會再從真實 AppData 撿到殘留快照。

## 全套測試（含隔離修補後）

```
py -3 -X utf8 -m pytest tests -q -p no:cacheprovider --basetemp tmp/next-home-tests-final2
```
Exit 0；`1472 passed, 75 skipped` in 50.50s；跑完後複查
`%APPDATA%/python/markdown-viewer/` 沒有再產生新的 `recovery` 資料夾，
確認 fixture 生效。

## 驗收案例核對

1. 全新設定、沒有文件庫：`test_home_action_new_on_a_brand_new_setup_opens_dialog_and_creates`
   （`tests/test_window_integration.py`）——首頁觸發時 dialog 收到 `folder=None`
   （不是先跳獨立 QFileDialog），選位置後成功建立、`_current_file` 更新。PASS。
2. 位置優先序／離線上次位置回退：
   `test_file_tree_new_note_uses_the_same_dialog_for_the_requested_folder`（明確資料夾）、
   `test_new_note_creates_opens_and_edits`（目前選取）、
   `test_new_note_prefers_last_successful_location_over_library_root`（上次位置優先於文件庫）、
   `test_new_note_falls_back_when_last_location_no_longer_exists`（離線/已刪除回退）、
   `test_new_note_falls_back_to_first_library_root`。PASS。
3. MD 原始碼／MD Office／TXT 建立後進入預期模式＋儲存重開一致：
   既有 `test_new_md_note_uses_original_markdown_split_by_default`、
   `test_new_md_note_can_open_directly_in_explicit_office_route`、
   `test_new_txt_note_opens_plain_editor_not_split`（模式）＋本次新增
   `test_new_md_source_note_save_and_reopen_round_trips`、
   `test_new_txt_note_save_and_reopen_round_trips`（輸入、`_save_edits()`、關閉分頁、
   `open_path` 重開，比對 `_editor.toPlainText()` 與磁碟內容）。PASS。
   Office（WYSIWYG）路線的重開一致性需要真實 Vditor JS 橋接，離線 pytest 無法模擬，
   標為手動待驗。
4. 取消／空白名稱／重名／保留字／只讀資料夾／建立競爭：
   `test_dialog_cancel_creates_nothing`、`test_dialog_duplicate_keeps_input_and_stays_open`、
   `validate_new_note`/`file_ops.is_valid_name` 既有測試（空白、保留字）、
   本次新增 `test_dialog_write_failure_leaves_no_empty_file_and_keeps_input`（模擬唯讀/
   權限錯誤：`file_ops.create_document` 拋 `OSError`，dialog 保留輸入、不留檔）、
   `test_create_document_uses_exclusive_create_against_a_race`（file_ops 層級用
   `monkeypatch` 模擬預檢通過後另一支寫入者已建立同名檔，驗證 `open("xb")` 不覆寫、
   原內容不變）。PASS。真實 Windows 唯讀資料夾的錯誤文字未逐字核對（見下方未確認）。
5. 真首頁可觸發／有文件開啟／待復原頁／過期導覽不誤觸：
   `test_home_action_new_opens_new_note_dialog_on_real_home`、
   `test_home_action_new_ignored_while_a_document_is_open`、
   `test_home_action_new_ignored_during_pending_recovery`（用真實
   `_load_document`/`_activate_tab` 流程，確認 `_current_file` 在顯示待復原頁前就已
   設定，guard 生效）。`app/renderer.py::_DocumentPage.acceptNavigationRequest` 對
   「文件正文同網址」與「過期 WebEngine 導覽」的既有雙重防護（`_current_path is None`
   ＋`NavigationTypeLinkClicked`）未變動、只加了 `_home_actions_html()` 純函式測試
   鎖住連結內容，未另跑真實 QWebEngineView 端到端點擊（超出 offscreen pytest 能力，
   標為手動待驗）。PASS（自動化涵蓋範圍內）。

全套 `pytest tests -q --basetemp tmp/next-home-tests-final2`：exit 0，
`1472 passed, 75 skipped`，未再觸發任何 recovery 卡死或其他失敗。
`git diff --stat` 只涵蓋 `app/file_ops.py`、`app/new_note_dialog.py`、
`app/renderer.py`、`app/window.py`、`tests/test_file_ops.py`、
`tests/test_text_support.py`、`tests/test_window_integration.py`。

## 已知未確認 / 手動待驗

- 540px 高度、明暗主題下的實際視覺操作是離線 pytest（offscreen）不會渲染
  像素，標為手動待驗。
- 只讀資料夾情境在 Windows 上實際觸發 PermissionError 的訊息文字未逐字核對，
  僅驗證走既有 OSError 錯誤路徑不留空檔。
