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
| Codex | ✅ | gpt-5.5 | high | default |
| Gemini | ✅ | Gemini 3.5 Flash (Medium) | 內含於模型 | — |
| 自動接棒 | ✅ | —（全域開關，規則見下） | — | — |

> 上表是**本專案**的檔位。設定的**規則文字**（強制檔位、升降檔需使用者同意、
> service_tier 速度、自動接棒定義）見 `AI_協作規範.md`。
>
> 此表由檢視器程式讀寫，**各列開頭（`| Claude |`、`| Codex |`、`| Gemini |`、
> `| 自動接棒 |`）不可改動**。

---

## 專案速覽（2026-07-08 更新）

PyQt6 桌面版 Markdown 閱讀器（v1.12.0），支援 Mermaid 圖表、PDF 匯出、
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

Windows 11 + PowerShell 5.1；Python 3.14（`py -3`）；PyQt6。
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

---

## 🎯 指揮口令與紀錄格式

指揮口令（叫 Gemini/Codex/Claude 的複製貼上範本）、模型選擇表、紀錄格式，
都在 `AI_協作規範.md`（檢視器「規範」分頁）。派工前照那份即可。

---

## 進度紀錄

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

1. **新增 import**：`from PyQt6.QtNetwork import QLocalServer, QLocalSocket`（`main.py:10`）
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
