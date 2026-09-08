# 消費者操作流程盤點（2026-09-08）

範圍：首次使用、新增筆記、文件庫、快速開啟、搜尋、模式與捷徑可發現性。唯讀檢查產品程式；只在暫存資料夾建立測試文件，未開啟主程式實際使用者工作階段，未修改產品程式或使用者文件。這是改善建議，不代表修改已完成。

優先序：P1＝直接阻礙日常操作或產生不完整結果；P2＝降低理解與操作成本。工期需待主專案整體規劃。

## 已有基礎，應保留

- 首頁已提供拖曳說明、開啟文件、最近文件、快速開啟，不是完全沒有導引：`app/renderer.py:443`。
- 文件庫已有背景掃描、加入文件庫、管理、重新整理、未設定提示；不需再做一套文件庫：`app/left_panel.py:79`、`app/file_browser.py:731`、`app/file_browser.py:1051`。
- 模式已有同一個下拉選單「閱讀／Markdown／並排預覽／Office 編輯」，不是缺少模式選擇：`app/window.py:937`。
- 新增筆記已有即時檔名檢查、重名檢查、位置瀏覽、失敗保留輸入：`app/new_note_dialog.py:40`、`app/new_note_dialog.py:121`、`app/new_note_dialog.py:223`。
- 快捷鍵已有統一登錄表、選單提示和可搜尋說明頁：`app/shortcuts.py:36`、`app/window.py:642`、`app/shortcuts_dialog.py:110`。

## 1. P1：全域搜尋的承諾與實際檔案範圍不符

**確認的缺口**：文件支援 `.md`、`.markdown`、`.txt`、`.pdf`；搜尋輸入框寫「搜尋所有文件庫內容」，實作卻只搜 `.md`。因此連同樣是 Markdown 的 `.markdown` 都找不到，使用者容易認定文件遺失或搜尋故障。

證據：`app/file_types.py:7`、`app/global_search.py:99`、`app/global_search.py:199`、`app/window.py:696`。

建議先讓 `.markdown`、`.txt` 納入相同文字搜尋；搜尋介面明列涵蓋類型，PDF 是否納入另依效能規劃，不讓「所有內容」暗示目前已支援。驗收：三種文字檔存相同關鍵字，搜尋均出現；未涵蓋 PDF 時介面可看出限制。

**實跑已確認**：在暫存庫建立 `.md`、`.markdown`、`.txt` 及子目錄 `.md`，同字搜尋只回傳兩個 `.md`。

## 2. P1：Ctrl+P 無法找到已加入文件庫但未開過的文件

**確認的缺口**：快速開啟只合併「最近清單」與「目前檔案同層」，未列舉已加入文件庫或子目錄。使用者看得到側欄文件，卻無法用快速開啟找到它。

證據：`app/window.py:5178` 至 `_quick_open_candidates()` 結束處；無候選時只顯示四秒狀態訊息：`app/window.py:5201`。

建議接既有文件庫掃描快取，最近開啟加權靠前；無候選時仍開啟面板，提供「開啟文件／加入資料夾」。驗收：已加入文件庫中從未開啟的子目錄文件，可直接用檔名片段找到。

**實跑已確認**：目前文件旁的三種文字檔出現在候選，含相同關鍵字的 `nested/nested.md` 不出現。候選函式使用無狀態 stub 執行，未建立 MainWindow 或更動設定。

## 3. P1：全域搜尋結果無法用 Enter 開啟

**確認的缺口**：全域搜尋清單只連接 `itemClicked`，不像快速開啟已處理 Enter。純鍵盤完成搜尋後仍需改用滑鼠。

證據：結果清單的 `itemClicked` 在 `app/global_search.py:204`；`:216` 的 Enter 只處理搜尋輸入框，未處理結果清單啟動。對照 `app/quick_open.py:64`、`app/quick_open.py:82`。

建議支援結果 `itemActivated`；搜尋框 Down 移至第一筆結果、上下鍵切換、Enter 開啟並跳到匹配位置，避開不可選檔名群組列。驗收：Ctrl+Shift+F → 輸入 → Down → Enter 全程不用滑鼠完成跳轉。

**實跑已確認**：Qt offscreen 中，選中結果列並送出 Return，開啟 callback 次數為 0；同一列以 QTest 滑鼠點擊，次數為 1。這是實際 Qt 事件差異，不只是程式推論。

## 4. P2：新用戶要先理解檔案與編輯器，才能寫第一行

**確認的現況**：首頁只有開啟／最近／快速開啟，沒有新增；未設定文件庫時 Ctrl+N 先跳資料夾選擇，再跳要求檔名、筆記類型與編輯方式的新增視窗。

證據：`app/renderer.py:457`、`app/window.py:1028`、`app/window.py:4749`、`app/new_note_dialog.py:77`、`app/new_note_dialog.py:92`。

**體驗推論**：偏閱讀的使用者可接受，但想把它當日常筆記軟體的新用戶需在書寫前做多次決定。尚未做真人可用性測試，不能宣稱已量到流失。

建議先做低成本改善：首頁加入「新增筆記」，將位置選擇整合在同一視窗，沿用既有「瀏覽」控制；編輯方式先顯示可理解的目前選項，進階說明可展開。若要進一步做「先寫再命名」，必須沿用既有復原／儲存保護，另立需求驗證。驗收：全新設定狀態可由首頁完成第一份筆記，且可明確認出實際儲存位置。

## 5. P2：快速開啟的同名文件只能靠滑鼠懸停辨認

**確認的缺口**：結果列只顯示檔名，完整路徑只放在 tooltip。多文件庫常見 `README.md`、`Meeting.md` 同名情境，鍵盤選擇時難以分辨。

證據：`app/quick_open.py:102`。

建議每列顯示檔名＋較淡的「文件庫／相對路徑」，保留完整路徑 tooltip；搜尋匹配區段可以加強辨識。驗收：不同文件庫兩份同名文件無需懸停即可選對。

**實跑已確認**：輸入兩個不同路徑的 `Meeting.md`，兩個 QListWidgetItem 可見文字完全相同。

## 6. P2：搜尋失敗和真正零結果使用同一個答案

**確認的缺口**：不存在的根目錄、掃描權限錯誤、讀檔失敗、含解碼替代字元的檔案均直接略過；UI 對空結果只顯示「找不到符合的內容」。沒有來源時也是同一結果。使用者無從知道該改關鍵字還是重新連接文件庫。

證據：`app/global_search.py:73`、`app/global_search.py:83`、`app/global_search.py:106`、`app/global_search.py:275`、`app/global_search.py:301`。

建議結果摘要顯示搜尋範圍與「略過 N 個無法讀取的文件」，細節需要時再展開；空文件庫改為可行動提示「加入文件庫」，離線來源提示重新連接。驗收：零匹配、未設定來源、來源不存在、讀取失敗四種狀態可區分。這一項已由程式定位，未模擬 Windows ACL／拔除磁碟。

## 7. P2：教學仍指向已隱藏的編輯入口

**確認的缺口**：README 要使用者點鉛筆或 layers 按鈕來進入兩個編輯器，現有工具列卻將這些按鈕隱藏，改由模式下拉選單呈現；README 也寫偏好設定位在 Tools，但目前位在「設定」選單。

證據：`README.md:57`、`README.md:59`、`README.md:154`；現況 `app/window.py:1002`、`app/window.py:937`、`app/window.py:778`。

建議同步 README／應用內說明與現有畫面；模式名稱以用途表達，例如「閱讀／文字編輯／編輯＋預覽／視覺編輯」，Markdown 與 Office 可作輔助解釋。名稱建議屬於易懂性的產品判斷，未做真人測試；現有模式功能與明確來源路徑應保留。驗收：新使用者照文件所寫能在當前 UI 找到同名入口。

## 實驗紀錄

使用 `py -3 -X utf8 -` 執行隔離 Python 探針，`QT_QPA_PLATFORM=offscreen`，exit code 0。建立測試檔僅使用 Python TemporaryDirectory；global_search 的排除目錄讀取暫時替換為空清單，避免使用個人設定影響結果。載入類別但未建立 MainWindow，因此未載入／復原使用者文件或寫回工作階段。

操作碼已保存至 `docs/audits/evidence-2026-09-08/consumer-flows-probe.py`，再次執行 exit code 0，結果存為同名 `.json`。從專案根目錄重現：

```powershell
py -3 -X utf8 docs/audits/evidence-2026-09-08/consumer-flows-probe.py
```

```text
global_search_types= ['.md', '.md']
quick_open_candidates= ['document.markdown', 'document.md', 'document.txt']
global_search_opened_after_return= 0
global_search_opened_after_mouse_click= 1
quick_open_duplicate_visible_rows= ['Meeting.md', 'Meeting.md']
```

限制：本分工沒有實際桌面可見視窗的人因測試，也未驗收整個安裝版、網路磁碟、螢幕閱讀器或螢幕縮放。建議的使用者影響屬產品判斷；列為確認的行為均附程式位置，其中三類搜尋／快速開啟問題已有直接執行證據。
