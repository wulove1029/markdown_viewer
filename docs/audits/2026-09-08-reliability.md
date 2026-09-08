# 可靠性與消費者便利性盤點（2026-09-08）

## 範圍與驗證

唯讀檢查產品程式，聚焦儲存／復原、附件與檔案搬移、更新／安裝、錯誤處理及操作等待。未修改產品程式、使用者文件或個人設定；新增本報告與隔離驗證資料。全文搜尋由另一份盤點負責。

驗證證據：

- 既有測試：`py -3 -X utf8 -m pytest tests/test_atomic_io.py tests/test_file_ops.py tests/test_recovery.py tests/test_attachments.py tests/test_updater.py -q -p no:cacheprovider --basetemp=tmp/audit-reliability-20260908/pytest-success` → **69 passed in 0.66s，exit 0**。
- 日誌：`docs/audits/evidence-2026-09-08/reliability-pytest-output.txt`。首次測試因 basetemp 的父目錄尚未建立而出現 setup error；建立父目錄後通過，該次失敗不是產品錯誤。
- 三個隔離行為重現：`tmp/audit-reliability-20260908/reproduce.py`，結果 `reproduction-results.json`，exit 0。測試來源／目的、sidecar、復原庫均在專用暫存目錄。
- 更新視窗 offscreen 實跑：`tmp/audit-reliability-20260908/update_ui_probe.py`，結果 `update-progress-ui.json`，exit 0；以假背景 worker 取代下載，不連網、不安裝。
- 本報告列出 8 點：前 3 點有隔離行為重現；後 5 點為有程式依據的產品改善建議。未執行真實安裝、網路／USB 實機效能或破壞性當機測試，未宣稱這些已驗證。

## 建議優先順序

### R1 — P1：搬移筆記時保住圖片與附件連結〔已重現〕

使用「移動到…」把筆記移到另一個資料夾，原本可用的 `assets/attachment.txt` 會失效；附件還留在原資料夾，Markdown 內容沒有重寫。

- 原因：`app/attachments.py:45`、`:48` 產生相對路徑；`app/file_ops.py:100` 只搬文件、`:101` 接著搬 sidecar；`app/file_ops.py:111` 的 move 直接沿用 rename。
- UI 確實走此路徑：`app/file_browser.py:1537`。後續 `app/window.py:5016` 起主要更新分頁、復原、最近項目與索引，不會修相對資源。
- 證據：`move_breaks_relative_attachment.valid_before_move=true`、`valid_after_move=false`、`original_asset_still_exists=true`。
- 改善：搬移前盤點本地相對資源；依資源是否共用，複製必要附件或重算連結；完成後驗證連結仍能解析。可加入「連同附件匯出資料夾／ZIP」供分享與備份。
- 驗收：含圖片、一般附件、空白／中文檔名及共用資源的筆記搬移後仍可開啟資源；其他引用同一資源的筆記不受破壞。

### R2 — P1：註記搬移失敗必須顯示，並保證操作一致〔已重現〕

目的地已有同名 sidecar 時，主文件已移動，註記檔留在原處，函式仍回傳成功。使用者看起來是「搬完後註記不見了」。

- 原因：`app/file_ops.py:98` 只先檢查主檔目的地；`:100` 先搬主檔，`:105` 搬註記，`:106` 吞掉例外。
- 證據：`sidecar_collision_silently_succeeds.document_moved=true`、`original_notes_stranded=true`，仍得到成功 mapping；目的地 sidecar 內容保持原值。
- 改善：預先檢查主檔／sidecar 全部目的地與權限；失敗回復已搬項目，或清楚呈現部分成功、原註記位置與重試入口。文件的 `.bak` 可一併納入搬移策略，目前 sidecar suffix 只列 `.notes.json`、`.highlights.json`（`app/file_ops.py:27`）。
- 驗收：目的地註記同名、唯讀、被占用等情況不得無聲成功；文件與註記仍能配對或提供明確恢復方式。

### R3 — P1：啟動時主動找出所有未儲存草稿〔已重現啟動邏輯缺口〕

本次新開的文件發生異常退出，即使已有復原草稿，若該文件不在上次正常關閉的分頁清單，且沒有 `last_file` 或啟動參數帶回同一路徑，啟動時不會主動帶回。草稿仍在；手動開啟原文件的程式路徑有既有復原入口，本次未真的終止程序後操作完整復原視窗。

- 原因：`app/session_state.py:169` 僅讀 `open_tabs`，`:210` 僅回退 `last_file`；這些在正常關窗時寫入（`:388`、`:391`）。`app/recovery.py:283` 已有完整草稿列舉 API，但啟動流程未使用。
- 證據：隔離庫有 1 份有效草稿，模擬上次 `open_tabs=[]` 後呼叫 `restore_last_session`，`restored_tabs=0`。此為函式重現，未真的終止使用者程式。
- 補充實跑：呼叫手動重開同路徑會使用的 `_prepare_recovery_state`，以 stub 選擇 Restore，取得 `unsaved draft`、`modified=true`，來源仍為 `disk version`。此證明草稿可取回，未驗完整手動重開對話框流程。證據見 `docs/audits/evidence-2026-09-08/reliability-reproduction-results.json` 的 `manual_open_recovery_preparation`。
- 改善：啟動時列舉 recovery store，顯示「找到 N 份未儲存草稿」入口；包括原始檔已刪除、搬移或不在上次分頁的草稿。支援保留、另存與稍後處理。
- 驗收：新開文件編輯至草稿寫入後異常退出，下次啟動不用記得原檔名就能找到並復原。

### R4 — P2：外部衝突增加「比較」與「另存副本」〔設計改善〕

目前外部變更已有保護，但一般使用者仍須面對「捨棄自己的修改」或「覆寫磁碟版本」的二選一，缺少直覺保留雙方內容的路徑。

- 依據：`app/window.py:3652` 儲存衝突只有 Yes／No 覆寫；`:5465` 外部變更詢問是否捨棄自己的編輯。檔案選單 `app/window.py:649` 起含開啟與格式匯出，沒有 Markdown／TXT「另存新檔」。
- 改善：提供「比較差異」「保留目前內容為副本」「載入外部版本」「稍後」；唯讀原檔或原資料夾不存在時也能用另存保住工作。
- 驗收：外部程式修改後仍可一鍵保留兩份版本；不必先全選複製到別的工具。

### R5 — P2：讓儲存與復原狀態長期可見〔設計改善〕

已有保護機制，但使用者不容易知道「原檔已儲存」與「只有復原草稿」的差別。多次儲存後的上版內容也缺少一般使用者可發現的入口。

- 依據：草稿 timer 為 750 ms（`app/window.py:533`），失敗僅 statusBar 顯示 5 秒（`:3359`）；原檔成功「已儲存」顯示 3 秒（`:3695`）。`app/atomic_io.py:92` 每次保留單份 `.bak`；`app/file_browser.py:87` 從檔案樹隱藏 `.bak`。
- 改善：持續顯示「未儲存／草稿已保護／已儲存於 HH:mm／儲存失敗」，失敗保留可操作提示；加上「開啟上一版」或有限版本歷史。版本策略應先沿用現有 `.bak`，避免再做一套基礎備份。
- 驗收：使用者能從當前視窗辨識原檔是否已寫入；復原寫入失敗不會在 5 秒後完全失去提示；可不經檔案總管查看上一版。

### R6 — P2：更新下載可取消、有進度，允許繼續工作〔UI 行為已實跑，改法屬設計建議〕

目前背景 worker 已存在，但下載視窗鎖住整個應用程式，沒有取消按鈕與可判讀的進度。下載慢時使用者只能等。

- 依據：`app/update_flow.py:286` 建立 `QProgressDialog(..., None, 0, 0, ...)`，`:288` 設為 `ApplicationModal`。`app/updater.py:144` 至 `:146` 用單次 `response.read()` 讀完整安裝檔。
- offscreen 證據：`window_modality=ApplicationModal`、`minimum=0`、`maximum=0`、`visible_push_buttons=[]`。
- 改善：分塊下載到暫存檔，回報百分比／已下載大小，提供取消與重試；下載期間可工作，完成後提供「現在安裝／稍後」。
- 驗收：慢速／中斷網路可取消且保留文件工作；大小已知時顯示真實百分比；取消後能重新下載。

### R7 — P2：降低安裝門檻並一致繁體中文〔來源檢查，未跑安裝〕

目前安裝要求系統管理員，安裝精靈主要使用英文；對只想快速看文件的一般使用者仍有門檻。

- 依據：`installer.iss:23` 為 `PrivilegesRequired=admin`；`:27` 只定義 English；`:43` 起直接註冊 `.md` 與 `.markdown` 關聯。
- 改善：提供每位使用者安裝位置或可攜版、完整繁體中文安裝／卸載文案、可選「設為 Markdown 開啟程式」。設定預設程式的完整實際效果需要在乾淨 Windows 帳號驗證，這裡不推斷系統 UserChoice 的結果。
- 驗收：標準使用者帳號可完成選定安裝方式；安裝、首次啟動、升級、移除流程皆有一致的繁體中文提示。

### R8 — P2：大附件匯入與搬移顯示進度並支援取消〔程式結構推論，未量測慢裝置〕

附件匯入目前直接在 UI 操作函式做同步檔案複製，大型影片、PDF 或慢速磁碟可能造成等待時無回饋；跨磁碟搬移也缺少完整策略。

- 依據：`app/window.py:2161` 直接呼叫匯入，`app/attachments.py:51` 同步 `shutil.copy2`；`app/image_paste.py:67` 起同類圖片匯入；`app/file_ops.py:100` 使用 `Path.rename`，未提供跨磁碟複製／驗證／移除流程。
- 改善：超過大小或耗時門檻改走背景任務，顯示進度、目的地及取消；跨磁碟操作先複製驗證，再移除來源，並將 sidecar／連結納入同一作業。
- 驗收：100 MB 附件、慢速來源、跨磁碟目的地皆有明確進度；取消不留下看似完整但其實截斷的附件；資料與 sidecar 不分離。

## 已做好的部分，避免重複投入

- 原子寫入、fsync、單份 `.bak` 及 Windows 短暫占用重試已實作，相關測試通過（`app/atomic_io.py:69`）。
- 復原草稿儲存在 AppData、不覆寫來源；提供左右比較與稍後處理（`app/recovery.py:210`、`app/recovery_dialog.py:37`）。
- 跨分頁的髒內容、原始編碼／換行、外部修改與刪除均有保護分支（`app/window.py:3612`、`:4293`），正常清單內的已刪文件也可憑草稿重開（`app/session_state.py:183`）。
- 正常檔案搬移會同步遷移分頁、最近項目、草稿、編輯模式與索引（`app/window.py:5016`）。問題是資源／sidecar 邊界情況，不是完全沒有搬移支援。
- 附件支援相對連結、同名防碰撞；最近資源跨文件匯入時重算路徑，缺失來源已有提示（`app/attachments.py:13`、`app/window.py:2205`）。
- 更新有背景執行、可關閉自動檢查、每日節流、HTTPS／GitHub 主機限制及可用時的 SHA-256 驗證（`app/update_flow.py:189`、`app/updater.py:155`）。

本次沒有足夠資料對真實大型文件的速度下定論；改善效能的優先順序應再以使用者典型文件、附件大小、儲存裝置實測決定。
