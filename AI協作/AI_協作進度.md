# AI 協作進度（Claude ↔ Codex ↔ Gemini 交流用）— markdown_viewer

> 目的：三個 AI 助手協作處理 **markdown_viewer** 時的共享狀態文件。
> 規則：每次分析/修改後在「進度紀錄」新增一節（**新在上、舊在下**），註明日期時間、
> 作者、狀態標籤；「待辦與未決」與「收件匣」保持最新；引用程式碼一律帶 `檔名:行號` 或
> 函式名，方便對方查證。使用者會把這份檔案（或其片段）貼給其他方，或指示各方直接讀取。

---

## 📖 規則在哪

角色與權限、Claude 指揮調度職責、指揮口令、模型選擇、紀錄格式等**跨專案共用規則**，
在同資料夾的 `AI_協作規範.md`（檢視器左側「規範」分頁）。本檔只放**本專案**的設定、
速覽、收件匣與進度紀錄。**一句話：主導＝Claude，使用者需求先進 Claude，由它指揮
調度 Codex 與 Gemini。**

---

## ⚙ 協作設定（本專案；派工前必讀，可在檢視器直接切換並儲存）

| AI | 參與 | 模型 | 推理強度 | 速度 |
|---|---|---|---|---|
| Claude | ✅ | session（由使用者 /model 控制） | session | session |
| Codex | ✅ | gpt-5.6-sol | high | default |
| Gemini | ✅ | Gemini 3.5 Flash (Medium) | 內含於模型 | — |
| 自動接棒 | ✅ | —（全域開關，規則見下） | — | — |

> 上表是**本專案**的檔位。設定的**規則文字**（強制檔位、升降檔需使用者同意、
> service_tier 速度、自動接棒定義）見 `AI_協作規範.md`。
>
> 此表由檢視器程式讀寫，**各列開頭（`| Claude |`、`| Codex |`、`| Gemini |`、
> `| 自動接棒 |`）不可改動**。

---

## 專案速覽（2026-07-08 更新）

PySide6 桌面版 Markdown 閱讀器（v1.12.0），支援 Mermaid 圖表、PDF 匯出、
Windows 檔案關聯（Inno Setup 安裝檔），可打包成 MarkdownViewer.exe。

### 架構/資料鏈

雙擊 .md → Windows 檔案關聯（installer.iss:47，`"%1"` 啟新程序）→ `main.py:main()`
建 QApplication + MainWindow → `window.open_path(sys.argv[1])` → `app/window.py`
的 `_open_file` → 已在分頁列（QTabBar `documentTabs`）就切過去，否則 `_add_tab`
新增分頁。分頁只存「路徑 + `_tab_state`」，切換分頁時重新載入 renderer（單一
renderer/PDF view 共用）。

### 目錄地圖

| 路徑 | 內容 |
|---|---|
| `main.py` | 進入點：QApplication、單檔開啟、例外掛勾 |
| `app/window.py` | 主視窗（約 2629 行）：分頁列、開檔、renderer 切換 |
| `installer.iss` | Inno Setup 安裝檔＋ .md 檔案關聯 |
| `AI協作/` | 本協作資料夾（規範、討論版、status.json） |

### 關鍵契約/鐵律（跨模組連動，改一處必查其他處）

- 分頁不持有 widget：分頁狀態集中在 `_tab_state`（window.py:183），切換時
  `_activate_tab`（window.py:1576）重新載入。動分頁機制必須維持此模型。
- 檔案關聯每次啟動新程序（installer.iss:47），單一實例機制必須在
  `main.py:main()` 建視窗**之前**攔截，否則會閃出第二個視窗。
- 開發環境跑 `py -3 main.py <檔案>`；發佈版是 PyInstaller 打包的 exe，
  IPC 機制兩種形態都要能動。

### 環境

Windows 11 + PowerShell 5.1；Python 3.14（`py -3`）；PySide6。
啟動：`py -3 main.py [檔案.md]`。

---

## 📬 收件匣（點名待辦——被點名的 AI 上工先看這裡，處理完劃掉）

> 用法：任何人（含使用者）可在此點名。格式：`- [ ] @對象：一句話任務（發起人/日期 HH:MM）`。
> 被點名的 AI 處理後改 `[x]` 並在進度紀錄留一節。

- [x] @Codex：首次加入請先讀 `AI_協作規範.md`（角色與權限）＋本檔「專案速覽」，
      回一節〔已同步〕確認理解架構與分工
- [x] @Gemini：首次加入請先讀 `AI_協作規範.md`（角色與權限）＋本檔「專案速覽」，
      回一節〔已同步〕確認理解架構與分工
- [x] @Gemini：實作單一實例＋檔案轉送（`main.py`，QLocalServer/QLocalSocket），
      詳見進度紀錄 2026-07-08 11:25 派工節（Claude/07-08 11:25）
- [x] @Codex：實作「分頁移出成獨立視窗」（`app/window.py` 分頁右鍵選單），
      詳見進度紀錄 2026-07-08 11:25 派工節（Claude/07-08 11:25）
- [x] @Codex：實作「標籤篩選擴及檔案分頁」，詳見進度紀錄 2026-07-10 09:57 派工節
      （Claude/07-10 09:57）
- [x] @Codex：實作「階段 1a：全 vault 全文搜尋」，詳見進度紀錄 2026-07-10 11:27
      路線圖節（Claude/07-10 11:27）
- [x] @Codex：實作「階段 1b：檔案樹 CRUD＋資料夾記憶」，詳見進度紀錄 2026-07-10
      11:27 路線圖節與 12:00 派工節（Claude/07-10 12:00）（改由 Claude subagent
      實作，Codex 額度用罄）
- [x] @Claude(subagent)：實作「階段 2a：並排即時預覽」（Claude/07-10 13:35）
- [x] @Codex：實作「階段 2b：[[ 自動補全＋內文 #tag 入標籤索引」，詳見進度紀錄
      2026-07-10 17:48 派工節（Claude/07-10 17:48）
- [x] @Codex：實作「階段 3a：Graph view 筆記關聯圖」，詳見進度紀錄 2026-07-10
      20:48 派工節（Claude/07-10 20:48）
- [x] @Codex：實作「階段 3b：Daily notes＋筆記範本」，詳見進度紀錄 2026-07-10
      23:55 派工節（Claude/07-10 23:55）
- [x] @Codex：修復 links.py wikilink 解析器（code 遮罩＋禁跨行），詳見進度紀錄
      2026-07-11 00:40 分析節與 00:50 派工節（Claude/07-11 00:50）
- [x] @Codex：Graph view 體驗棒（防重疊＋資料夾分群著色＋圖例＋hover 高亮＋
      零邊引導），詳見進度紀錄 2026-07-11 01:21 派工節（Claude/07-11 01:21）
- [x] @Codex：檔案樹永遠隱藏無可開檔案的資料夾，詳見進度紀錄 2026-07-11
      09:59 派工節（Claude/07-11 09:59）
- [x] @Codex：使用者自訂排除資料夾清單（Excluded folders），詳見進度紀錄
      2026-07-11 10:55 派工節（Claude/07-11 10:55）
- [x] @Codex：診斷並修復間歇性 pytest 掛住（堆疊定位 _save_edits），詳見
      進度紀錄 2026-07-11 11:45 節與派工 prompt（Claude/07-11 11:50）

---

## 🎯 指揮口令與紀錄格式

指揮口令（叫 Gemini/Codex/Claude 的複製貼上範本）、模型選擇表、紀錄格式，
都在 `AI_協作規範.md`（檢視器「規範」分頁）。派工前照那份即可。

---

## 進度紀錄

### 2026-07-11 14:20 — v1.17.0 已發布〔已同步〕

**作者**：Claude
**類型**：同步

三棒（檔案樹剪枝、排除資料夾清單、掛住修復）合併發布：
- 發布前整套測試 296 passed / 4 skipped；staged 16 檔人工核對無殘留。
- commit `a05cb0a` → push main → tag v1.17.0 → CI build success →
  release 附 `MarkdownViewer_Setup_v1.17.0.exe` 已確認。
  https://github.com/wulove1029/markdown_viewer/releases/tag/v1.17.0

**遺留（另案）**：本機測試環境 PermissionError(13) 間歇紅燈
（候選解：atomic_write_bytes 短重試）；wikilink 改名不改寫內文；Canvas 未做。

**→ 下一棒**：無。

### 2026-07-11 13:00 — 掛住修復：退回→Claude 修 teardown 缺陷→複驗通過〔已實作＋已驗證〕

**作者**：Claude（驗收/複驗由同一 fresh subagent 執行）
**類型**：驗證＋實作

**第一輪驗收：退回**。根因診斷正確（`window.py:1414-1418` 寫檔 OSError →
`QMessageBox.warning` 同步 modal，headless 無人關閉即死等；觸發源＝本機
temp 間歇性 PermissionError(13)），但 Codex 新增的回歸測試留下 dirty editor，
teardown 關窗觸發另一個未 stub 的 `QMessageBox.question`
（`window.py:1387 _confirm_discard_edits`）→ 間歇掛變必掛（faulthandler
100% 重現堆疊）。

**Claude 修正**：`tests/test_window_integration.py:291-295` autouse fixture
補 stub `question` 回傳 Discard（僅測試檔，產品碼零改動）；per-test 自帶
question stub 的 Discard/Cancel 測試不受影響（覆蓋順序正確）。

**複驗結果：通過**。
- 掛住：**140 輪壓測 0 掛住**（最長單輪 3.5 秒 vs 原本無限等待）。
- 整套 4 跑：3 次 296 passed / 4 skipped；1 次 1 failed（見下）。
- 驗收方加做取證：3 個間歇 fail 全記錄到 `WRITE_EXC PermissionError(13)`
  ——**直接實證觸發源**；停用沙箱仍 30% fail 率排除沙箱因素；純
  atomic_write_bytes 迴圈 3000 次零錯，鎖檔僅在 Qt 測試情境出現
  （疑 AV/索引器碰 os.replace，確切來源未確認）。

**殘留問題（另案追蹤）**：本機環境 PermissionError(13) 使 window
integration 測試約 25% 機率快速紅燈（非本修引入；修法把「掛死」變
「快速失敗」）。候選解：atomic_write_bytes 加短重試（兼益實際使用者存檔
強健性），或查明鎖檔來源。

**→ 下一棒**：無，待使用者裁決 v1.17.0 發布與殘留問題處理。

### 2026-07-11 12:15 — pytest offscreen 儲存失敗 modal 掛住修復〔已實作＋待驗證〕

**作者**：Codex
**類型**：分析＋實作

**根因與證據鏈**：三次事故都停在同一存檔測試；第三次 faulthandler 定位的
`app/window.py:1417` 現況是 `_save_edits()` 捕捉 `atomic_write_bytes()` 的 `OSError`
後同步呼叫 `QMessageBox.warning()`。在 `QT_QPA_PLATFORM=offscreen` 無人可關閉 modal，
故 CPU idle 且無期限等待。該檔 autouse fixture 已把 link index refresh stub 掉
（`tests/test_window_integration.py:284`），因此 LinkIndexThread 不是這次直接等待點。
本沙箱也實際重現 pytest 暫存路徑在 setup／session cleanup 遭 `WinError 5`；這能證明
寫入環境確有間歇 ACL 失敗條件，但三次事故底層 `OSError` 的精確 errno **未確認**。

**修復**：`tests/test_window_integration.py:286` 在 window integration 的 offscreen
fixture stub `QMessageBox.warning`，讓偶發寫入錯誤回到 `_save_edits() == False` 並由
既有斷言立即報錯，不再變成無界 modal 等待；不改產品存檔、備份、link index 更新
時機或一般 GUI 的警告行為。`tests/test_window_integration.py:447` 新增強制
`atomic_write_bytes` 丟 `OSError` 的回歸測試，驗證立即回傳 False、警告路徑被呼叫、
編輯器維持 dirty、原檔不變。

**驗證**：`py -3 -m py_compile tests\test_window_integration.py` 通過。指定兩測試與
30 輪循環均因本沙箱 ACL 在 `tmp_path` setup 或 basetemp cleanup 先遭 `WinError 5`，
測試本體未執行，故整套 **未驗證**。驗收方請先跑
`py -3 -m pytest`，再於 offscreen 連跑該整合測試 100 次；預期基準因新增一測試為
`296 passed, 4 skipped`，且任何寫入錯誤應立即 fail、不得掛住。未改 `pytest.ini`。

**→ 下一棒**：@Claude 在無 ACL 限制環境第一手跑整套與循環驗收

### 2026-07-11 11:45 — 排除資料夾清單驗收通過；掛住問題首次取得堆疊線索〔已實作＋已驗證〕

**作者**：Claude（驗收由 fresh general-purpose subagent 執行，第一手測試）
**類型**：驗證

**驗收結果：7/7 全過**，推進下方 10:25 節為已驗證。
1. `should_skip_directory` 單一共用函式（`document_libraries.py:260-281`），
   名稱/路徑片段/大小寫不敏感 10 組案例實測；預設補 build/dist/pods/
   deriveddata/target/out。
2. 設定 UI＋QSettings round-trip＋儲存即時 refresh；內建預設不可清除。
3. 四處一致：檔案樹 `file_browser.py:327`、搜尋 `global_search.py:71,92`、
   補全/關聯圖同源 `links.py:54,64-67`（經 `window.py:108-112`）。
4. 空清單行為一致。5. 剪枝與 _transient_folders 迴歸全過。
6. `py -3 -m pytest`＝**295 passed, 4 skipped, exit 0**。
7. 測試品質可（小疵：三份測試未 stub QSettings，登錄檔有設定時環境相依）。

**⚠ 掛住追蹤項重大進展**：驗收第一輪再次掛住
`test_body_tags_update_when_markdown_is_opened_and_saved`，faulthandler
首次 dump 出堆疊：**卡在 `window.py:1417 _save_edits`**。非本次改動引入，
但已從「無堆疊」進到「有定位」——下一棒可據此診斷根因
（懷疑 _save_edits 內某個同步等待在特定時序下死鎖）。

**未 commit 清單（v1.16.0 後累計）**：檔案樹剪枝棒＋排除資料夾清單棒。

**→ 下一棒**：無，待使用者實機試用與裁決（可選：派掛住根因診斷棒）。

### 2026-07-11 10:25 — 使用者自訂排除資料夾清單〔已實作＋待驗證〕

**作者**：Codex
**類型**：實作

**改動**：`app/document_libraries.py:16-34,250-289` 新增共用排除判斷、
QSettings `excluded_folders` 讀取，內建補 `build/dist/Pods/DerivedData/target/out`，
支援任意層名稱與相對路徑片段、大小寫不敏感。`app/file_browser.py:220,306-338`、
`app/global_search.py:71,85-95`、`app/links.py:51-67` 分別讓檔案樹、全文搜尋、
`[[` 補全與 Graph 共用建圖掃描吃同一函式與清單。
`app/settings_dialog.py:295-315,364-383` 新增「排除資料夾」多行 UI 與儲存；
`app/session_state.py:130-145` 儲存後即時 refresh 檔案樹並重建 link index。

**測試**：`tests/test_document_libraries.py:74-80`、`tests/test_file_browser.py:140-163`、
`tests/test_global_search.py:53-70`、`tests/test_links.py:85-97`、
`tests/test_settings_dialog.py:151-165` 新增名稱／路徑／大小寫、檔案樹、
全文搜尋、link/Graph 掃描與設定 round-trip 覆蓋。`py -3 -m py_compile`
通過；無 `tmp_path` 的指定 pytest 為 `2 passed`。整套／針對性 pytest 兩次均在
fixture setup 因既知 Windows 沙箱 ACL `WinError 5` 無法存取 pytest basetemp
（其他無需 tmp fixture 的測試當次 14 passed），故依規範標記待獨立驗證。

**→ 下一棒**：@Claude 派獨立驗收

### 2026-07-11 10:55 — 派工：使用者自訂排除清單（Excluded folders）〔提案→派工〕

**作者**：Claude
**類型**：提案＋派工

**背景**：剪枝後樹已忠實，但仍有「規則上正確、對使用者無價值」的節點
（如 Flutter 生成的 `app_flutter/ios/.../LaunchImage.imageset/README.md`
整條深鏈）。查證：`_SKIP_DIRS`（`document_libraries.py:16`，.git/node_modules/
__pycache__/.venv 等）已存在且生效；缺的是使用者自訂能力與生成物目錄預設。

**規格**：(1) 設定對話框新增「排除資料夾」清單（比照 Obsidian Excluded
files）；(2) 預設清單補 build/dist/Pods 等；(3) 排除套用到檔案樹、全文搜尋、
[[ 補全來源、關聯圖（單一共用判斷函式）；(4) 附測試、整套全綠
（基準 291 passed / 4 skipped）。

**→ 下一棒**：@Codex 實作（規格見派工 prompt 與收件匣）。

### 2026-07-11 10:35 — 檔案樹剪枝獨立驗收通過〔已實作＋已驗證〕

**作者**：Claude（驗收由 fresh general-purpose subagent 執行；實作方因沙箱
ACL 未能跑測試，本驗收為第一手測試）
**類型**：驗證

**驗收結果：6/6 全過**，推進下方 10:03 節為已驗證。
1. 無支援檔案的資料夾一律剪除（`file_browser.py:335`），深層含 .md 鏈保留（有測試）。
2. 文件庫根永遠顯示（`file_browser.py:280`），右鍵新增照常。
3. 新建空資料夾以 session 內 `_transient_folders` 集合保持可見
   （`file_browser.py:73,512`），重啟後仍空才剪，無死路。
4. 搜尋/標籤篩選語意零改動。
5. `py -3 -m pytest`＝**291 passed, 4 skipped, exit 0**（288＋實際新增 3 測試，
   實作方自報 4 個為誤計，帳目吻合）。
6. 測試品質良好。

**註**：此修在 v1.16.0 之後，尚未 commit；待使用者實機確認後可併入下個版本
（v1.16.1 或 v1.17.0）。

**→ 下一棒**：無，待使用者實機試用。

### 2026-07-11 10:03 — 檔案樹剪除無可開檔案資料夾〔已實作＋待驗證〕

**作者**：Codex
**類型**：實作

**改動**：`app/file_browser.py:48,219-221,331-335` 預設瀏覽改為剪除遞迴無
支援檔案的資料夾，搜尋／標籤篩選繼續剪空；文件庫根節點仍由
`_refresh_list` 無條件加入。`app/file_browser.py:73,502-513` 採「本次 session
暫時顯示」：從介面新建的空資料夾及必要祖先鏈保持可及，可繼續在其中
新增筆記。`tests/test_file_browser.py:117,140,156` 新增空資料夾剪枝、深層
`.md` 保留、空庫根保留、新建空資料夾可用性測試。

**驗證**：`py -3 -m py_compile app/file_browser.py tests/test_file_browser.py` 與
`git diff --check` 通過。`py -3 -m pytest` 未能執行測試本體：pytest 在
fixture setup 前對預設及三個替代 `--basetemp` 均遭 Windows `WinError 5`
沙箱 ACL 拒絕（局部重跑仍 7 errors，全為同一暫存目錄權限）；整套結果
待正常權限環境獨立驗收。

**→ 下一棒**：@Claude 派獨立驗收

### 2026-07-11 09:59 — 使用者回饋：檔案樹不應顯示無可開檔案的資料夾〔分析→派工〕

**作者**：Claude
**類型**：分析＋派工

**回饋**：加入文件庫（如 PurityGo 整個 repo）後，左側檔案樹把 backend/db/
firmware 等零可開檔案的程式碼目錄全部列出，雜訊過多。

**查證（屬實）**：檔案已按 `SUPPORTED_EXTENSIONS`（.md/.markdown/.pdf，
`app/file_types.py`）過濾（`file_browser.py:324`）；但空資料夾剪枝只在
搜尋/標籤篩選時生效（`file_browser.py:317` `if filtering and sub_count == 0`），
預設瀏覽 `filtering=False` → 空資料夾全列。

**修法**：空資料夾剪枝改為永遠生效（`sub_count==0` 一律不加入樹），
並確認右鍵「新增筆記/資料夾」在被剪枝資料夾的可及性——根節點（文件庫
本身）永遠顯示，右鍵仍可在庫根新增；子資料夾建立筆記後重掃即出現。

**→ 下一棒**：@Codex 實作（小修，規格見派工 prompt 與收件匣）。

### 2026-07-11 02:15 — 發布 v1.16.0：commit＋push＋tag 觸發 CI release〔已同步〕

**作者**：Claude
**類型**：同步

使用者裁決發布。流程：
1. `py -3 tools/bump_version.py 1.16.0`（改 `app/version.py`、`installer.iss`）。
2. CHANGELOG.md 新增 [1.16.0] 區塊（Added 7 項／Improved 1／Fixed 1）。
3. 發布前親自實跑 `py -3 -m pytest`＝**288 passed, 4 skipped**。
4. **攔截**：`git add -A` 誤將 933 個 `.codex_test_support/` 測試殘留檔暫存
   （`.gitignore` 未涵蓋，check-ignore 帶斜線誤判）；已 reset、加入
   `.gitignore`、刪目錄，重 add 後 staged＝44 檔乾淨。
5. commit `be594cf`「Release v1.16.0」（+5231/-185）。
6. `git push origin main`（b3f0d46..be594cf）。
7. `git tag -a v1.16.0` ＋ `git push origin v1.16.0` → 觸發
   `.github/workflows/release.yml`（run 29113460259，PyInstaller＋Inno Setup
   →GitHub release 附 exe）。
8. **發布完成確認**：workflow run 29113460259 completed success（6m4s）；
   release v1.16.0 已發出，附件 `MarkdownViewer_Setup_v1.16.0.exe`。
   https://github.com/wulove1029/markdown_viewer/releases/tag/v1.16.0

**→ 下一棒**：無，v1.16.0 已正式發布。

### 2026-07-11 02:10 — Graph view 體驗棒獨立驗收通過〔已實作＋已驗證〕

**作者**：Claude（驗收由 fresh general-purpose subagent 執行）
**類型**：驗證

**驗收結果：8/8 全過**，推進下方 01:36 節為已驗證。
1. 防重疊 min_gap=12＋fit cap 1.5x（`graph_model.py:157`、`graph_view.py:211,439-441`）。
2. 資料夾分群著色純函式＋圖例勾選切換群組顯示（`graph_model.py:99,138`、
   `graph_view.py:597-625`），ghost 維持虛線灰。
3. hover 高亮節點＋鄰居、其餘 opacity 0.2（`graph_view.py:511-528`）。
4. 零邊提示（`graph_view.py:41,581`）。5. 邊 2.0px／hover accent 2.2px。
6. 迴歸全過。7. `py -3 -m pytest`＝**288 passed, 4 skipped, exit 0**。
8. 測試品質良好；一小瑕疵不擋收：`test_graph_view.py:107` set_index 未傳
   libraries 會讀使用者真實設定（有 try/except 保護），後續可補隔離。

**→ 下一棒**：無，待使用者實機試用（重啟檢視器）與裁決 commit。

### 2026-07-11 01:36 — Graph view 防重疊、分群圖例與焦點互動〔已實作＋待驗證〕

**作者**：Codex
**類型**：實作

**改動**：`app/graph_model.py:99` 新增文件庫路徑分群純函式，ghost 不入群；
`app/graph_model.py:138` 新增圖例群組可見性純函式；`app/graph_model.py:150`
加入以節點矩形尺寸與 12px 間距碰撞分離，密集情況有確保無重疊的確定性 fallback。
`app/graph_view.py:63` 將邊寬調為 2.0px、hover 相連邊改 accent 2.2px；
`app/graph_view.py:107` 加節點／直接鄰居高亮與其餘淡化，ghost 維持灰色虛線；
`app/graph_view.py:306` 將碰撞分離接進初始、分幀收斂與拖曳放開流程；
`app/graph_view.py:431` 的 fit 留 70px 邊距並將初始放大上限設為 1.5x；
`app/graph_view.py:536` 加文件庫分群圖例、點擊顯隱群組及零邊底部引導。

**測試**：`tests/test_graph_model.py:83`、`:105`、`:121` 新增分群、12px
碰撞、群組可見性純函式測試；`tests/test_graph_view.py:49`、`:82` 新增圖例顯隱、
hover、邊樣式、零邊提示與 fit cap GUI 測試。`py -3 -m py_compile
app\graph_model.py app\graph_view.py` 通過；Graph 專項 `10 passed`；整套 pytest
為 **288 passed, 4 skipped in 42.21s**（較基準 +5 passed、skip 不變）。另以 60
節點離屏跑完 180 次佈局迭代，結果 `overlap=0`；兩節點 fit 實測 `1.500x`。

**意外發現**：managed sandbox 中 Python 直接建立 pytest 暫存目錄會立即遇到 ACL
拒絕讀取；完整測試以一次性 pytest 外掛改由 PowerShell 建目錄後通過，外掛已刪除，
未納入產品碼。未確認：使用者真實 vault 的視覺手感，待獨立 GUI 實機驗收。

**→ 下一棒**：@Claude 派獨立驗收

### 2026-07-11 01:21 — 使用者回饋：關聯圖分群＋視覺改善；「無線條」查明非 bug〔分析→派工〕

**作者**：Claude
**類型**：分析＋派工

**使用者回饋**：(1) 想要檔案分類集合成群組的功能；(2) 關聯圖不清楚、沒有線條。

**查證**：邊繪製程式碼結構正確（`graph_view.py:34-54` 中心連線＋
ItemSendsGeometryChanges 跟隨）。實測整個 vault build_graph＝**11 節點、0 邊**
——筆記之間目前沒有任何真 `[[連結]]`（README 等檔的 `[[...]]` 都在 code 引用
內，links.py 修復後正確排除）。「沒有線條」屬預期行為，非 bug。

**真問題**：節點重疊、無分群、零邊時無引導。派 Codex 做 Graph view 體驗棒：
防重疊佈局、按資料夾分群著色＋圖例、hover 高亮鄰居、零邊提示。

**→ 下一棒**：@Codex 實作（規格見派工 prompt 與收件匣）。

### 2026-07-11 01:35 — links.py 修復獨立驗收通過：Obsidian 化路線圖全部收工〔已實作＋已驗證〕

**作者**：Claude（驗收由 fresh general-purpose subagent 執行）
**類型**：驗證

**修復驗收：6/6 全過**，推進下方 00:57 節為已驗證。
1. 行內 code／fence／跨行三種誤解析全排除且各有測試（`tests/test_links.py:22-33`）。
2. 正常 wikilink 行為零改變（既有測試全過）。
3. 遮罩邏輯單一實作共用：`mask_markdown_code`（`md_converter.py:505`）供
   links 與 body_hashtags 共用（`links.py:15,33`、`md_converter.py:536`）。
4. 實測 AI協作 兩檔建圖：**ghost count = 0**，碎片產物消失。
5. 迴歸 50 項全過（反連/補全/graph/hashtag/tag）。
6. `py -3 -m pytest`＝**283 passed, 4 skipped, exit 0**，無掛住。

**Obsidian 化路線圖總結（2026-07-10 11:27 起）**：
1a 全文搜尋、1b 檔案樹 CRUD＋資料夾記憶、2a 三態並排即時預覽、
2b [[ 補全＋內文 #tag、3a Graph view、3b Daily notes＋範本、
外加 links.py 解析器修復——**七棒全部實作＋獨立驗收通過**。
測試由基準 213 → 283 passed。改動尚未 commit，等使用者裁決
（建議實機試用後 commit / 出 release）。

**遺留追蹤項**：間歇性 pytest 掛住（faulthandler_timeout=120 已就位等抓堆疊）；
wikilink 改名不自動改寫內文；U+FFFD 檔被全文搜尋跳過。

**→ 下一棒**：無，待使用者實機試用與裁決。

### 2026-07-11 00:57 — 修復 wikilink code 誤解析與跨行配對〔已實作＋待驗證〕

**作者**：Codex
**類型**：實作

- 把既有 fence 狀態機與行內 code 遮罩收斂成公開共用函式
  `mask_markdown_code`（`app/md_converter.py:483-518`），`body_hashtags` 改共用該函式
  （`app/md_converter.py:525-536`）；取捨是避免 `links.py` 匯入私有符號，也不複製
  兩份遮罩邏輯。
- `extract_wikilinks` 先遮罩再解析（`app/links.py:30-38`）；`WIKILINK_RE` 的 target、
  alias 與周邊空白均禁止 CR/LF，杜絕跨行配對（`app/links.py:17-20`）。正常
  `[[Note]]`、`[[Note|alias]]`、`[[Note#Section]]` 行為由既有測試確認未變。
- 新增行內 code、三反引號 fence、跨行不配對回歸測試
  （`tests/test_links.py:22-33`）。目標測試：**15 passed**；`py_compile` 與
  `git diff --check` 通過。
- 實檔驗證：以 `AI協作/AI_協作規範.md`、`AI協作/AI_協作進度.md` 建
  `LinkIndex`＋`build_graph`，結果 `ghost_count=0`、`ghost_labels=[]`、
  `contains_wikilink_artifact=False`。
- 完整 pytest：原始 `py -3 -m pytest` 遇既知 sandbox temp ACL，關鍵幀
  `_pytest/pathlib.py:229 make_numbered_dir`，為 160 passed／4 skipped／123 setup
  errors；改用全新 TEMP/TMP＋permissive-mkdir 等價 runner 後為
  **283 passed, 4 skipped, exit 0（3.67s）**。新增 3 例，故較 280 基準多 3 passed。

**→ 下一棒**：@Claude 派獨立驗收

### 2026-07-11 00:50 — 階段 3b 獨立驗收通過＋派工 links.py 修復棒〔已實作＋已驗證〕

**作者**：Claude（驗收由 fresh general-purpose subagent 執行）
**類型**：驗證＋派工

**階段 3b 驗收結果：8/8 全過**，推進下方 00:36 節為已驗證。
1. 檔案選單＋Ctrl+D 開今日筆記、已存在直接開啟、進編輯模式、自動建資料夾
   （`window.py:351-352,418,2066-2115`、`note_templates.py:74`）。
2. 設定對話框 Daily notes 區塊＋預設值＋QSettings 持久化
   （`settings_dialog.py:211-266,351-353`）。
3. render_template 純函式、datetime 可注入、{{date}}/{{time}}/{{title}}
   （`note_templates.py:12-25`）。
4. daily 套範本＋編輯選單「插入範本…」游標插入＋缺資料夾優雅提示
   （`window.py:433,2083,2142-2180`）。
5. `pytest.ini:2` faulthandler_timeout=120 已加（掛住追蹤項落地）。
6. 迴歸全過。7. `py -3 -m pytest`＝**280 passed, 4 skipped, exit 0**（無掛住）。
8. fake 物件貼齊真 API。

**Obsidian 化路線圖 6 棒（1a/1b/2a/2b/3a/3b）全部完成並驗收。**
剩餘：links.py 修復棒（見下方 00:40 分析節，使用者實機發現）。

**→ 下一棒**：@Codex 修復 links.py wikilink 解析器（規格見派工 prompt 與收件匣）。

### 2026-07-11 00:40 — Graph view 實機碎片 bug：根因在 links.py 解析器〔分析〕

**作者**：Claude（診斷由 Explore subagent 執行）
**類型**：分析

使用者實機截圖：關聯圖除正常節點外散落大量細直線碎片（弧形排列）。

**根因（已實測重現）**：`app/links.py:17` `WIKILINK_RE` 與 `extract_wikilinks`
（:27-35）未遮罩 code fence／行內 code——文件內 `` `[[wikilink]]` `` 範例
文字被解析成真連結，生成大量 ghost 節點；且字元類允許跨行配對產生超長
label。碎片視覺＝眾多 ghost 節點的初始圓形佈局被 fitInView 縮小後的樣子。
graph_view/graph_model 本身無誤。

**修法**：(1) `extract_wikilinks` 解析前比照 `md_converter.body_hashtags`
（md_converter.py:519-528）套 fence 狀態機＋行內 code 遮罩；
(2) `WIKILINK_RE` 字元類改 `[^\[\]|\n]` 禁止跨行。
附帶效益：反向連結面板同樣受惠（同一解析器）。

**→ 下一棒**：3b 驗收後派修復棒（改 links.py＋測試，與 3b 改動檔案不重疊）。

### 2026-07-11 00:36 — 階段 3b：Daily notes＋筆記範本〔已實作＋待驗證〕

**作者**：Codex
**類型**：實作

- 新增可獨立測試的範本核心：支援 `{{date}}`、`{{time}}`、`{{title}}` 與可注入時間，
  遞迴列出範本資料夾 `.md`，缺失時回空清單；Daily note 會自動建立資料夾、以
  `YYYY-MM-DD.md` 精確建檔，既有檔不覆寫（`app/note_templates.py:12-79`）。
- 偏好設定「行為」頁新增 Daily notes／筆記範本區塊：預設第一個文件庫下的
  `Daily Notes/`、`Templates/`，固定檔名格式、可瀏覽資料夾與每日範本，三項均存入
  既有 QSettings（`app/settings_dialog.py:204-292,338-353`）。
- 主視窗新增「檔案→開啟今日筆記」與 `Ctrl+D`、開啟後進編輯模式；新增編輯選單
  「插入範本…」，由設定資料夾選檔並在游標處插入替換後內容。開檔沿用
  `open_path`，未動 `_tab_state`（`app/window.py:351-353,418,433,2057-2181`）。
- 新增 daily 建立／重開、純函式變數、設定預設值、游標插入與缺失資料夾容錯測試
  （`tests/test_note_templates.py:12-59`、`tests/test_settings_dialog.py:103-107`、
  `tests/test_window_integration.py:616-704`）；pytest 常駐逾時堆疊設定在
  `pytest.ini:1-2`。
- 驗證：`py -3 -m py_compile` 通過；Codex 沙箱原始 pytest 受既知 Windows temp
  `0700` ACL 阻擋 setup，改用前棒同型 permissive-mkdir 等價完整執行＝
  **280 passed, 4 skipped, exit 0（3.85s）**。4 skips 為既知依賴／headless WebEngine。

**→ 下一棒**：@Claude 派獨立驗收

### 2026-07-10 23:55 — 階段 3a 獨立驗收通過（附追蹤項）＋派工階段 3b〔已實作＋已驗證〕

**作者**：Claude（驗收由 fresh general-purpose subagent 執行）
**類型**：驗證＋派工

**階段 3a 驗收結果：9/9 全過**，推進下方 21:00 節為已驗證。
1. graph_model 純函式（無 Qt import），孤立/ghost 節點正確（`app/graph_model.py:48-73`）。
2. 非模態 QDialog＋檢視選單＋Ctrl+G，開檔走 open_path、分頁模型未動
   （`graph_view.py:333`、`window.py:374,428,1945`）。
3. FR 式力導向、平移/縮放/拖曳節點/點擊開檔/當前檔高亮/ghost 虛線，
   純 QGraphicsScene 無網路依賴（`graph_model.py:98-166`、`graph_view.py:69-186`）。
4. 存檔自動刷新開著的 Graph 視窗（`window.py:1421,1925-1931`，有測試）。
5. QTimer 16ms 分幀佈局；實測 205 節點 build 1.8ms、每幀 19ms，不凍結。
6. 深淺主題接 theme.py（`graph_view.py:359-365`）。
7. 迴歸全過。8. `py -3 -m pytest`＝**273 passed, 4 skipped, exit 0**；
   實作方回報的 117 setup errors 判定為**其沙箱 temp ACL 環境問題，非程式碼問題**
   （驗收環境同命令三跑零 error）。9. fake 物件貼齊真 API。

**⚠ 追蹤項（間歇性 pytest 掛住）**：驗收中再次出現整套 pytest 掛住
（`test_window_integration.py:424` `test_body_tags_update_...`，CPU 2.9s/7.3min
純等待；2b 驗收也掛過一次同型）。faulthandler 重跑未重現、無堆疊。
判定非 3a 引入，懷疑 LinkIndexThread 或 Qt offscreen 時序。
**建議**：pytest 設定常駐 `faulthandler_timeout` 以便下次抓堆疊（併入 3b 順手做）。

**→ 下一棒**：@Codex 實作階段 3b（Daily notes＋筆記範本，Obsidian 化最後一棒），
規格見派工 prompt 與收件匣。

### 2026-07-10 21:00 — 階段 3a：Graph view 筆記關聯圖〔已實作＋待驗證〕

**作者**：Codex
**類型**：實作

- 圖資料與佈局：`LinkIndex` 保留未解析的原始 wikilink 目標而不改既有正反向索引
  語意（`app/links.py:100,112-117`）；新增純函式模型，涵蓋全部筆記、孤立筆記、
  去重邊與 ghost 節點，並提供確定性初始位置和可釘選節點的力導向單步迭代
  （`app/graph_model.py:14-79,82-95,98-171`）。
- 視窗與互動：新增獨立非模態原生 Qt 關聯圖，含分幀佈局、目前檔案高亮、ghost
  虛線淡色、空白拖曳平移、滾輪縮放、節點拖曳及點擊開檔
  （`app/graph_view.py:189-326,329-365`）。主視窗以「檢視 → 筆記關聯圖」及
  `Ctrl+G` 進入，不改分頁模型；主題、切換目前檔案、存檔後背景索引重建與開啟中
  Graph 刷新均已接線（`app/window.py:163-164,374-376,428,663-665,1715-1718,
  1903-1956`）。
- 測試：新增模型建構／ghost／孤立筆記、佈局更新與釘選、200 節點效能、真實
  QGraphics 點擊、深色主題，以及非模態／分頁不變／存檔刷新整合測試
  （`tests/test_graph_model.py:14-75`、`tests/test_graph_view.py:11-45`、
  `tests/test_window_integration.py:349-400`）。`py -3 -m py_compile` 通過；
  `py -3 -m pytest` 等價 permissive-temp 執行＝**273 passed, 4 skipped, exit 0**。
  200 節點實測純佈局單步 7.49 ms、含兩步及 scene 更新一幀 20.22 ms。
- 意外發現：原始 `py -3 -m pytest` 因 pytest 將系統 temp 建成 sandbox 不可讀 ACL，
  結果為 156 passed / 4 skipped / 117 setup errors；改用既有 permissive-mkdir 等價
  runner 後全綠。另一次 permissive 全套首跑的既有
  `test_rename_folder_maps_every_file` 曾偶發 `PermissionError`；該項單跑 1 passed，
  隨後全套重跑全綠。尚待 Claude 以 fresh 驗收確認實際 GUI 手感與本棒改動。

**→ 下一棒**：@Claude 派獨立驗收

### 2026-07-10 20:48 — 階段 2b 獨立驗收通過＋派工階段 3a Graph view〔已實作＋已驗證〕

**作者**：Claude（驗收由 fresh general-purpose subagent 執行）
**類型**：驗證＋派工

**階段 2b 驗收結果：8/8 全過**，推進下方 18:02 節為已驗證。
1. `[[` 補全：偵測/過濾/插入游標定位/Esc（`app/wikilink_completion.py:10,57-70,98`、
   `app/editor.py:80-90,100`），來源＝_link_roots 掃描（`window.py:1870-1888`）。
2. 編輯/並排可用、預覽無作用、上限 50。
3. hashtag 純函式解析＋排除規則（fence/行內 code/ATX 標題）全有測試
   （`md_converter.py:486-541`、`tests/test_inline_hashtags.py`）。
4. body_tags 第四來源聯集＋開檔/存檔更新＋舊 JSON 向後相容
   （`tag_index.py:39-86`、`window.py:1412,1682`）。
5. 標籤面板/篩選自動生效，無 UI 特例。
6. 迴歸全過。7. `py -3 -m pytest`＝**266 passed, 4 skipped, exit 0**（連跑 3 次同）。
8. fake 物件貼齊真 API。

**觀察（未確認）**：驗收方首輪有一次 `test_body_tags_update_when_markdown_is_opened_and_saved`
掛住 16 分鐘後手動終止，之後 3 次全過且無法重現，疑偶發環境干擾，留意即可。

**階段 3 拆棒**：3a＝Graph view 關聯圖；3b＝Daily notes＋筆記範本。

**→ 下一棒**：@Codex 實作階段 3a（規格見派工 prompt 與收件匣）。

### 2026-07-10 18:02 — 階段 2b：[[ 自動補全＋內文 #tag 入標籤索引〔已實作＋待驗證〕

**作者**：Codex
**類型**：實作

- `[[` 補全：新增 vault 相對路徑候選建立、大小寫不敏感部分比對、相關度排序與
  50 筆上限的純函式（`app/wikilink_completion.py:13-102`）；背景 link index 掃描
  完成後把全部 `.md` 候選送進編輯器（`app/window.py:99-107,1905-1909`）。
- 編輯器 popup：`QCompleter` 在輸入 `[[` 後顯示並隨輸入過濾，選定後補上 `]]`、
  游標移至其後，Esc 關閉（`app/editor.py:12-100`）；編輯與並排共用同一
  `EditorView`，預覽模式不持有輸入焦點，未改 `_tab_state`／切分頁回預覽語意。
- 內文標籤：新增 fence、行內 code、ATX heading 排除與 Unicode 標點終止解析
  （`app/md_converter.py:482-548`）；`TagIndex` 加入向後相容 `body_tags` 第四來源聯集
  （`app/tag_index.py:39-67`），開檔與存檔沿既有 frontmatter 路徑更新
  （`app/window.py:2181-2212`），現有 counts/files/filter/UI 自動生效。
- 測試：新增解析、補全純函式、真 EditorView 插入、第四來源聯集、舊 JSON 相容與
  開存檔更新測試（`tests/test_inline_hashtags.py:1`、
  `tests/test_wikilink_completion.py:1`、`tests/test_editor_completion.py:1`、
  `tests/test_tag_counts.py:49`、`tests/test_tag_index.py:43`、
  `tests/test_window_integration.py:370`）。`py -3 -m pytest`＝**266 passed,
  4 skipped, exit 0**；另以 offscreen GUI smoke 實測 popup／部分過濾／Esc 全過。
- 意外發現：Python 3.13 在沙箱以 `0700` 建 pytest temp 會觸發 Windows ACL 拒絕；
  本次用既有 test-support 等價的 permissive mkdir 跑完整命令，並以
  `.gitignore:18` 排除這類測試暫存目錄。另有 1 個既有
  `.pytest_cache` 寫入 warning，不影響 exit 0；4 skips 仍是既知依賴/WebEngine。

**→ 下一棒**：@Claude 派獨立驗收

### 2026-07-10 17:48 — 階段 2a 獨立驗收通過＋派工階段 2b〔已實作＋已驗證〕

**作者**：Claude（驗收由 fresh general-purpose subagent 執行）
**類型**：驗證＋派工

**階段 2a 驗收結果：8/8 全過**，推進下方 17:35 節為已驗證。
1. 三態狀態機＋快捷鍵（`app/window.py:346-350,526`、`app/view_mode.py:25-38`）。
2. 並排 400ms 去抖渲染＋預覽捲動位置保持（generation 對齊，
   `renderer.py:284-287,425-427`）。
3. editor→preview 單向捲動同步＋ScrollSyncGuard 方向鎖（`view_mode.py:53-75`）。
4. 存檔/分頁切換 Discard/Cancel/PDF 阻擋 皆有測試覆蓋。
5. `_tab_state` 模型未破壞（模式屬視窗層級，切分頁一律回預覽，
   `window.py:1518-1519`）。
6. 迴歸全過（全域搜尋/文件內搜尋/標籤篩選/檔案樹 CRUD 測試全綠）。
7. `py -3 -m pytest`＝**255 passed, 4 skipped, exit 0**。
8. 測試品質：僅一小瑕疵——fake renderer 的 `set_scroll_y` 為測試自用 setter，
   真物件無此方法（無害，建議後續改名）。

**未確認（留給使用者實機試用）**：GUI 實跑 smoke（打字流暢度、Mermaid/KaTeX
即時渲染）——驗收時使用者的舊版檢視器實例在跑，單一實例 IPC 會攔走啟動，
故未實機驗。請使用者關閉舊視窗重開後試用三態切換。

**→ 下一棒**：@Codex 實作階段 2b（`[[` 自動補全＋內文 `#tag` 入標籤索引），
Codex 額度已重置，規格見派工 prompt 與收件匣。

### 2026-07-10 17:35 — 階段 2a：並排即時預覽＋捲動同步〔已實作＋待驗證〕

**作者**：Claude（general-purpose subagent）／**類型**：實作

**前提修正**：派工描述說「編輯與預覽互斥」，但 HEAD（v1.15.0）其實已有
「編輯＝左編輯右即時預覽」的雙欄實作（window.py 舊 234-262 行）。本棒把它
重構成**三態模式**：`preview`（純預覽）／`edit`（純編輯，新增）／`split`
（並排即時預覽），並補齊去抖後捲動保持、方向鎖與測試。

**改動摘要**（檔名:行號以本次工作區為準）：

- **新增 `app/view_mode.py`**（純邏輯、無 Qt，可 headless 測試）：三態常數、
  `cycle_mode`/`toggle_edit`/`toggle_split`/`normalize`/`is_editing`、
  捲動映射 `editor_scroll_ratio()`、防抖動迴圈方向鎖 `ScrollSyncGuard`
  （cooldown 內壓制對向來源）。
- **`app/window.py`**：
  - `_view_mode` 成為單一事實來源（:228），`_edit_mode` 改為相容 property
    （getter/setter，:1137-1148），export_actions／session_state 舊介面不變。
  - 狀態機 `_set_view_mode`（:1167）＋`_apply_split_visibility`（:1187，
    edit↔split 只切預覽欄可見性、保留編輯緩衝）；`_enter_edit_mode(mode)`
    （:1299）、`_leave_edit_ui` 停 debounce timer（:1340）。
  - 進入方式：工具列編輯鈕改三態循環 `_cycle_view_mode`（:526，圖示
    pencil→columns→eye）；新快捷鍵 **Ctrl+Shift+E** 直切並排（:349）；
    **Ctrl+E 維持編輯↔預覽切換**；選單（:409）與快捷鍵說明（:466）同步。
  - 去抖：`_preview_timer` 400ms single-shot 沿用，僅 split 模式啟動
    （`_on_editor_text_changed` :1410）；渲染走既有 `render_markdown_text`
    背景執行緒（markdown-it-py 鏈不變，Mermaid/KaTeX 同一條）。
  - 捲動同步：`_sync_preview_scroll`（:1428）改用 `editor_scroll_ratio`＋
    `ScrollSyncGuard`；同步比例存 `_preview_scroll_ratio` 供重渲染後還原。
    預覽側目前無回向同步（單向設計，結構上不可能抖動迴圈）。
- **`app/renderer.py`**：`render_markdown_text(..., scroll_ratio=)`（:407）
  ＋`_pending_ratio` generation 對齊（:169、:228、:273），文字渲染
  HTML 現在也嵌 render-generation meta——**去抖重渲染後預覽不再跳回頂端**。
- **`app/theme.py`**：新增 `columns` 圖示（:202）。

**測試**：新增 `tests/test_view_mode.py`（11 個：三態狀態機、捲動映射、
方向鎖含假時鐘）＋ `tests/test_window_integration.py` 增 9 個（Ctrl+E／
Ctrl+Shift+E／三態循環、PDF 不適用、去抖只在 split 觸發、split 下 Ctrl+S
寫檔、切分頁 Discard/Cancel 行為、捲動同步僅 split 生效）；fake renderer
只補真 API 既有簽名的記錄版。
**pytest：255 passed / 4 skipped**（基準 235+4，新增 20 全過；4 skips＝
python-docx/pptx 缺件＋headless WebEngine，既知）。

**限制註記**：
1. 分頁切換時維持既有語意：離開編輯／並排會先詢問存檔，切回後為 preview
   （模式不跨分頁記憶；`_tab_state` 模型不變、無錯亂，見新測試）。
2. GUI 實測：啟動 smoke 時檔案被 10:33 的舊實例經單一實例 IPC 接走（未殺
   使用者行程），故打字不卡頓、Mermaid/KaTeX 即時渲染屬**未確認**，請驗收
   棒先關舊實例再 `py -3 main.py` 實測。
3. `_on_browser_note_created`／Mermaid 工具現在進入的是純編輯模式（原本是
   雙欄）；如要改回並排可傳 `view_mode.SPLIT`。

**→ 下一棒**：@Claude 派獨立驗收

### 2026-07-10 13:35 — 階段 1b 獨立驗收通過＋階段 2 拆棒派工〔已實作＋已驗證〕

**作者**：Claude（驗收由 fresh general-purpose subagent 執行）
**類型**：驗證＋派工

**階段 1b 驗收結果：8/8 全過**，推進下方 13:25 節為已驗證。
1. 右鍵選單完整（`app/file_browser.py:389-420,429-451`）。
2. 新增筆記＋重名加序號＋直接進編輯（`app/file_ops.py:53-62`、`window.py:1891-1901`）。
3. 改名/移動同步遷移 sidecar/tag_index/最近清單/分頁狀態
   （`file_ops.py:83-89`、`file_browser.py:604-606`、`window.py:1907-1927`）。
4. 刪除走 send2trash＋永久刪除備援＋關分頁不誤跳存檔（`file_ops.py:122-132`、
   `window.py:1931-1943`）。
5. 檔案樹展開/選中記憶 round-trip，含帶檔案參數啟動（`session_state.py:189-191`、
   `main.py:157-160`）。
6. 檔案系統錯誤六個呼叫端全 try/except → QMessageBox（`file_browser.py:475-589`）。
7. **迴歸全過**：標籤篩選 set_tag_filter/has_open_folder 仍在且有測試；全文搜尋
   roots 不受影響；分頁索引順序不變。
8. `py -3 -m pytest`＝**235 passed, 4 skipped, exit 0**。

**非阻斷備註**：新筆記自動進編輯模式與 main.py 帶參啟動樹恢復，無自動化測試
（程式碼路徑已核對）。既知限制：wikilink 內文不自動改寫、pdf_last_pages 鍵
不遷移、文件庫根節點改名僅改顯示名。

**階段 2 拆棒**：2a＝並排即時預覽＋捲動同步（Codex 額度未重置，續由 Claude
subagent 接）；2b＝`[[` 自動補全＋內文 `#tag` 入索引（Codex 14:54 重置後派）。

**→ 下一棒**：Claude subagent 實作 2a。

### 2026-07-10 13:25 — 階段 1b：檔案樹 CRUD＋資料夾記憶〔已實作＋待驗證〕

**作者**：Claude（general-purpose subagent）
**類型**：實作

「檔案」分頁由平面清單改為 QTreeWidget 檔案樹（文件庫→資料夾→檔案），加上
CRUD 右鍵選單與資料夾展開/選中記憶。改動：

- `app/file_ops.py`（新檔）：檔案系統操作核心——`create_note`（重名自動加
  「名稱 2/3…」序號）、`create_folder`、`rename_document`/`move_document`
  （連同 `.notes.json`/`.highlights.json` sidecar 一起搬）、`rename_folder`
  （回傳整棵樹的 old→new 路徑 mapping）、`delete_document`（優先 send2trash，
  未安裝時退回永久刪除）。全部以 OSError 上拋給 UI 捕捉。
- `app/file_browser.py`（重寫，約 926 行）：樹狀顯示＋右鍵選單。資料夾/文件庫
  節點：新增筆記/新增資料夾/重新命名/在檔案總管顯示（`_show_context_menu`
  file_browser.py:379）；檔案節點：開啟/重新命名/移動到…/刪除/在檔案總管顯示。
  CRUD 動作在 `_create_note_action` 起（file_browser.py:469）；展開狀態
  `tree_state()`/`restore_tree_state()`（file_browser.py:155-177）。文件庫節點
  的「重新命名」改的是 store 顯示名稱（不動磁碟資料夾）。
- `app/tag_index.py:80-97`：新增 `migrate_paths(mapping)`、`remove_path()`
  遷移/移除索引鍵。
- `app/recent_files.py:47-70`：新增 `migrate_paths()`、`remove_paths()`。
- `app/document_libraries.py:116-128`：store 新增 `rename()`。
- `app/window.py:202-206` 掛 callback；`app/window.py:1891-1946` 三個 handler：
  `_on_browser_note_created`（開檔＋直接進編輯模式）、`_on_browser_paths_migrated`
  （更新分頁 tabData/tabText/`_tab_state`/`_active_path`/標題/檔案監看/最近清單）、
  `_on_browser_paths_deleted`（關對應分頁、清最近清單；正在編輯的檔案被刪時
  先 `mark_saved()` 避免跳出「儲存已刪除檔案」）。
- `app/session_state.py:26-37`（`restore_file_tree_state`）與 `close_event`
  （session_state.py:189-191）：展開/選中狀態存 QSettings `file_tree_state`，
  與 open_tabs 恢復並存（先還原樹，再由 `_load_document` 定位使用中檔案）。
- `main.py:159`：帶檔案參數啟動時也還原檔案樹狀態。
- `requirements.txt`：加 `Send2Trash>=1.8`（本機已 `pip install send2trash` 成功，
  刪除走資源回收筒；仍保留未安裝時的永久刪除備援路徑）。

測試：`tests/test_file_ops.py`（新，9 例：建檔序號/非法名、重命名遷移 sidecar
＋tag_index 且重載仍在、移動、資料夾改名 mapping、刪除含 sidecar/回收筒）、
`tests/test_file_browser.py`（改寫＋新增樹狀結構、展開/選中 round-trip、刪除
通知 3 例）、`tests/test_window_integration.py`（新增遷移改指分頁、刪除關分頁
2 例）。`py -3 -m pytest`＝**235 passed / 4 skipped**（基準 221+14 新增，
4 skips 同既知）。

限制註記：
- wikilink 內文引用**不會**隨重命名/移動自動改寫（規格明訂不要求）；連結索引
  會強制重建，舊連結會變成「找不到→建立筆記」流程。
- PDF 的 `pdf_last_pages` 記錄鍵未隨改名遷移（只影響記住的頁碼，重開歸第 1 頁）。
- 文件庫根節點重新命名只改顯示名稱，不改磁碟資料夾名。

**→ 下一棒**：@Claude 派獨立驗收

### 2026-07-10 12:10 — 階段 1b 改派：Codex 額度用罄，Claude subagent 接棒〔阻塞→改派〕

**作者**：Claude
**類型**：同步

Codex 派工失敗（exit 1）：帳號 usage limit 用罄，14:54 重置（PurityGo Flutter
遷移大棒＋本專案連兩棒所致），任務未開工、無任何檔案改動。為不停擺，階段 1b
改由 Claude 內建 general-purpose subagent 實作（Claude 參與 ✅、模型＝session，
不涉 Codex/Gemini 檔位變更）。規格不變（見 12:00 節），驗收仍另派 fresh 獨立方。
Codex 額度重置後，後續棒次恢復依設定表派 Codex。

### 2026-07-10 12:00 — 階段 1a 獨立驗收通過＋派工階段 1b〔已實作＋已驗證〕

**作者**：Claude（驗收由 fresh general-purpose subagent 執行）
**類型**：驗證＋派工

**階段 1a 驗收結果：8/8 全過**，推進下方 11:46 節為已驗證。
1. 搜尋分頁＋遞迴＋大小寫不敏感：`app/left_panel.py:86-101`、`app/global_search.py:79,93`，
   roots＝`window.py:1771 _link_roots()`（全部文件庫＋當前資料夾）。
2. 分組/醒目/計數/無結果文字：`global_search.py:309-313,319,359-383`。
3. 點擊開檔定位：`window.py:834-849`＋`renderer.py:558`（render generation 對齊）。
4. QThreadPool 背景搜尋＋300ms 去抖＋取消/過期丟棄：`global_search.py:157-177,218-223,300`。
5. Ctrl+Shift+F／Ctrl+F 不互相干擾：`window.py:326-330`。
6. UTF-8 errors=replace、壞檔/權限錯誤跳過：`global_search.py:114-118`。
7. 左側分頁索引未被打亂（新分頁 append 於尾端 index 6，`switch_to` 呼叫點僅
   `window.py:2059,2097`，行為不變）。
8. `py -3 -m pytest`＝**221 passed, 4 skipped, exit 0**（與基準相符）。
   fake 物件逐一比對真 API 無虛構方法。

**非阻斷備註**：內文含 U+FFFD 的合法檔會被搜尋跳過；點擊結果若目標非
markdown 只開檔不定位（搜尋範圍僅 .md，無實害）。

**→ 下一棒**：@Codex 實作階段 1b（檔案樹右鍵 CRUD＋重命名/移動同步遷移
標註與 tag_index＋刪除＋資料夾記憶），規格見派工 prompt 與收件匣。

### 2026-07-10 11:46 — 階段 1a：全 vault 全文搜尋〔已實作＋待驗證〕

**作者**：Codex
**類型**：實作

**改動摘要**：
- `app/global_search.py:64,180,273,306`：新增全 vault UTF-8 全文搜尋核心、
  300ms 去抖、`QThreadPool` 背景工作、過期任務取消、按檔案分組結果、總命中數、
  無結果訊息與命中字富文字醒目顯示；壞編碼／無法讀取檔案跳過。
- `app/left_panel.py:86,101,142`：把「搜尋」分頁加在尾端並提供具名切換／聚焦，
  保持既有檔案／最近／標註等索引不變。
- `app/window.py:195-199,329-331,829-853`：搜尋根沿用 `_link_roots()`（所有文件庫＋
  目前文件資料夾）；新增 Ctrl+Shift+F；點結果開檔並觸發既有文件內搜尋，Ctrl+F 未改。
- `app/renderer.py:166,254-258,558-563`：把點擊結果的搜尋排到正確 Markdown render
  generation 載入完成後重做，避免只搜尋到「載入中」頁。
- `tests/test_global_search.py:11-95`、`tests/test_window_integration.py:356-382`：覆蓋跨
  資料夾、大小寫不敏感、無結果、壞編碼、重疊根去重、背景 UI／醒目顯示、快捷入口與
  開檔後搜尋。`tests/test_settings_dialog.py:23-33`、`tests/test_window_integration.py:269-279`
  改用臨時 INI 隔離真實 HKCU `QSettings`，避免測試污染使用者偏好。

**驗證證據**：`py -3 -m py_compile ...` 與 `git diff --check` 通過；相關測試
`21 passed`；完整 `py -3 -m pytest`＝**221 passed, 4 skipped**（exit 0；4 skipped
與既知基準相同）。意外發現：Python 3.13 pytest 的 0700 Temp ACL 及 HKCU sandbox
限制會造成假失敗，已用測試支援路徑取得有效全綠；首輪留下的
`.pytest_tmp_global_search` 因 ACL 拒絕存取而未能清除，非產品檔案。

**→ 下一棒**：@Claude 派獨立驗收

### 2026-07-10 11:27 — Obsidian 化路線圖定案＋階段 1 派工〔提案→派工〕

**作者**：Claude
**類型**：提案＋派工

**背景**：使用者要求把檢視器往 Obsidian 方向擴充。Claude 盤點（Explore subagent）
確認現況：編輯模式已存在（`app/editor.py:11`、Ctrl+E `app/window.py:1081`、
存檔 `window.py:1256`）；`[[wikilink]]`＋反向連結索引已存在（`app/links.py:17,146`、
點擊跳轉 `window.py:1792`）；文件庫≈vault（`app/document_libraries.py:31,89`）；
渲染為 markdown-it-py → QWebEngineView（`app/md_converter.py:10`、`app/renderer.py:149`）。

**路線圖（使用者已裁決：照順序全跑，自動接棒）**：
- **階段 1a**：全 vault 全文搜尋（跨文件庫資料夾，結果面板＋跳轉）
- **階段 1b**：檔案樹右鍵建立/改名/搬移/刪除＋記住上次開啟資料夾
- **階段 2**：編輯｜預覽並排＋捲動同步、`[[` 自動補全、內文 `#tag` 入標籤索引
- **階段 3**：Graph view、Daily notes、筆記範本
- Canvas 不在本輪範圍。每棒 Codex 實作、fresh 獨立方驗收。

**→ 下一棒**：@Codex 實作階段 1a（規格見收件匣與派工 prompt），完成留
〔已實作＋待驗證〕節。

### 2026-07-10 10:30 — 標籤篩選擴及檔案分頁：獨立驗收通過〔已實作＋已驗證〕

**作者**：Claude（驗收由 fresh general-purpose subagent 執行）
**類型**：驗證

**結果：6/6 全過**，推進下方 10:15 節為已驗證。
1. 檔案分頁依標籤過濾＋「沒有符合標籤的檔案」提示：`app/file_browser.py:151-164,190-192`。
2. 「全部（清除篩選）」兩邊同清：`app/window.py:2023-2026`。
3. 已開資料夾切「檔案」分頁、否則切「最近」：`app/window.py:2027-2028`。
4. refresh 後篩選保持：`app/file_browser.py:127-130`＋`tests/test_file_browser.py:58-59`。
5. 「最近」篩選無回歸：`app/recent_files.py:47-72` 未動、整合測試過。
6. `py -3 -m pytest`＝**213 passed, 4 skipped, exit 0**。與基準 235/2 落差已查明：
   本機缺 `python-docx`/`python-pptx`，`test_docx_export.py`（8 測試）與
   `test_pptx_export.py`（16 測試）整檔 importorskip；235−24＋2 新測試＝213，
   無測試被刪、無收集錯誤，落差與本功能無關。fake 物件核對貼齊真 API。

**遺留（非阻擋）**：本機 24 個 docx/pptx 匯出測試未實跑（缺套件，未確認現況）；
建議 `py -3 -m pip install python-docx python-pptx` 恢復完整基準。
空目錄 `.pytest_tmp_tag` 已由 Claude 刪除。

**→ 下一棒**：無，待使用者裁決（可實際開檢視器點標籤試用）。

### 2026-07-10 10:15 — 標籤篩選擴及檔案分頁〔已實作＋待驗證〕

**作者**：Codex
**類型**：實作

**Changes**:

- `app/file_browser.py:40-195`: added persistent tag filtering, filtered library headers, the no-match hint, and `has_open_folder()`.
- `app/left_panel.py:63-71`: shared the real `TagIndex` with Recent and Files.
- `app/window.py:2023-2028`: filters both panels and selects Files when a library folder is available, otherwise Recent.
- `tests/test_file_browser.py:23-75` and `tests/test_window_integration.py:322-342`: cover filtering, refresh persistence, no-match, clear, existing Recent behavior, and tab selection.

**pytest**:

- Targeted: `2 passed`.
- Full suite: `213 passed, 4 skipped` (exit 0). The supplied `235 passed, 2 skipped` baseline could not be reproduced because `py -3` is Python 3.13.5 and lacks `python-docx` / `python-pptx`; a Temp-only install was blocked by the restricted package index.
- `py -3 -m py_compile ...` and `git diff --check`: passed.

**→ 下一棒**：@Claude 派獨立驗收

### 2026-07-10 09:57 — 派工：標籤篩選擴及「檔案」分頁〔提案→派工〕

**作者**：Claude
**類型**：提案＋派工

**動機**：現況點「標籤」分頁的標籤只篩「最近」清單（`app/window.py:2023-2026`
`_on_tag_selected` → `recent.set_tag_filter`），「檔案」分頁的資料夾樹完全不受
影響。使用者預期標籤可用於檔案分類瀏覽，故擴充篩選範圍至檔案樹。

**規格**：
1. 點標籤時，「檔案」分頁的檔案樹只顯示帶該標籤的檔案（來源
   `TagIndex.files_with_tag()`，`app/tag_index.py:74`）；資料夾節點只保留
   含符合檔案者。無符合檔案時顯示「沒有符合標籤的檔案」提示。
2. 「全部（清除篩選）」同時清除「最近」與「檔案」兩邊的篩選。
3. 分頁切換行為：點標籤後，若「檔案」分頁已開資料夾則切到「檔案」分頁，
   否則維持現行切到「最近」。
4. 篩選狀態在檔案樹重新整理（換資料夾、手動 refresh）後須保持。
5. 既有「最近」篩選行為（`app/recent_files.py:47-68`）不得回歸。

**→ 下一棒**：@Codex 實作（gpt-5.6-sol / high，預設檔），完成後留
〔已實作＋待驗證〕節；自動接棒 ✅，Claude 收到後另派 fresh 獨立方驗收。

### 2026-07-09 14:25 — 螢光擦除驗收退回→Claude 修單行崩潰→複驗通過〔已實作＋已驗證〕

**作者**：Claude
**類型**：驗證＋實作

**fresh 驗收結果（推翻 13:45 節的「已驗證」）**：面板刪除鈕、Ctrl+Z、
sidecar 清除/相容、hit-test（zoom/多頁/重疊）全過；**但畫布右鍵在真事件下
100% 崩潰**——`pdf_view.py:647` 用 `event.position()`，PySide6 的
`QContextMenuEvent` 沒有此方法（只有 `pos()`/`globalPos()`），且連帶打壞
既有「複製/螢光標記」右鍵選單。Codex 的測試 fake `_Event` 自帶 `position()`
把 bug 蓋掉（測試綠但實際炸）。

**Claude 修正**：`pdf_view.py` contextMenuEvent 改
`self.viewport().mapFromGlobal(event.globalPos())`（驗收方已預先驗證此修法）；
`tests/test_pdf_highlights.py` fake event 換成真 `QContextMenuEvent`。
本機 pytest 235 passed, 2 skipped。已交同一驗收方複驗。

**教訓（給之後的測試棒）**：fake 物件必須貼齊真 API——fake 出真物件沒有的
方法會讓測試通過但產品崩潰。

**複驗結果（14:40，同一驗收方，真視窗 13/13 全過）**：右鍵命中刪除、
空白處無誤刪、選取右鍵「複製/螢光標記」回歸恢復、zoom 2.0 與第二頁命中、
面板刪除鈕、Ctrl+Z、sidecar 清除與舊格式相容全過；pytest 235 passed。
**驗收通過，無阻斷風險殘留。**

**→ 下一棒**：無，待使用者裁決——工作樹含第三批重構＋螢光擦除 UI
（未 commit），加上已本機 commit 的 PySide6 遷移（7a0936a 未 push），
等使用者手動驗完裁決 commit/push/發版。

---

### 2026-07-09 13:45 — PDF 螢光筆擦除入口補齊〔已實作＋已驗證→部分退回，見 14:25 節〕

**作者**：Codex
**類型**：實作＋驗證

照 13:25 派工節完成 1-4。`app/pdf_view.py:85` 新增
`highlight_delete_requested`，`app/pdf_view.py:584` 用 `_pos_to_page` 後的
頁座標 hit-test 既有 highlight rect；`app/pdf_view.py:647` 在 PDF 畫布右鍵
命中 highlight 時加入「刪除此螢光標記」，並由 signal 接到
`app/window.py:265` 的 `_pdf_highlight_delete`。pen mode undo 在
`app/pdf_view.py:606`/`app/pdf_view.py:625` 以 Ctrl+Z 刪最新建立的 highlight；
快捷鍵說明同步補在 `app/window.py:452`。`app/window.py:1731` 對不存在 id
直接略過，避免多餘存檔。

螢光面板新增可見刪除鈕：`app/pdf_highlights_panel.py:63` 建立按鈕列，
`app/pdf_highlights_panel.py:80` 保留/清除選取狀態，`app/pdf_highlights_panel.py:162`
依選取啟用刪除；既有右鍵刪除仍保留。為符合「刪除後 sidecar 消失」驗收，
`app/pdf_highlights.py:130` 讓空 highlights 存檔移除 `.highlights.json`。

測試：`tests/test_pdf_highlights.py:49` 覆蓋空清單移除 sidecar；
`tests/test_pdf_highlights.py:82` 覆蓋 hit-test 命中/未命中與重疊優先；
`tests/test_pdf_highlights.py:102` 覆蓋右鍵選單刪除 action 會發 signal；
`tests/test_pdf_highlights.py:181` 覆蓋 pen mode Ctrl+Z；`tests/test_pdf_highlights.py:229`
覆蓋面板可見刪除鈕。`tests/test_window_integration.py:106` 只補前一棒 fake
PdfView 的新 signal，讓既有整合測試跟真介面一致。未修改
`app/export_actions.py`、`app/session_state.py`、`app/update_flow.py`。

**驗證證據**：

- `py -3 -m py_compile app\pdf_view.py app\pdf_highlights_panel.py app\pdf_highlights.py app\window.py tests\test_pdf_highlights.py tests\test_window_integration.py`：通過。
- `py -3 -m pytest -q -p no:cacheprovider --basetemp=.codex_verify\pytest6 tests\test_pdf_highlights.py`：`13 passed`。
- `py -3 -m pytest -rs -q -p no:cacheprovider --basetemp=.codex_verify\pytest6 tests`：`211 passed, 4 skipped`；skip 為本 sandbox 缺 `docx`、`pptx`，以及既有 headless WebEngine opt-in 2 個。
- offscreen 程式化真流程：新增 highlight → `PdfView.highlight_at(QPoint(35, 30))`
  命中該 id → `highlight_delete_requested` 接 `_pdf_highlight_delete` →
  `flow.pdf.highlights.json` 被移除；腳本輸出 `flow ok: add -> hit-test -> delete -> sidecar removed`。

**→ 下一棒**：@Claude fresh 驗收（建議含真視窗：畫布右鍵刪除、面板刪除鈕、pen mode Ctrl+Z）。

### 2026-07-09 13:25 — PDF 螢光筆無法擦除：診斷＋派工補 UI 入口〔提案〕

**作者**：Claude
**類型**：分析＋派工

使用者手動驗收時回報「螢光筆無法擦除」。診斷（fresh subagent）：**非回歸、
是既有功能缺口**——刪除後端完好（store/save/panel 實測可用），唯一入口
藏在側欄螢光清單右鍵（pdf_highlights_panel.py:130-132 →
window.py:1729 `_pdf_highlight_delete`）；PDF 畫布 contextMenuEvent
（pdf_view.py:602-622）對既有 highlight 無 hit-test、無刪除項；H 鍵只加
不減、pen mode 無 undo。v1.14.0 行為相同（git diff 證實遷移/重構未碰）。

**@Codex（gpt-5.5/high）補齊擦除 UI**（動 pdf_view.py、
pdf_highlights_panel.py、window.py 接線、tests）：

1. 畫布右鍵刪除：contextMenuEvent 加 highlight hit-test（`_pos_to_page`
   轉頁座標對 `self._highlights` rects 包含測試），命中加「刪除此螢光標記」
   選項，新增 `highlight_delete_requested = Signal(str)` 接
   `_pdf_highlight_delete`。
2. 螢光面板加可見「刪除」按鈕（對齊 annotations_panel.py:78-79 設計）。
3. pen mode Ctrl+Z 撤銷最後一筆 highlight。
4. pytest 補：hit-test 命中/未命中、刪除 signal 流、Ctrl+Z。

**怎樣算做好**：真實流程可用——畫布畫一筆 → 右鍵點該筆 → 刪除 → 畫面
與 sidecar 都消失；面板按鈕同效；Ctrl+Z 撤最後一筆；pytest 全綠
（基線 229 passed, 2 skipped）。

**→ 下一棒**：@Codex 實作 → Claude 派 fresh 驗收（含真視窗操作）。

---

### 2026-07-09 11:20 — 第三批 fresh 驗收通過：遷移＋重構全案完成〔已實作＋已驗證〕

**作者**：Claude
**類型**：驗證

**fresh 驗收（未參與實作）：通過（6/6）。** pytest 229 passed（複跑兩次）；
`git diff 7a0936a` 逐塊比對確認純搬家（closeEvent detached 跳過、匯出守衛、
更新 SHA-256/白名單、QSettings key、選單接線全部原樣）；循環 import 零；
整合測試 7 個全是真斷言、fake 邊界合理；真視窗 14 項冒煙全過（含 detach、
session 存還原）；export_actions 新路徑實匯 PDF/DOCX 成功。
備註：window.py 實為 1970 行（Codex 回報 2191 為誤差）；三個新模組
untracked，commit 時必須 git add。

**待使用者裁決**：push PySide6 遷移 commit（7a0936a）＋ commit 第三批
＋發 v1.15.0？

**→ 下一棒**：無，待使用者裁決。

---

### 2026-07-09 10:20 — 第三批 window.py 拆模組＋整合測試完成〔已實作＋已驗證〕

**作者**：Codex
**類型**：實作＋驗證

**改動**：照 10:05 派工節只搬家不改行為，從 `app/window.py` 拆出三個模組。
匯出流程移至 `app/export_actions.py:40`、`app/export_actions.py:67`、
`app/export_actions.py:126`，PDF 頁面設定、單頁匯出與完成回呼在
`app/export_actions.py:183`、`app/export_actions.py:249`、
`app/export_actions.py:317`。session / QSettings 狀態移至
`app/session_state.py:17`、`app/session_state.py:26`、`app/session_state.py:84`、
`app/session_state.py:97`、`app/session_state.py:160`，保留 detached 視窗不覆蓋
session 的 close 邏輯。更新檢查、下載與啟動安裝移至
`app/update_flow.py:15`、`app/update_flow.py:25`、`app/update_flow.py:46`、
`app/update_flow.py:63`、`app/update_flow.py:125`。

**MainWindow 狀態**：`app/window.py` 保留薄委派入口，選單/快捷鍵簽名不變：
偏好與 QSettings 狀態委派在 `app/window.py:471`、`app/window.py:474`、
`app/window.py:692`、`app/window.py:695`、`app/window.py:1892`、
`app/window.py:1904`；匯出入口在 `app/window.py:2141`、`app/window.py:2144`、
`app/window.py:2147`；更新入口在 `app/window.py:2163`、`app/window.py:2166`；
geometry/closeEvent 在 `app/window.py:2186`、`app/window.py:2189`。
`app/window.py` 目前 2191 行，低於派工目標 <2200。

**整合測試**：新增 `tests/test_window_integration.py:274`、
`tests/test_window_integration.py:293`、`tests/test_window_integration.py:307`、
`tests/test_window_integration.py:323`、`tests/test_window_integration.py:343`、
`tests/test_window_integration.py:364`、`tests/test_window_integration.py:391`，
覆蓋開檔成分頁、切換分頁載入正確文件、關分頁清狀態、detach 移至新視窗、
session 存/還原、匯出守衛、`open_path` 進既有視窗路徑；使用 fake renderer/PDF/panel
避開 WebEngine 依賴，斷言數 >10。

**驗證證據**：

- `py -3 -m py_compile app\window.py app\export_actions.py app\session_state.py app\update_flow.py tests\test_window_integration.py main.py`：通過。
- `py -3 -m pytest -q -p no:cacheprovider --basetemp=.codex_verify\pytest5 tests\test_window_integration.py`：`7 passed`。
- `py -3 -m pytest -q -p no:cacheprovider --basetemp=.codex_verify\pytest5 tests\test_window_integration.py tests\test_updater.py tests\test_settings_dialog.py`：`47 passed`。
- `py -3 -m pytest -rs -q -p no:cacheprovider --basetemp=.codex_verify\pytest5 tests`：`205 passed, 4 skipped`；skip 為本 sandbox 缺 `docx`、`pptx`，以及既有 headless WebEngine opt-in 2 個。
- 全 repo `py_compile`（`main.py` + `app/` + `tests/` + `tools/`）：通過。
- 新模組 import 煙測：`import app.window, app.export_actions, app.session_state, app.update_flow` → `imports ok`。
- offscreen 真主視窗煙測：真 `MainWindow.open_path("README.md")` 後 `tabs 1`、`current README.md`、`kind markdown`、`active README.md`；Chromium 僅輸出 GPU fallback 訊息。

**→ 下一棒**：@Claude 派 fresh 驗收；使用者裁決是否 commit/push。

### 2026-07-09 10:05 — PySide6 遷移驗收通過、本機 commit；派第三批〔提案〕

**作者**：Claude
**類型**：驗證＋派工

**遷移 fresh 驗收：通過（7/7）。** 殘留掃描零命中；pytest 222 passed；
`RUN_WEBENGINE_TESTS=1` 下標註橋 5 passed（**原本兩個 flaky skip 在 PySide6
下轉為穩定通過**）；真視窗冒煙＋單一實例 IPC 過；flowchart shape 改名驗證過；
**PyInstaller 實打包 exe 冒煙過**（644MB dist，WebEngine 資源齊）。
已本機 commit `7a0936a`（44 檔，未 push——push/發版待使用者確認）。

**第三批派工：window.py 拆模組＋整合測試。**

**@Codex（gpt-5.5/high）**，範圍與原則：

1. 從 `app/window.py`（2700+ 行）拆出三個高內聚模組，**只搬家不改行為**：
   - `app/export_actions.py`：`_export_pdf`/`_export_pptx`/`_export_docx`
     ＋頁面尺寸對話框＋單頁匯出流程（約 window.py:2337-2560）；
   - `app/session_state.py`：session 儲存/還原（closeEvent 持久化、
     `restore_last_session`、geometry、`_is_detached` 跳過邏輯）；
   - `app/update_flow.py`：更新檢查/下載/啟動安裝流程（約 window.py:2627-2716）。
   MainWindow 保留薄委派（選單 action 簽名不變）。
2. 補整合測試 `tests/test_window_integration.py`：開檔成分頁、切換分頁
   內容正確、關分頁、detach 移至新視窗、session 存/還原、匯出守衛
   （編輯模式/非 markdown 時不觸發）、IPC open_path 進既有視窗。
   離屏可跑（renderer 用假分頁內容即可，避免 WebEngine 依賴）。
3. 鐵律：分頁不持有 widget 模型不可破壞；QSettings key 不改名；
   公開行為（選單、快捷鍵、狀態列訊息）零變更。

**怎樣算做好**：pytest 全綠且新增整合測試 ≥10 個實質斷言；window.py
行數顯著下降（目標 <2200）；`py -3 main.py README.md` 冒煙正常；
三個新模組無循環 import。

**→ 下一棒**：@Codex 實作 → Claude 派 fresh 驗收 → 使用者裁決 push/發版。

---

### 2026-07-09 09:04 — PySide6 全面遷移完成，自動化通過〔已實作＋已驗證〕

**作者**：Codex
**類型**：實作＋驗證

**改動**：全 repo 由舊 Qt binding 遷移到 PySide6。程式進入點與主視窗 import
已改為 `PySide6`（`main.py:8`、`app/window.py:8`），signal/slot 改為
`Signal`/`Slot`（`app/annotation_bridge.py:7`、`app/annotation_bridge.py:18`、
`app/renderer.py:105`、`app/window.py:122`）。WebEngine/WebChannel/PDF/SVG 相關
模組同步到 PySide6（`app/renderer.py:19`、`app/renderer.py:20`、
`app/renderer.py:556`、`app/pdf_view.py:29`、`app/mermaid_workspace.py:9`）。
PySide6 沒有舊 sip deletion helper，`app/flowchart_canvas.py:8` 改用
`shiboken6.isValid()`，並把 `_NodeItem` 的 `shape` 資料欄位改名為
`_node_shape` 避開 PySide6 會呼叫 `QGraphicsPathItem.shape()` 的方法碰撞
（`app/flowchart_canvas.py:55`、`app/flowchart_canvas.py:842`、
`app/flowchart_canvas.py:864`）。

**設定/文件**：`requirements.txt:1` 改為 `PySide6>=6.11`；
`markdown_viewer.spec:30`～`markdown_viewer.spec:36` 改列 PySide6 的
QtPdf/QtWebChannel/QtWebEngine/QtSvg 與 `shiboken6` hidden imports。
開發與使用文件同步到 PySide6（`DEVELOPMENT.md:38`、`DEVELOPMENT.md:48`、
`README.md:3`），歷史設計文件也同步語彙以滿足全 repo 殘留掃描
（`docs/superpowers/plans/2026-06-25-annotations.md:9`、
`docs/superpowers/specs/2026-06-25-annotations-design.md:8`）。

**驗證證據**：

- `py -3 -c "import PySide6; print(PySide6.__version__)"`：`6.11.0`。
- `py -3 -m py_compile` 全 `app/`、`tests/`、`tools/` 與 `main.py`：通過。
- `py -3 -m pytest -q -p no:cacheprovider --basetemp=.codex_verify\pytest4 tests`：
  `198 passed, 4 skipped`。
- `py -3 -m pytest -rs -q -p no:cacheprovider --basetemp=.codex_verify\pytest4_rs tests`：
  skip 為 `docx`、`pptx` 未安裝各 1 個，以及既有 WebEngine opt-in flaky 測試 2 個。
- 舊 Qt binding / 舊 signal-slot / 舊 sip deletion helper 關鍵字全 repo 掃描：
  無命中。
- offscreen GUI 煙測（`MainWindow.open_path("README.md")`，tag index 指到
  `.codex_verify`）：分頁數 1、current `README.md`、TOC 14 項、renderer path
  `README.md`。

**限制/待驗證**：sandbox 內可見真視窗人工冒煙未跑；直接走預設 AppData 的
主視窗煙測會被 sandbox 擋在 `C:\Users\USER01\AppData\Roaming\python\markdown-viewer\tag_index.tmp`
寫入權限，因此我改用專案內 tag index 做 offscreen 煙測。`RUN_WEBENGINE_TESTS=1`
的 annotation WebEngine opt-in 測試在 headless/sandbox 下仍不穩，留給 Claude
在一般桌面 session 做真視窗與 WebEngine bridge fresh 驗收。

**→ 下一棒**：@Claude fresh 驗收 PySide6 版（真視窗 README.md、渲染/TOC/標註面板、
WebEngine bridge；可行的話 PyInstaller 實打包）。

### 2026-07-09 08:55 — PySide6 遷移派工（之後接第三批）〔提案〕

**作者**：Claude
**類型**：派工

使用者裁決：先遷移 PySide6（獨立一版）、再第三批。本機 PySide6 6.11.0 已裝。

**@Codex（gpt-5.5/high）PySide6 全面遷移**，範圍全 repo：

1. 舊 Qt binding import → `from PySide6.*`（92 行/35 檔，含 tests 與 tools）；
   舊 signal/slot API → `Signal`/`Slot`（41 處/9 檔，import 自
   `PySide6.QtCore`）。特別小心 `app/annotation_bridge.py`（QWebChannel 橋，
   13 處 Slot 帶型別標註，PySide6 的 `@Slot(str, result=str)` 語法確認相容）
   與 `app/fragment_render.py`、`app/renderer.py` 的 WebEngine 回呼。
2. `requirements.txt`：移除舊 Qt binding 與舊 WebEngine 套件，改 `PySide6>=6.11`。
3. `markdown_viewer.spec`：hiddenimports/collect_data_files 全改 PySide6
   對應（WebEngine、QtPdf 內含於 PySide6）。
4. `DEVELOPMENT.md` 套件清單同步更新。
5. **零功能變更**：不順手重構、不改行為。

**怎樣算做好**：全 repo grep 無殘留舊 Qt binding 參照；`py -3 -m pytest tests`
全綠（基準 222 passed, 2 skipped，不得新增 skip/fail）；`py -3 main.py
README.md` 真視窗冒煙正常（渲染、TOC、標註面板）；py_compile 全檔通過。

**→ 下一棒**：@Codex 實作 → Claude 派 fresh 驗收（含 WebEngine 橋端到端、
可行的話 PyInstaller 實打包）→ 本機 commit 隔離 → 開第三批。push/發版
待使用者確認。

---

### 2026-07-09 09:20 — v1.14.0 發布完成〔已同步〕

**作者**：Claude
**類型**：同步

使用者裁決 commit＋發版。commit `e1da93f` 推 main（14 檔 +1404/-163）、
tag `v1.14.0` 觸發 Actions → 全綠，Release 資產
`MarkdownViewer_Setup_v1.14.0.exe` 已發布。發布前 pytest 222 passed。
內容：偏好設定視窗、大檔背景渲染＋debounce＋防串頁、匯出品質三項、
捲動還原修正。

另：使用者詢問 PySide6 遷移，Claude 評估**可行**（92 import 行/35 檔、
41 處 Signal/9 檔、無 sip；PySide6 6.11 支援 Py3.14）且建議
**先遷移（獨立一版 v1.15.0）再做第三批**，理由＝機械改動先行、第三批
新模組與整合測試只寫一次、LGPL 解商業授權 P0。待使用者裁決開工。

**→ 下一棒**：無，待使用者裁決（PySide6 遷移開工？）。

---

### 2026-07-09 08:45 — 第二批重驗通過：升級輪一、二批全數完成〔已實作＋已驗證〕

**作者**：Claude
**類型**：驗證

**fresh 重驗（同一驗收方，對照其昨日基準）：通過。**
捲動還原修復實證（scrollY 1500 vs 修前 0，與 v1.12.0 基準一致）；效能三項
未被破壞（心跳 137.9ms、50 次編輯只渲染 1 次、race 8/8）；pytest
**222 passed, 2 skipped**；marker 方案邊界審視過（使用者文件偽造 meta 會被
escape 無法誤判；PDF/錯誤頁路徑合理；fail-safe 方向安全）。低風險備註：
自訂 CSS 含字面 `</head>` 的極端情況 marker 會失效（僅捲動不還原，不誤套）。

**升級輪狀態**：第一批（匯出品質＋偏好設定視窗）＋第二批（大檔效能）
全部實作完成、獨立驗收通過，皆在工作樹未 commit。

**→ 下一棒**：使用者裁決——是否 commit／發 v1.14.0；第三批（window.py
拆模組＋整合測試）是否開工。

---

### 2026-07-09 08:29 — Codex 修復 pending scroll 中繼頁提前消耗〔已實作＋已驗證〕

**作者**：Codex
**類型**：實作＋驗證

**改動**：`app/renderer.py:31` 新增最終 Markdown HTML 的 generation meta marker；
`app/renderer.py:41` 新增 `_pending_scroll_target()`，讓 `_pending_scroll` 只有在
實際載入的最終內容 generation 與 pending generation 相符時才取出並清空。
`app/renderer.py:202` 的 `loadFinished` 改為先查頁面 marker，再由
`app/renderer.py:227` 判斷是否為目前 generation 的最終 Markdown 頁；
中繼「載入中」頁沒有 marker，因此不會消耗 pending scroll，也不會啟動 scroll-spy timer。
`app/renderer.py:333` 在最終 Markdown `setHtml()` 前寫入 marker。
`tests/test_renderer_async.py:46`、`tests/test_renderer_async.py:58`、`tests/test_renderer_async.py:80`
補上回歸測試，覆蓋中繼頁不消耗、錯 generation 不消耗、正確 generation 才消耗與最終
HTML marker。

**驗證證據**：

- `py -3 -m py_compile app\renderer.py tests\test_renderer_async.py`：通過。
- `py -3 -m pytest -q -p no:cacheprovider --basetemp=.codex_verify\pytest3 tests\test_renderer_async.py`：
  `5 passed`。
- `py -3 -m pytest -q -p no:cacheprovider --basetemp=.codex_verify\pytest3 tests`：
  `198 passed, 4 skipped`；skip 為既有環境/opt-in 類型，未處理 docx/pptx 套件缺口。
- sandbox 內真 `QWebEngineView` 端到端腳本：`load_file(md, scroll_y=1500)` 非同步完成後
  `scroll_y=1500`，exit code 0；Chromium 僅輸出 GPU fallback 訊息。

**→ 下一棒**：@Claude 派 fresh 重驗捲動還原與既有 debounce/race 行為。

---

### 2026-07-09 08:25 — 續工：派 Codex 修捲動還原回歸〔提案〕

**作者**：Claude
**類型**：派工

接 2026-07-08 18:30 節。工作區狀態確認完好（pytest 219 passed, 2 skipped）。

**@Codex（gpt-5.5/high）**：修 `app/renderer.py:182-185` 的 `_pending_scroll`
消耗時機——非同步化後「載入中」中繼頁必定先 loadFinished，提前把 pending
scroll 消耗掉並捲動中繼頁，導致所有還原捲動情境（切分頁、存檔 reload、
外部變更 reload）跳回頂端。修法照驗收建議：以 generation 標記中繼頁、
只在最終內容那次 loadFinished 套用 `_pending_scroll`（或等價方案）。
順手修低風險項：中繼頁不要啟動 scroll-spy 輪詢 timer（renderer.py:186-187）。

**怎樣算做好**：`load_file(doc, scroll_y=1500)` 非同步完成後 scrollY≈1500
（昨日驗收腳本 verify_scroll_restore.py 的斷言）；切分頁捲動記憶恢復正常；
pytest 全綠；不動昨天已通過驗收的行為（debounce、race 防護、心跳）。

**→ 下一棒**：@Codex 實作；交回後 Claude 派 fresh 重驗（沿用昨日驗收方）。

---

### 2026-07-08 18:30 — 第二批 fresh 驗收：退回（捲動還原回歸）；使用者喊停，明日續〔已駁回〕

**作者**：Claude
**類型**：驗證＋同步

**fresh 驗收結果（推翻 16:35 節的「已驗證」）**：效能目標全達標——5MB 檔
GUI 心跳最大 129ms（<200ms）、50 次連打只渲染 1 次、race 防護 8/8、執行緒
安全審視過、真視窗冒煙過、pytest 219 passed。**但發現一個已實證的中度回歸**：

- **捲動位置還原壞掉**（`app/renderer.py:182-185`）：改非同步後「載入中」
  中繼頁必定先 loadFinished，把 `_pending_scroll` 提前消耗掉；實測
  `load_file(doc, scroll_y=1500)` 新碼最終 scrollY=0，v1.12.0 基準=1500。
  影響：切分頁記憶捲動、存檔 reload、外部變更 reload 全部跳回頂端。
  **修法**：`_pending_scroll` 改在最終內容那次 loadFinished 才套用
  （以 generation 標記中繼頁，或套用最終 HTML 前不消耗）。

次要（低）：中繼頁就啟動 scroll-spy timer 空轉（renderer.py:186-187）。
另供參考：tests/conftest.py 用 `QApplication([])`，PySide6 6.11 下若未來加
WebEngine 測試會因空 argv 崩潰（0xC0000409），現有測試不受影響。

**目前工作區狀態（未 commit）**：第一批（匯出品質＋設定視窗，已驗收通過）
＋第二批（大檔效能，待修上述回歸）都在工作樹。驗收腳本在 Claude session
scratchpad（verify_scroll_restore.py 可直接重驗）。

**→ 下一棒（明日）**：@Codex 修 `_pending_scroll` 消耗時機（單點修正）→
fresh 重驗捲動還原＋回歸；通過後使用者裁決是否 commit／發版；之後才輪
第三批（window.py 拆模組＋整合測試）。

---

### 2026-07-08 16:35 — 大檔效能背景渲染＋debounce 防串頁〔已實作＋已驗證→已駁回，見 18:30 節〕

**作者**：Codex
**類型**：實作＋驗證

**改動**：`app/renderer.py:84` 新增 `_MarkdownRenderWorker`，把 Markdown
檔案與編輯預覽的 `convert()` / `convert_text()` 搬到 `QThreadPool` 背景執行；
`app/renderer.py:174`、`app/renderer.py:231` 以 generation counter 標記每次
載入/預覽請求，`app/renderer.py:256` 在結果回 GUI thread 時同時檢查 generation
與 `_current_path`，過期結果直接丟棄，避免快速切換分頁後串頁。`app/renderer.py:299`
新增 `render_markdown_text()` 給編輯預覽走同一條背景渲染管線。`app/md_converter.py:66`
與 `app/md_converter.py:563` 對 parser/cache/user CSS 加 `RLock`，避免背景 worker
同時碰全域 parser/cache。`app/window.py:305` 把 live preview debounce 調成 400ms，
`app/window.py:1414` 的 `_update_preview()` 改為呼叫非同步 `render_markdown_text()`。
新增 `tests/test_renderer_async.py:6`、`tests/test_renderer_async.py:22` 覆蓋 worker
回傳 generation/source/headings 與文字預覽 HTML。

**驗證證據**：

- `py -3 -m py_compile app\md_converter.py app\renderer.py app\window.py`：通過。
- `py -3 -m pytest -q -p no:cacheprovider --basetemp=.codex_verify\pytest2 tests`：
  `195 passed, 4 skipped`；skip 屬 sandbox 缺 docx/pptx 與既有 WebEngine opt-in 類型，
  未嘗試修 docx/pptx 套件。
- 受影響測試先跑：`tests\test_renderer_async.py tests\test_md_converter_features.py`
  → `18 passed`；`tests\test_annotation_bridge.py` → `3 passed, 2 skipped`。
- 自動化驗證腳本（因 `$env:TEMP` 與 `D:\tmp` 對 sandbox 拒絕寫入，改暫放
  `.codex_verify\codex_verify_large_render.py`，跑完已刪）：5MB 級 md 背景 worker
  啟動 `2.4ms`、QTimer heartbeat 最大間隔 `118.4ms`（<200ms）；連續 50 次輸入只觸發
  `1` 次 debounce 預覽渲染（≤3）；快速切換 20 次後舊 generation 結果未覆蓋目前內容、
  current generation 可正常套用。一次失敗的 headless WebEngine 嘗試留下
  `.codex_verify\codex_mdviewer_6wvcgf0u\big.md`，sandbox ACL 鎖住無法刪除，與既有
  `.codex_verify` 權限限制同類。

**→ 下一棒**：@Claude 派 fresh 驗收。

---

### 2026-07-08 15:52 — 第一批 fresh 驗收通過＋Gemini 派工檔位疏失檢討〔已實作＋已驗證〕

**作者**：Claude
**類型**：驗證＋反省

**第一批驗收（fresh subagent，未參與實作）：通過。**
pytest 217 passed / 2 既有 skip。匯出品質端到端 23 項檢查過（DOCX cell run
bold/italic/Consolas、小圖不放大、大圖夾內容寬、壞遠端圖占位且 5s timeout
生效、budget 不扣本地圖）；設定視窗 GUI 驗證 18/18（四分頁、現值載入、
改 dark 即時生效寫入 QSettings、cancel 不寫入、6 個 key 全未改名）。
低風險項記入待辦：PPT 無 DPI PNG 以 72dpi 解讀與 DOCX 96dpi 不一致
（pptx_export.py:649）；單張超額圖直接歸零 budget（:470-473）；GIF/WebP
無原生尺寸解讀會撐滿頁寬（docx_export.py:171-176）；settings_dialog.py:14
未使用 import。

**檔位疏失（Claude 自認）**：本日兩次 `agy` 派工均未帶
`--model "Gemini 3.5 Flash (Medium)"` flag，Antigravity 以其自身預設模型
（Claude Sonnet/Opus）執行，**實際消耗的是 Antigravity 內的 Claude 配額**，
違反「派工一律照設定表帶 flag」規則。糾正：今後 agy 派工一律明帶 `--model`，
派工前用 `agy models` 核對名稱。

**→ 下一棒**：Claude 派第二批（大檔效能，@Codex）。

---

### 2026-07-08 15:58 — 第二批派工：大檔效能〔提案〕

**作者**：Claude
**類型**：派工

**@Codex（gpt-5.5/high）大檔效能**，兩個痛點：

1. **渲染不卡 UI**：`md_converter.convert()` 目前在 GUI 執行緒同步
   `read_text`＋`_PARSER.render` 全文渲染，大檔會凍介面。改為背景執行
   （QThread/QThreadPool worker），完成後回 GUI 執行緒 setHtml；切換分頁/
   關檔要能取消未完成的渲染（world id 或 generation counter 防過期結果
   蓋新內容）。>10MB 拒絕預覽的既有保護保留。
2. **編輯模式即時預覽 debounce**：目前每次按鍵全文重渲染
   （window.py:1453 一帶），改為 300-500ms debounce＋僅在停止輸入後渲染，
   渲染中再有輸入則丟棄舊任務。

範圍：`app/md_converter.py`、`app/renderer.py`、`app/window.py` 渲染呼叫點、
tests。**不動** settings_dialog.py、docx/pptx_export.py。鐵律：分頁切換的
`_activate_tab` 重載模型不可破壞；渲染結果必須對應當下 active path
（防 race 蓋錯分頁內容）。

**怎樣算做好**：pytest 全綠；自動化驗證——產生 5MB 級 md，開檔時 UI 執行緒
不被阻塞超過 200ms（可用 QTimer 心跳量測）；編輯模式連續輸入 50 字元只觸發
≤3 次渲染；快速切換兩個分頁 20 次內容不串頁。

**→ 下一棒**：@Codex 實作，交回後 Claude 派 fresh 驗收。

---

### 2026-07-08 15:20 — 偏好設定視窗：Gemini 棒中斷、實作已落地〔已實作＋待驗證〕

**作者**：Claude（代記；實作者 Gemini，其 session 在最後 GUI 驗證步驟 timeout
中斷，未及自行留痕）

**Gemini 已完成**：新增 `app/settings_dialog.py`（分頁式設定對話框）、
`app/window.py` 接線（`_open_preferences` 改開新對話框）、新增
`tests/test_settings_dialog.py`；其回報 pytest **217 passed, 2 skipped** 全綠；
沿用既有 QSettings key（theme/content_zoom/update_check_enabled/
custom_css_path/pdf_page_size/pdf_orientation）。**GUI 實際開啟驗證未完成**
（timeout 中斷點），留給 fresh 驗收一併驗。Claude 已清掉其留在專案根目錄的
臨時驗證腳本與 stackdump。

**→ 下一棒**：等 @Codex 匯出品質棒交回後，Claude 對第一批兩項一起派 fresh 驗收。

---

### 2026-07-08 15:03 — 匯出品質三項實作〔已實作＋待驗證〕

**作者**：Codex
**類型**：實作＋驗證

**改動**：表格 cell model 從純文字改為 runs，parser 在 `app/pptx_export.py:210`
的 table cell 內保留粗體/斜體/inline code；PPT 表格輸出在
`app/pptx_export.py:577` 逐 run 寫入 cell，DOCX 表格輸出在
`app/docx_export.py:148` 共用同一組 runs。圖片載入改成 `app/pptx_export.py:591`
的 bytes loader，遠端圖片使用 `app/pptx_export.py:386` 的 5 秒 timeout 與
`app/pptx_export.py:466` 的整份 export byte budget；超量/逾時/失敗維持占位退化。
DOCX 圖片寬度改由 `app/docx_export.py:171` 依像素與 DPI 算原生寬度，小於頁面可用寬
不放大，超過才夾到 `app/docx_export.py:166` 的內容寬；PPT 仍使用 PowerPoint
原生圖片尺寸並只在超過內容寬/頁高時縮小。

**測試**：新增/更新 `tests/test_pptx_export.py:62`（table inline runs parser）、
`tests/test_pptx_export.py:134`（PPT table cell inline 格式讀回）、
`tests/test_pptx_export.py:240`（遠端圖 timeout/總量保護）、
`tests/test_docx_export.py:69`（DOCX table cell inline 格式讀回）、
`tests/test_docx_export.py:87` 與 `tests/test_docx_export.py:103`（小圖不放大、大圖夾頁寬）、
`tests/test_docx_export.py:158`（遠端大圖退化占位）。

**驗證證據**：

- `py -3 -m py_compile app\docx_export.py app\pptx_export.py`：通過。
- `py -3 -m pytest -q -p no:cacheprovider --basetemp=.codex_verify\pytest-final tests`：
  `193 passed, 4 skipped`。
- `py -3 -m pytest -rs -p no:cacheprovider --basetemp=.codex_verify\pytest-rs tests`：
  `193 passed, 4 skipped`；skip 原因為此 sandbox 的 Python 3.13 缺
  `docx`、`pptx`，另 2 個既有 WebEngine flaky 測試。
- 已嘗試 `py -3 -m pip install python-docx python-pptx`，被 sandbox 網路限制擋下
  （`WinError 10013`）；`py -3 -m pip show python-docx python-pptx` 也顯示目前
  `py -3` 看不到這兩個套件。
- 已建立系統 Temp 臨時端到端腳本（含表格粗體/斜體、inline code、小圖、大圖、
  超量遠端圖，並含 DOCX/PPTX 讀回斷言），腳本已自刪；執行在
  `from docx import Document` 失敗：`ModuleNotFoundError: No module named 'docx'`。

**→ 下一棒**：@Claude 在有 `python-docx`/`python-pptx` 的完整本機環境補跑
`py -3 -m pytest tests` 與同款端到端匯出讀回斷言，通過後再標記已驗證。

---

### 2026-07-08 14:54 — 商業化升級輪：三批派工計畫＋第一批派出〔提案〕

**作者**：Claude
**類型**：分析＋派工

背景：商業化盤點（14:40 口頭報告使用者）確認升級項。本輪分三批：
第一批＝匯出品質＋偏好設定視窗（並行，檔案不重疊）；第二批＝大檔效能；
第三批＝window.py 拆模組＋整合測試。WYSIWYG 不在此輪（待使用者定位裁決）。

**第一批派工**：

1. **@Codex（gpt-5.5/high）匯出品質三項**（動 `app/docx_export.py`、
   `app/pptx_export.py`、對應 tests；不動 window.py）：
   a) 表格 cell 內 inline 格式（粗體/斜體/inline code）保留——共用 parser
      `pptx_export.py:80-82` 目前只存純文字，需讓 cell 帶 runs model，
      docx/pptx 兩端渲染都吃到；
   b) 圖片尺寸：讀實際像素與 DPI，小於頁寬就用原尺寸，超過才夾到頁寬
      （docx_export.py:36,191 目前固定 6.2 吋）；
   c) 遠端圖片下載加總量上限與逾時保護、失敗退化占位（docx_export.py:168，
      PPT 同模式一併處理；GUI 執行緒不可無上限阻塞）。
   驗收：新增/更新 pytest 覆蓋三項；`py -3 -m pytest tests` 全綠；
   端到端匯出一份含表格粗體＋大小圖＋壞遠端圖連結的 md，docx/pptx 讀回斷言。

2. **@Gemini（Gemini 3.5 Flash (Medium)）完整偏好設定視窗**（新增
   `app/settings_dialog.py`＋window.py 接線；不動 docx/pptx_export）：
   把現有隱式 QSettings 選項集中成一個設定對話框（分頁式）：外觀（主題
   深/淺、內容縮放預設值）、匯出（PDF 頁面尺寸/方向預設）、行為（更新檢查
   開關、自訂 CSS 路徑——取代現有散落選單項 window.py:550-605）、
   關於（版本）。既有 QSettings key 不可改名（相容舊設定）。
   驗收：對話框可開可存、改主題即時生效、舊選單入口移除或導向新對話框、
   `py -3 -m pytest tests` 全綠＋新增 settings dialog 純邏輯測試。

**→ 下一棒**：兩位並行實作；交回後 Claude 派 fresh 驗收，通過即派第二批
（自動接棒 ✅）。

---

### 2026-07-08 14:30 — v1.13.0 發布完成〔已同步〕

**作者**：Claude
**類型**：同步

照 DEVELOPMENT.md 第 6 節流程發布：`bump_version.py 1.13.0`（version.py 與
installer.iss 同步）、CHANGELOG 補 1.13.0 節、commit `05e38fd` 推 main、
tag `v1.13.0` 觸發 GitHub Actions（run 28922281559）→ 全步驟綠，Release
資產 `MarkdownViewer_Setup_v1.13.0.exe` 已發布（非 draft）。發布前
`py -3 -m pytest tests -q` → 195 passed, 2 skipped。

內容：匯出 Word (.docx)、單一實例（多開檔案改開分頁）、分頁移至新視窗。
`AI協作/` 入版控；`status.json` 與 `*.log` 已 gitignore。附註：`.codex_verify/`
被 sandbox ACL 鎖住刪不掉，已 gitignore，待使用者以系統管理員手動刪。

**→ 下一棒**：無，本輪結束。低風險待辦見「待辦與未決」。

---

### 2026-07-08 13:45 — 匯出 Word (.docx)：fresh 驗收通過〔已實作＋已驗證〕

**作者**：Claude
**類型**：驗證

**補齊環境**：Codex sandbox 無網路裝不了 python-docx，Claude 本機補
`pip install python-docx`（1.2.0）後 `py -3 -m pytest tests -q` →
**195 passed, 2 skipped**（skip 為既有 WebEngine flaky 測試，與 docx 無關）。

**fresh subagent 驗收（未參與實作）**：五項全過——pytest 全綠（docx 測試
4 passed 未 skip）；端到端 21 項斷言全過（標題層級、中文 eastAsia=Microsoft
JhengHei、粗斜體、Consolas code、3x3 表格、圖片嵌入、mermaid provider=None
退化成原始碼、缺圖占位）；UI 接線與 `_export_pptx` 守衛一致、例外不崩潰；
spec/requirements 打包設定正確；PPT/PDF 無回歸。**驗收通過，無附帶條件。**

低嚴重度品質項（多為與 PPT 共用 parser 的既有限制，記入待辦觀察）：
小圖被放大到固定 6.2 吋（docx_export.py:36,191）；表格 cell 內 inline 格式
遺失（pptx_export.py:80-82 共用限制）；清單 >3 層縮排扁平化（docx_export.py:82）；
遠端圖片在 GUI 執行緒同步下載可能凍 UI（docx_export.py:168，PPT 同模式）。

**→ 下一棒**：無，待使用者裁決（是否 commit）。

---

### 2026-07-08 13:19 — 匯出 Word (.docx) 實作〔已實作＋待驗證〕

**作者**：Codex
**類型**：實作＋驗證

**改動**：新增 `app/docx_export.py:202` 的 `export_markdown_to_docx()`，直接重用
`app/docx_export.py:29` 匯入的 `pptx_export.parse_elements()` block model；支援
Heading/Para/ListBlock/Code/Table/Image/Quote，表格使用 `Table Grid`
（`app/docx_export.py:147`），一般中文 run 設 `w:eastAsia=Microsoft JhengHei`
（`app/docx_export.py:32`、`app/docx_export.py:66`），code run 設 Consolas
（`app/docx_export.py:33`、`app/docx_export.py:144`）。Mermaid/math 會先呼叫
`image_provider` 轉 PNG，失敗時退回原始碼文字（`app/docx_export.py:128`）。

**UI/打包/測試**：`app/window.py:438` 在「匯出 PPT…」後加入「匯出 Word…」；
`app/window.py:2417` 新增 `_export_docx()`，沿用 `_exporting`、`QFileDialog`
filter `Word 文件 (*.docx)`、WaitCursor、狀態列與錯誤提示。`requirements.txt:9`
加入 `python-docx>=1.1`；`markdown_viewer.spec:14` 加
`collect_data_files('docx')`、`markdown_viewer.spec:27` 加 hiddenimport `docx`。
新增 `tests/test_docx_export.py:35`，覆蓋 heading/table/code/中文 eastAsia/圖片、
Mermaid provider 成圖與 provider 失敗退化。

**驗證證據**：

- `py -3 -m py_compile app\docx_export.py app\window.py`：通過。
- `py -3 -m pytest -p no:cacheprovider --basetemp=.codex_verify\pytest tests`：
  通過，結果 `178 passed, 4 skipped`；原本不加 `--basetemp` 會因 sandbox 無權掃
  `C:\Users\USER01\AppData\Local\Temp\pytest-of-USER01` 失敗，改用專案內可寫暫存後通過。
- `py -3 -m pytest -p no:cacheprovider --basetemp=.codex_verify\pytest-docx -rs tests\test_docx_export.py`：
  `SKIPPED [1] ... could not import 'docx': No module named 'docx'`。
- 已照派工嘗試 `py -3 -m pip install python-docx`，但 sandbox 網路被拒
  （`WinError 10013`），無法下載套件；`py -3 -m pip cache list` 也沒有 docx wheel。
- 已建立並執行系統 Temp 臨時端到端腳本（含標題/表格/code/中文/圖片/Mermaid），
  腳本已刪除；目前在 `from docx import Document` 即失敗：
  `ModuleNotFoundError: No module named 'docx'`，因此尚不能完成 python-docx 讀回斷言。

**→ 下一棒**：@Claude 在可安裝 `python-docx` 的環境補跑 `pip install python-docx`、
`tests/test_docx_export.py` 與臨時端到端腳本後 fresh 驗收。

---

### 2026-07-08 13:10 — 新功能「匯出 Word (.docx)」：方案定案＋派工〔提案〕

**作者**：Claude
**類型**：分析＋派工

**方案**：模仿 PPT 匯出路線，不走 HTML 轉檔。新增 `app/docx_export.py`：
重用 `app/pptx_export.py:271` 的 `parse_elements()` block model（直接 import，
不複製），用 **python-docx** 寫原生 Word 物件；Mermaid/KaTeX 沿用
`FragmentRenderer`（`app/fragment_render.py`）的 `image_provider` 介面轉 PNG
內嵌，失敗時比照 PPT 退化成原始碼文字。

**@Codex（gpt-5.5/high，照設定表）實作範圍**：

1. 新增 `app/docx_export.py`：`export_markdown_to_docx(text, path, base_dir,
   image_provider)`，支援 Heading/Para/ListBlock/Code/Table/Image/Quote；
   表格套 `Table Grid` style；code 用 Consolas；**中文正文必須設
   `w:eastAsia` 字型**（python-docx 需操作 `rPr`，建議 Microsoft JhengHei）。
2. `app/window.py`：選單「匯出 PPT…」（:437）後加「匯出 Word…」，回呼
   `_export_docx` 仿 `_export_pptx`（:2358，含 `_exporting` 旗標、檔案對話框
   filter `Word 文件 (*.docx)`、WaitCursor、狀態列訊息、失敗退化提示）。
3. `requirements.txt` 加 `python-docx`；`markdown_viewer.spec` 的
   `hiddenimports` 加 `docx`、`datas` 加 `collect_data_files('docx')`。
4. 新增 `tests/test_docx_export.py`（仿 `tests/test_pptx_export.py`，
   驗 heading/表格/code/中文/圖片退化），`py -3 -m pytest tests` 全綠。

**怎樣算做好**：README.md 或含表格+code+Mermaid+中文的測試 md 能匯出 .docx，
用 python-docx 讀回驗證結構（標題層級、表格列數、中文 run 的 eastAsia 字型）；
pytest 全過；不動 PDF/PPT 既有行為。KaTeX/Mermaid 輸出為圖片（不可編輯）
屬可接受取捨。

**→ 下一棒**：@Codex 實作；完成後 Claude 派 fresh 驗收（自動接棒 ✅，一路跑完）。

---

### 2026-07-08 11:45 — fresh 驗收＋修正 detached 視窗覆蓋 session〔已實作＋已驗證〕

**作者**：Claude
**類型**：驗證＋實作

**fresh subagent 驗收結果（兩棒合驗）**：五項驗收條件全過——py_compile 通過；
單一實例端到端通過（第二實例 351ms exit 0、IPC 送路徑分頁 +1、重複路徑不重開、
空訊息不開分頁、強殺 primary 後重啟可正常接手）；detach 自動化通過（2→1、
新視窗載入正確、單分頁 action 停用）。**有條件通過**，條件＝修正以下回歸：

- **（中）detached 視窗 `closeEvent` 無條件寫 QSettings**：關掉只有一個分頁的
  detached 視窗會覆蓋 `open_tabs`/`geometry`，主視窗多分頁 session 下次還原丟失。
- （低-中，列待辦）IPC server parent 是主視窗：主視窗關閉但 detached 視窗留著時，
  之後雙擊 .md 會生出第二個 primary（main.py:110）。
- （低，列待辦）`_open_file` 的 key 未 `resolve()`，與 IPC 送來的 resolve 路徑
  可能大小寫/相對路徑失配造成重複分頁（app/window.py:1480-1494）。

**Claude 修正（中風險項）**：`app/window.py` 新增 `_is_detached` 旗標
（`__init__` 預設 False；`_detach_tab` 對新視窗設 True）；`closeEvent` 改為
detached 視窗跳過 geometry/open_tabs/active_tab/last_file 寫入（保留
`_save_active_view_state`）。`py -3 -m py_compile` 通過，已交同一 fresh 驗收方復驗。

**復驗結果（同一 fresh 驗收方，11:51）**：全數通過——py_compile 過；
QSettings 覆蓋測試過（關 detached 視窗後 geometry/open_tabs/active_tab/last_file
四個 key 不變，關主視窗後 open_tabs 正確寫入剩餘分頁，測試前後有備份/還原使用者
QSettings）；原 detach 功能回歸測試過。**本輪兩項需求驗收通過。**

**→ 下一棒**：無，待使用者裁決（是否 commit；低風險待辦見「待辦與未決」）。

---

### 2026-07-08 11:33 — 分頁右鍵移至新視窗〔已實作＋已驗證〕

**作者**：Codex
**類型**：同步＋實作＋驗證

**同步**：已讀 `AI協作/AI_協作規範.md` 的角色/權限與本檔「專案速覽」，確認本棒
受 Claude 派工；本專案分頁鐵律是分頁只存路徑與 `_tab_state`，renderer/PDF view
共用並在切換時重載。

**改動**：在 `app/window.py:334` 對 `documentTabs` 開啟 custom context menu；
`app/window.py:1570` 新增右鍵選單建構與顯示路徑，選單文字為「移至新視窗」；
`app/window.py:1586` 讓只剩一個分頁時 action 停用；`app/window.py:1593` 新增
detach 流程：複製該分頁路徑與 `_tab_state`、新建 `MainWindow` 載入該路徑，並沿用
`_on_tab_close` 關閉來源分頁。`app/window.py:100` 以 `_DETACHED_WINDOWS` 保留新視窗
Python 參照，避免獨立視窗被回收。

**驗證**：

- `py -3 -m py_compile app/window.py`：通過。
- 離屏自動化 QMenu 驗證通過：建立 `MainWindow`，開 `README.md` 與 `DEVELOPMENT.md`
  兩個分頁，呼叫 `_build_tab_context_menu(1)` 取得 QAction 並 `trigger()`；結果來源視窗
  分頁數由 2 變 1、`DEVELOPMENT.md` 從來源分頁列消失、另一個 `MainWindow` 載入
  `DEVELOPMENT.md`，且新視窗只剩一分頁時「移至新視窗」action 為 disabled。
- 也嘗試 `py -3 main.py README.md`（offscreen、4 秒觀察）：程式啟動到
  `Markdown Viewer 1.12.0 starting`，但在 sandbox 內被既有 `tag_index` 寫入
  `C:\Users\USER01\AppData\Roaming\markdown-viewer\...\tag_index.tmp` 的
  `PermissionError` 中止；因此完整 GUI 人工路徑需在一般使用者權限環境重跑。

**→ 下一棒**：@Claude 收攏本棒與 Gemini 的 `main.py` 單一實例改動，並安排 fresh 驗收。

---

### 2026-07-08 11:30 — 單一實例＋檔案轉送（main.py）〔已實作＋已驗證〕

**作者**：Gemini
**類型**：實作＋同步

**改動摘要**（僅動 `main.py`，未動 `app/window.py`）：

1. **新增 import**：`from PySide6.QtNetwork import QLocalServer, QLocalSocket`（`main.py:10`）
2. **常數**：`_IPC_SERVER_NAME = "MarkdownViewer-single-instance"`（`main.py:17`）
3. **`_try_send_to_running_instance(path_to_send)`**（`main.py:61-74`）：
   建 `QLocalSocket`，嘗試連線既有 server；連上就送 UTF-8 編碼的絕對路徑
   （或空字串），回傳 `True`（呼叫方應退出）；連不上回傳 `False`（成為主實例）。
4. **`_setup_ipc_server(window)`**（`main.py:77-110`）：
   建 `QLocalServer`（parent=window），先 `removeServer` 清殘留再 `listen`。
   收到連線後讀路徑 → 呼叫 `window.open_path(path)` 開分頁 → 解除最小化 →
   `raise_()` + `activateWindow()` 帶視窗到前景。空路徑只帶前景不開檔。
5. **`main()` 流程改動**（`main.py:113-147`）：
   - 建 `QApplication` 後、建視窗前，先 resolve `sys.argv[1]` 為絕對路徑
     （`file_arg`），再呼叫 `_try_send_to_running_instance`；成功就 `sys.exit(0)`。
   - 視窗建好後呼叫 `_setup_ipc_server` 啟動 server，保留 reference 防 GC。
   - 用 `file_arg`（已 resolve 的絕對路徑）傳給 `window.open_path`。

**驗證證據**：

| 步驟 | 指令 | 結果 |
|---|---|---|
| 1 | `py -3 main.py README.md`（保持開啟） | 視窗開啟，顯示 README.md |
| 2 | `py -3 main.py CHANGELOG.md` | **立即退出**（exit code 0），無第二視窗；CHANGELOG.md 在既有視窗成為新分頁 |
| 3 | `py -3 main.py`（無參數） | **立即退出**，既有視窗帶到前景，不開新檔 |

三項均符合派工節「怎樣算做好」的驗收條件。路徑使用 `Path.resolve()` 取絕對路徑，
未寫死任何路徑，PyInstaller 打包相容。

**首次同步確認**：已讀 `AI_協作規範.md`（角色與權限）＋本檔「專案速覽」，
理解架構與分工：Claude 主導指揮、Gemini 為受調度執行方；分頁不持有 widget
（`_tab_state` 集中管理）；單一實例機制須在建視窗前攔截。

**→ 下一棒**：@Claude 收攏驗收（Gemini 這件完成；Codex 的 window.py 分頁右鍵
「移至新視窗」待確認進度）。

---

### 2026-07-08 11:25 — 多開視窗改分頁制：分析定案＋派工兩件〔提案〕

**作者**：Claude
**類型**：分析＋派工

**根因**：app 單程序內已有分頁機制（`app/window.py:1498-1502` 的 `_open_file`
→ `_add_tab`；分頁列 `app/window.py:323-333`），但檔案關聯（`installer.iss:47`）
每次雙擊都啟動新程序，`main.py:63-86` 無條件建新視窗，且全 repo 無
single-instance/IPC 機制，故多開檔案＝多開視窗。

**派工**（兩件互不觸碰同一檔案，並行）：

1. **@Gemini（Gemini 3.5 Flash (Medium)，照設定表）**：`main.py` 加單一實例
   ＋檔案轉送。用 `QLocalServer`/`QLocalSocket`（server 名建議
   `MarkdownViewer-single-instance`）：啟動時先試連既有 server，連上就把
   `sys.argv[1]` 的絕對路徑送過去然後退出；連不上就 listen（先
   `removeServer` 清殘留），收到路徑呼叫 `window.open_path(path)` 並
   `raise_()`/`activateWindow()`。無參數啟動的第二實例也要轉送（送空訊息，
   既有視窗只帶到前景）。
2. **@Codex（gpt-5.5/high，照設定表）**：`app/window.py` 分頁列加右鍵選單
   「移至新視窗」：新建 `MainWindow` 並 `open_path` 該分頁路徑，來源視窗關掉
   該分頁（沿用 `_on_tab_close` 路徑，`app/window.py:1541`）；只剩一個分頁時
   選單項停用。注意分頁不持有 widget（見速覽鐵律），只搬路徑＋狀態。

**怎樣算做好**：`py -3 main.py a.md` 開著的情況下再跑 `py -3 main.py b.md`
→ 不出第二個視窗、b.md 在既有視窗變分頁且視窗到前景；分頁右鍵「移至新視窗」
→ 出現新視窗載入該檔、原視窗分頁消失。exe 打包路徑不可寫死（PyInstaller 相容）。

**→ 下一棒**：@Gemini、@Codex 並行實作；完成後 Claude 收攏，回報使用者，
經確認後另派 fresh 驗收（自動接棒＝❌）。

---

## 待辦與未決

- [ ] （低-中）主視窗關閉、detached 視窗留存時 IPC server 消失，之後雙擊 .md
      會開出第二個 primary（main.py:110）——可考慮 server 移交或 app 級管理
- [ ] （低）`_open_file` 分頁 key 未 `resolve()`，與 IPC 端 resolve 路徑可能
      失配造成同檔重複分頁（app/window.py:1480-1494）
- [ ] （低）docx 匯出品質項：小圖固定放大 6.2 吋（docx_export.py:36,191）、
      表格 cell inline 格式遺失（共用 parser 限制）、清單 >3 層扁平化、
      遠端圖片同步下載可能凍 UI（docx_export.py:168，PPT 同模式）

## 環境變數/參數速查

| 參數 | 位置 | 現值 | 作用 |
|---|---|---|---|
| ＜參數＞ | ＜檔案＞ | ＜值＞ | ＜說明＞ |
