# 驗收報告：首頁新增入口（78fcfd6）

驗收對象：worktree `agent-aa205a789c883b4c9` / branch `worktree-agent-aa205a789c883b4c9` / commit `78fcfd6`。
對照規格：`docs/CLAUDE-CODE-NEXT-OPTIMIZATIONS.md` 第 4、6 節。獨立查證，未採信實作者紀錄的結論。

## 逐條結果

1. PASS — `app/renderer.py:171-178` `_home_actions_html()`：新增筆記為首個、`class="home-action-primary"`、含 Ctrl+N；開啟/最近/快速開啟保留。`tests/test_window_integration.py:2979-2991` 驗證順序與字串。
2. PASS（有缺口，見挑錯）— `app/window.py:1032-1051` `_home_action` 靠 `self._current_file is not None` 擋掉「有文件開啟」與「待復原頁」（`_load_document` 設定 `_current_file` 早於顯示復原頁），測試 `test_home_action_new_ignored_while_a_document_is_open`、`test_home_action_new_ignored_during_pending_recovery`（`tests/test_window_integration.py:2944-2976`）皆實際建立情境後斷言 `_new_note` 未被呼叫，非純字串比對。但「文件正文中同網址連結」與「過期 WebEngine 導航」的把關邏輯在 `app/renderer.py:189-195`（`_current_path is None` + `NavigationTypeLinkClicked`），是本次未改動的既有程式碼，且**沒有任何測試直接呼叫 `_DocumentPage.acceptNavigationRequest` 驗證這兩種情境**——全部新測試都繞過 renderer 直接呼叫 `win._home_action("new")`，只驗到 `_home_action` 這層，不是規格點名的「文件正文同網址連結」「過期導航」路徑本身。
3. PASS — `app/window.py:412`（`on_new_note_requested = self._new_note`）、`:651`（選單 `file.new`）、`:1051`（首頁動作）皆呼叫同一個 `_new_note()`，無另造儲存邏輯，確認建立後走 `_on_browser_note_created` 共用流程。
4. PASS — `NewNoteDialog` 擴充（同一類別加 `folder: str|Path|None`），非新視窗；`folder=None` 顯示「請選擇資料夾（按「瀏覽…」選擇建立位置）」（`app/new_note_dialog.py:185-188`）；瀏覽在同一對話框（`_browse_folder`）；`_revalidate` 在 `folder is None` 時停用建立鈕（`:226-230`）；未見預設程式目錄或 cwd。
5. PASS — `app/window.py:4880-4887` 優先序：`requested_folder` → `browser.selected_directory()` → `_last_new_note_folder()` → `library_roots()[0]`，與規格一致。`_last_new_note_folder()` 只在 `_new_note()` 呼叫一次（非每次按鍵），`_remember_new_note_folder` 只在 `path is not None`（建立成功）後寫入（`:4899`）。Dialog 內 `validate_new_note` 每次按鍵檢查目標檔是否已存在屬既有重名檢查行為，非「上次位置」探測。
6. PASS — `app/file_ops.py` `create_document` 保留 `exists()` 友善預檢，實際寫入改用 `path.open("xb")` 獨占建立，`FileExistsError` 轉為可讀 `OSError`。`tests/test_file_ops.py::test_create_document_uses_exclusive_create_against_a_race` 用 `monkeypatch` 讓預檢說謊（回傳 False）製造真實 TOCTOU 競爭，驗證既有內容未被覆寫——是行為測試，非字串比對。
7. PASS — `app/window.py:4906-4907`：`browser.reveal_created_note(path)` 與 `_on_browser_note_created` 各呼叫一次，內部 `_open_file`→聚焦、`_refresh_link_index(force=True)`；TXT 進 `_enter_edit_mode`，MD 依 backend 進原始碼或 Office；取消/例外時 `dialog.created_path()` 為 None，`_new_note` 提前 return，不留空檔（`test_dialog_write_failure_leaves_no_empty_file_and_keeps_input`、`test_dialog_cancel_creates_nothing` 驗證磁碟與 input 狀態）。未見 Office 被設為全域預設。
8. 未確認 — 找不到任何測試以 `resize(w, 540)` 檢查對話框在 540px 高度下主要元件可見/可捲動，也沒有 Tab 順序、Esc 取消的專屬測試。`_name_input.setFocus()`（`app/new_note_dialog.py:158`）確認預設焦點在檔名，其餘（540px、Tab 順序、Esc）僅能推定 Qt 預設行為，未經測試驗證。
9. PASS — `tests/test_window_integration.py:383-398` `_isolated_recovery_store`（autouse）把 `RecoveryStore` 導向 `tmp_path`；`:401-421` `_clean_settings`（autouse）把 `QSettings` 重導向 tmp 的 `settings.ini` 並清空 recent/geometry 等 key，`_last_new_note_folder` 用同一個 `QSettings(_ORG, _APP)` 呼叫路徑，故一併被隔離。實跑後 `%APPDATA%\python\markdown-viewer\recovery` 檔案數為 0，無殘留。
10. PASS — `py -3 -X utf8 -m pytest tests -q -p no:cacheprovider --basetemp tmp/next-home-review`：exit 0，`1472 passed, 75 skipped`，42.91s，無卡住。
11. PASS — `git show --stat 78fcfd6`：僅 `app/file_ops.py`、`app/new_note_dialog.py`、`app/renderer.py`、`app/window.py`、`docs/upgrades/2026-09-08-next-home-new.md`、`tests/test_file_ops.py`、`tests/test_text_support.py`、`tests/test_window_integration.py`；未見 updater.py、update_flow.py、md_converter.py、docs/CLAUDE-CODE-NEXT-OPTIMIZATIONS.md。

## 挑錯清單

- 規格第 88 行明確要求「文件正文中的同網址連結、過期 WebEngine 導航都不能意外建立文件」，本次新增測試全部繞過 renderer 層，只驗證 `_home_action()` 對 `_current_file` 的守門，**沒有測試直接命中 `_DocumentPage.acceptNavigationRequest` 中 `_current_path is None` + `NavigationTypeLinkClicked` 這段判斷**（該段本身也未被本次改動）。屬「宣稱涵蓋但測試未真正命中該情境」的落差，建議至少補一個對 `_DocumentPage` 或 `RendererView.home_action_requested` 訊號的直接測試（模擬 `_current_path` 非 None 時同網址連結、以及非 LinkClicked 的導航類型）。
- 540px 高度可操作性、Tab 順序、Esc 取消三項規格要求（第 94 行）沒有對應測試，也未見任何 offscreen `resize` 驗證證據。

## 建議

功能邏輯、共用路徑、獨占建立、範圍與測試隔離均查證通過，且全量測試綠燈。唯一實質風險是「同網址連結/過期導航」防護只靠既有未改動程式碼、缺乏直接測試佐證，加上 540px/Tab/Esc 未驗證。建議：可合併，但合併前或合併後盡快補上述兩類測試，不宜視為本項規格已完全驗收。
