# 新增筆記 + TXT 支援 — 實作進度（2026-08-25）

## 狀態
- [x] 架構探索完成（Explore agent）
- [x] 實作完成
- [x] 測試通過
- [x] 驗收 agent 核對

最終發布驗證：一般套件 `1176 passed, 43 skipped`；啟用 WebEngine 的完整套件 `1219 passed`；版本 `1.26.0`。

## 需求摘要
1. Ctrl+N 新增筆記對話框：選 .md（預設）/.txt、輸入檔名、自動補副檔名、檔名驗證（空白/Windows 不合法字元/重名不覆蓋）、建在文件庫選取目錄→庫根目錄→無庫則另選位置；建立後刷新目錄樹、開分頁、進最近開啟、直接進編輯模式聚焦。
2. .txt 正式支援：開檔對話框、目錄樹、最近開啟、分頁、session restore、尋找/取代；純文字顯示不渲染 markdown；編輯+Ctrl+S；txt 無預覽（editor-only）。
3. 編碼：新檔 UTF-8；讀取處理 UTF-8 BOM（utf-8-sig 優先）、辨識 UTF-16 BOM；解碼失敗提示、不可 errors=ignore；保留原編碼與 CRLF/LF。
4. Ctrl+N 進 shortcuts.py registry（QShortcut+選單提示+說明視窗同步）。

## 關鍵檔案位置（Explore 結果）
- file_types.py: MARKDOWN_EXTENSIONS:7, document_kind:12 → 加 TEXT_EXTENSIONS/"text"/is_text
- left_panel.py:217 開檔 filter
- window.py: _open_file:1744, _load_document:2031（:2042 branch 點）, _request_view_mode:1442（:1443 擋非 md）, _leave_edit_ui:1629/:1635-1638, _save_edits:1660（:1692 _reload_preview、:1695 _update_front_tags 需跳過 for text）, _enter_edit_mode:1579（:1587 newline sniff, :1588 read_text）, 按鈕 gating :2065-2071, File menu :535-545（command_act:528）, _install_shortcuts:593, toolbar :648/:730/_refresh_icons:866
- editor.py:37 highlighter 硬接 → 加開關；wikilink :139-202、image paste :86-137 對 text 停用
- md_converter.py:663 read_text(utf-8→cp950→gbk)→ 加 utf-8-sig/utf-16 BOM、回傳 encoding
- file_ops.py:53 create_note（.md 專用）→ 泛化 suffix/空白內容
- shortcuts.py:36 WINDOW_SHORTCUTS 加 ShortcutSpec("file.new",...,"Ctrl+N",handler="_new_note")；Ctrl+N 目前無人用
- recent_files.py:270 icon 第三分支
- session_state.py:50/72 restore 過 is_supported_document（file_types 改完自動通）、:105 scroll 只記 markdown → 加 text
- file_browser.py:589 副檔名 filter（吃 SUPPORTED_EXTENSIONS 自動通）、_create_note_action:1356 模式、refresh_libraries:885、_select_path:1201
- document_libraries.py:160 scan filter（自動通）
- window.py:337 on_note_created → _on_browser_note_created:2399（開檔+進編輯的現成路徑）

## 測試
- 跑法：py -3 -X utf8 -m pytest tests -q（WebEngine 測試需 RUN_WEBENGINE_TESTS=1，預設 skip）
- test_window_integration.py:661 registry↔menu 一致性測試（加 Ctrl+N 沒加 menu action 會 fail）
- test_document_libraries.py:67 需加 .txt case；test_file_ops.py、test_shortcuts.py:24

## Git 基準
- main @ 706181c（工作區乾淨；de51d80 為 v1.25.0）

## 實作紀錄（2026-08-25，實作 agent）

### 狀態更新
- [x] 架構探索完成
- [x] 實作完成
- [x] 測試通過：`py -3 -X utf8 -m pytest tests -q` → 938 passed, 43 skipped（WebEngine 自動跳過）
- [x] 驗收 agent 核對：10/10 條驗收全 PASS（fresh agent 獨立跑 938 passed/43 skipped + 11 個自寫 probe）。
  兩個 low 備註留待後續：(1) UTF-16 BE 檔存檔後 byte order 變 LE（仍為合法 UTF-16+BOM）；
  (2) 孤立 CR（舊 Mac 換行）存檔後會變 LF。未 commit，待使用者指示。

### 變更檔案
- `app/file_types.py`：TEXT_EXTENSIONS={".txt"}、document_kind→"text"、is_text()
- `app/file_ops.py`：新增 create_document(folder, name, suffix=".md")——空內容、UTF-8、atomic、重名直接 OSError（不自動編號）；舊 create_note 行為不變
- `app/md_converter.py`：_decode_bytes()（BOM 優先：utf-8-sig / utf-16，再 utf-8→cp950→gbk）、read_text_detailed() 回傳 (text, encoding, newline)（換行在解碼後偵測，UTF-16 CRLF 才抓得到）、read_text() 改為包裝
- `app/editor.py`：set_plain_text_mode()——txt 停用 Markdown highlighter（setDocument(None)）、wikilink 補全、圖片貼上/拖放轉 Markdown 連結；apply_theme 記住 theme、plain 模式跳過 highlighter
- `app/new_note_dialog.py`（新檔）：NewNoteDialog + 純函式 normalized_file_name / validate_new_note；建立動作在 dialog 內執行，重名/失敗保留輸入不關窗
- `app/shortcuts.py`：ShortcutSpec("file.new","文件","新增筆記（Markdown 或純文字）",Ctrl+N,handler="_new_note")
- `app/window.py`：_new_note()（資料夾解析：樹選取→第一個庫根→QFileDialog）；File 選單加「新增筆記…」；_load_document 加 "text" 分支（直接進編輯器、無預覽/標註/backlinks）；_enter_edit_mode 泛化（read_text_detailed、plain 模式、txt 強制 EDIT、回傳 bool、txt 保留 search/reload 按鈕）；_request_view_mode 擋 txt 切模式；_save_edits 的 preview/tags/link 後續只跑 markdown；_reload_current 與外部變更提示加 text 路徑；不支援檔案訊息加 .txt
- `app/left_panel.py`：開檔 filter 加 *.txt（含獨立「純文字檔案 (*.txt)」項）
- `app/file_browser.py`：新公開 API selected_directory() / library_roots() / reveal_created_note()
- `app/recent_files.py`：txt 沿用 markdown 檔案圖示（加註解）
- 測試：tests/test_text_support.py（新）、test_window_integration.py 追加 13 個測試、test_file_ops.py 加 create_document、test_document_libraries.py 加 .txt、test_file_browser.py 原本拿 .txt 當「不支援副檔名」的 fixture 改成 .log

### 設計決策
1. 工具列不加新按鈕：theme.py 現有 icon 沒有適合「新增筆記」的圖示（file-text 已被開啟鈕用），避免誤導；Ctrl+N + File 選單已可觸達。
2. txt 永遠 editor-only：_load_document("text") 直接走 _enter_edit_mode；Ctrl+E/Ctrl+Shift+E 對 txt 無效；search/reload 按鈕在 txt 編輯中保持可用（md 編輯中維持原本停用行為）。
3. UTF-16 BE 檔存檔會變 UTF-16（native/LE BOM）——Python "utf-16" codec 行為，內容仍為合法 UTF-16。
4. text 分頁不記 scroll（session_state 只記 markdown scroll；editor 捲動位置 plumbing 重，略過）。
5. session restore / 快速開啟 / 拖放 / 目錄樹 / document_libraries 皆吃 is_supported_document / SUPPORTED_EXTENSIONS，file_types 改完自動支援。
6. doc_tags facade 對 txt 維持 no-op（需求：txt 無標籤）。

### 後續追加（2026-08-25）
- NewNoteDialog 加「瀏覽…」按鈕：建立位置可改選（set_folder/folder API + QFileDialog），換資料夾即時重新驗證；測試 test_dialog_change_folder_revalidates_and_creates_there。
- editor.py 加行號欄（_LineNumberArea，標準 QPlainTextEdit gutter 模式）：目前行高亮、寬度隨位數自適應、跟隨主題、md/txt 編輯皆有；已修正 stylesheet padding 造成的 viewport/gutter 原點偏移。全套 939 passed / 43 skipped。

### 已知事項
- test_window_close_defers_once_until_running_update_finishes 在其中一輪 full run fail 過一次、單跑與後兩輪 full run 皆過——疑似既有 timing flake，與本次改動無關聯證據。
- GUI 需人工確認項：對話框視覺樣式（light/dark）、Ctrl+N 實際按鍵、檔案樹選取目錄的實際解析、txt 編輯器字型呈現。


## 格式工具列實作紀錄（2026-08-25）

### 新增檔案
- app/format_actions.py：純函式格式邏輯（toggle_inline / toggle_heading / 清單 / 引用 / 連結 / 表格 / 分隔線 / 程式碼區塊），回傳 TextEdit(start, end, replacement, sel_start, sel_end)；apply_format_action() 以單一 QTextCursor beginEditBlock/endEditBlock 套用，Ctrl+Z 一步還原。
- app/format_toolbar.py：FormatToolbar(QWidget)，文字標籤 QToolButton（B/I/S/H1-H3/•/1./☑/>/<>/```/連結/田/—），NoFocus 不搶編輯器焦點，apply_theme() 隨主題刷新。
- tests/test_format_actions.py：純邏輯單元測試（選取/無選取/再按取消、多行、有序清單重新編號、粗斜體判別、去空白包裹、連結游標）。

### 修改檔案
- app/shortcuts.py：WINDOW_SHORTCUTS 新增 edit.bold (Ctrl+B)、edit.italic (Ctrl+I)，handler _format_bold/_format_italic。
- app/window.py：editor_pane 版面在搜尋列下方加入 _format_toolbar；_enter_edit_mode 依 plain_text 顯示/隱藏；_leave_edit_ui 隱藏；_apply_theme 刷新；編輯選單加入 粗體/斜體 command_act；新增 _format_bold/_format_italic/_apply_format_action（非 Markdown 編輯模式一律 no-op）。
- tests/test_window_integration.py：新增整合測試（bold 修改文件且一步 undo、italic 快捷 handler、預覽/.txt no-op、工具列顯示/隱藏、按鈕點擊套用）。

### 設計決策
- 星號判別：以邊界星號連續數（run length）判斷——斜體需兩側 run 為奇數、粗體需 >= 2，***bold italic*** 可正確各自解除。
- 行級 toggle：所有被選到的行都有前綴才移除，否則全部加上；有序清單加入時會重新編號既有的「N. 」前綴。
- 分隔線前後自動補空行（避免 --- 變成 setext 標題）；表格插入於游標行之後的新行，游標選取第一個標題儲存格。
- 工具列按鈕用文字標籤（theme.py 無對應 icon，遵守不新增資產檔）。

### 驗收（fresh agent 獨立複核）
- 全套 975 passed / 43 skipped；round-trip、***消歧、renumber、單步 undo、.txt/預覽 no-op、
  工具列可見性、快捷鍵唯一性、CRLF 保留探針全過。總判定：可交付。
- 三個低嚴重度備註：跨空行選取的 inline 標記 CommonMark 不渲染（UX 邊角）；
  搜尋列聚焦時 Ctrl+B 仍作用於編輯器（與既有 window-level 捷徑同模式）；
  window.py 直接讀 _plain_text_mode 私有屬性（風格）。未 commit。


## 第二批工具列+SPLIT實作紀錄（2026-08-25）

- 確認渲染管線：dollarmath + KaTeX 已啟用（$…$ / $$ 會渲染為數學式）；==x== 不會渲染，但 <mark> 在 safe-HTML 白名單內 → 醒目標示採 <mark>…</mark>。
- format_actions.py：新增 toggle_wrap（非對稱前後綴切換）、mermaid_block、math_block；compute_edit 新增 mermaid / math_inline / math_block / wikilink / highlight。
- format_toolbar.py：改為 QToolBar 子類別（窄視窗溢出按鈕收進內建 » chevron），新增 圖片 / Mermaid / $x$ / $$ / [[]] / 醒目 六顆按鈕；樣式改用 QToolBar 選擇器。
- editor.py：新增 document_path() 存取器。
- window.py：_apply_format_action 攔截 image → _insert_image_via_dialog（無檔案路徑顯示「請先儲存文件才能貼入圖片」；有路徑 QFileDialog 選圖 → import_image_file + markdown_image_link，單一 undo step）。
- window.py：_on_browser_note_created 新 .md 進 SPLIT（.txt 維持 EDIT），僅建立當下一次。
- 既有 147 個 format/window 測試通過；接著補新測試。

- 測試補齊：test_format_actions.py +17（mermaid/math/wikilink/highlight 各 selection/caret/toggle-off）；test_window_integration.py +8（工具列新按鈕存在與觸發、圖片按鈕未儲存/已儲存/undo、新 .md SPLIT、新 .txt 純編輯、既有檔維持 PREVIEW、切換後不再強制 SPLIT）。
- QToolBar 溢出驗證：窄視窗（220px）chevron 顯示、17 顆按鈕收入延伸選單；寬視窗全部可見。
- 全套測試：1000 passed, 43 skipped（基準 975+43，新增 25，無回歸）。完成。

### 驗收與修正（fresh agent 獨立複核）
- 22 個純邏輯 probe + 真 EditorView + window 層 7 probe 全過；<mark>/$math$ 渲染確認正確。
- 抓到 1 個 medium bug：math_block 未 pad 空行，緊貼段落時 $$ 不渲染成 display math
  → 指揮官已修（format_actions.py math_block 加 pad=True），並補渲染層測試
  test_math_block_next_to_text_renders_as_display_math（用 render_body 驗證 class="math block"）。
- 修後全套 1001 passed / 43 skipped。updater timing flake（close_defers）單跑與重跑皆過，既有問題。
- 低嚴重度備註：wikilink 包裹後游標在 ]] 之後，連按兩下會再插一組空 [[]]（設計取捨）。未 commit。
