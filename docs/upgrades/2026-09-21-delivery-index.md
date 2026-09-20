# 六批次逐項交付索引

驗證歷程與完整命令見 [進度紀錄](2026-09-21-optimization-progress.md)；未達標與未驗證項目見 [仍開放清單](2026-09-21-remaining-work.md)。

以下逐項測試保留各提交當時的結果；最終整合結果以 [測試基準](2026-09-21-test-baseline.md) 為準：一般 1812 passed／82 skipped，全量 WebEngine 1887 passed／7 skipped，零失敗。版號為 1.34.0；完整提交可見 [版本比較](https://github.com/wulove1029/markdown_viewer/compare/v1.33.1...v1.34.0)。

正式發布與安裝檔下載雜湊均已驗證，詳見 [發布驗證](2026-09-21-release-verification.md)。發布來源提交為 `5c0c3cb`；其後的補充提交僅修改交付文件。

## A1 LinkIndex 索引標準 Markdown 連結

- 做了什麼：加入來源相對 Markdown 連結解析，保留原有 wikilink 行為與 raw_targets 相容介面。
- 檔案：`app/links.py:152`
- 測試結果：測試：`py -3 -X utf8 -m pytest tests/test_links.py tests/test_graph_model.py tests/test_graph_view.py -q` → 29 passed in 0.52s。 / 獨立覆核發現角括號圖片及複雜 fence 假邊，已加入回歸案例並修正，複驗通過。
- CHANGELOG：- 關聯圖與反向連結索引支援 Markdown 相對連結及角括號路徑，正確處理百分比編碼與錨點，並排除圖片、外部網址及程式碼中的假連結。
- Commit: 61f02ea

## A2 重名節點 label 補上父資料夾

- 做了什麼：重名 label 逐層補父資料夾；tooltip 以最深層包含該檔的文件庫根目錄計算相對路徑。
- 檔案：`app/graph_model.py:45`
- 測試結果：測試：`py -3 -X utf8 -m pytest tests/test_links.py tests/test_graph_model.py tests/test_graph_view.py -q` → 31 passed in 0.54s。 / Fresh agent 獨立驗收：graph model/view 16 passed in 1.31s，額外檢查副檔名碰撞與巢狀文件庫通過。
- CHANGELOG：- 關聯圖的重名筆記逐層補上父資料夾以便辨識，節點提示顯示相對於文件庫的完整路徑。
- Commit: 7dfbbf0

## A3 稀疏圖依資料夾分群、圖例可切換分組維度

- 做了什麼：新增依文件庫／資料夾／標籤切換，QSettings graph/group_mode 缺省 library；沿用群組按鈕隱藏／展開，圖例水平捲動。
- 檔案：`app/graph_model.py:122`, `app/graph_view.py:550`
- 測試結果：測試：`py -3 -X utf8 -m pytest tests/test_links.py tests/test_graph_model.py tests/test_graph_view.py tests/test_tag_index.py -q` → 40 passed in 0.66s。 / Fresh agent 複驗 40 passed in 0.64s；亮暗 Qt 截圖已檢視（offscreen 無內建字型，測試程序明確載入系統 Segoe UI 與微軟正黑體）。
- CHANGELOG：- 關聯圖可切換文件庫、資料夾或標籤分群並記住選擇；同群節點集中排列，圖例可隱藏群組並水平捲動。
- Commit: d883130

## A4 共享 #tag 弱邊（可選）＋提示文字說明索引哪些連結型別

- 做了什麼：無邊提示明列 wikilink、Markdown 相對連結與排除範圍；選配共享標籤弱邊不實作。
- 檔案：`app/graph_view.py:53`
- 測試結果：`py -3 -X utf8 -m pytest tests/test_graph_view.py -q` → 7 passed in 0.49s；fresh agent 7 passed in 0.40s。
- CHANGELOG：- 關聯圖無連線提示明列已索引的 wikilink 與 Markdown 相對連結，以及不納入圖的外部網址與非 Markdown 檔案。
- Commit: 6a9acde

## A5 關聯圖測試補齊

- 做了什麼：A1–A3 已新增 12 個案例；A5 再補混合連結實際 Qt edge items 驗證。
- 檔案：`tests/test_graph_view.py:12`
- 測試結果：完整回歸（A1–A3）：`py -3 -X utf8 -m pytest tests/ -q` → 1703 passed、82 skipped、0 failed，62.20s。 / A4/A5 追加後 graph_view：8 passed in 0.43s。
- CHANGELOG：- 補齊關聯圖混合連結、路徑解析、程式碼排除、重名節點及實際 Qt 連線顯示的回歸測試。
- Commit: 6f2539d

## B1 CI 從不跑測試

- 做了什麼：新增 Windows push/PR tests job、手動 WebEngine job、JUnit artifacts；失敗不允許忽略。
- 檔案：`.github/workflows/ci.yml:1`
- 測試結果：遠端 push CI 1812 passed / 82 skipped；手動 WebEngine 93 passed。刻意失敗的 cba22ad 已驗證紅燈，探針於 26854cd 移除，未合併至 main。
- CHANGELOG：- 新增 Windows push／pull request 測試 CI，並提供手動觸發的獨立 WebEngine 測試工作。
- Commit: f596a72

## B2 `pytest.ini` 缺 `testpaths`

- 做了什麼：pytest.ini 設定 testpaths 與 norecursedirs。
- 檔案：`pytest.ini:2`
- 測試結果：`py -3 -X utf8 -m pytest --collect-only -q` → 1786 tests collected in 1.66s、0 error。 / `py -3 -X utf8 -m pytest tests/ --collect-only -q` → 1786 tests collected in 1.54s、0 error。
- CHANGELOG：- pytest 預設只收集 tests，排除暫存及封裝目錄，避免根目錄執行時出現存取拒絕的收集錯誤。
- Commit: 52fdee3

## B3 `atomic_io` 備份失敗被靜默吞掉

- 做了什麼：atomic_write_bytes/text 回傳備份警示（正常仍 None），記錄 traceback；MainWindow 成功存檔後顯示警示、不留下 dirty 狀態。
- 檔案：`app/atomic_io.py:72`
- 測試結果：`py -3 -X utf8 -m pytest tests/test_atomic_io.py tests/test_editor_data_safety.py -q` → 72 passed in 8.41s。 / Fresh agent：72 passed in 8.29s；B2 補驗兩種 collection 的 1789 個 node IDs 完全相同（B3 增加 3 個測試）。
- CHANGELOG：- 備份失敗仍保存文件，但記錄警告並在狀態列提示；原子寫入最終失敗時清理暫存檔，保留原文件。
- Commit: 636a377

## B4 77% 的 except 不記錄、logging 幾乎沒人用

- 做了什麼：AST 重測 broad Exception + pass：17 → 0。高頻 fragment_render 用 debug，其餘 warning；窄型別合理防禦保持不變。
- 檔案：`app/md_converter.py:1`, `app/render_service.py:155`
- 測試結果：`py -3 -X utf8 -m pytest tests/ -q` → 1707 passed、82 skipped，55.71s。 / `py -3 -X utf8 -m pytest tests/test_render_service.py -q` → 23 passed in 6.79s；測試實際寫入 fallback.log 並確認錯誤原因。 / Fresh agent：23 passed in 6.93s，AST 與 WYSIWYG 不重構限制確認通過。
- CHANGELOG：- 移除 17 處靜默吞掉 Exception 的處理，渲染 worker 失敗與退回本機解析會記錄原因；高頻片段渲染清理使用 debug 避免刷滿日誌。
- Commit: 9b3776e

## B5 QSettings 常數散落＋applicationName 不一致

- 做了什麼：集中 ORG/APP 與 settings()；applicationName 對齊 MarkdownViewer、顯示名稱仍 Markdown Viewer。
- 檔案：`app/settings_store.py:7`
- 測試結果：完整回歸：1709 passed、82 skipped，62.57s。Fresh agent 發現 PDF 設定漏匯入，已修正並補真實對話框取消測試。 / `py -3 -X utf8 -m pytest tests/test_settings_store.py tests/test_pdf_export.py -q` → 34 passed in 0.71s；fresh agent 34 passed in 0.64s。
- CHANGELOG：- 統一 QSettings 組織與程式名稱常數，保留原登錄路徑及既有 AppData 文件庫、標籤、復原草稿與日誌位置。
- Commit: a24640b

## B6 沒有任何 lint / type-check / format 設定

- 做了什麼：`py -3 -X utf8 -m ruff check app main.py tests tools --select I --fix` → 124 violations fixed、0 remaining，110 個檔案異動。 pyproject.toml 開啟 E/F/I；74 個既有檔案逐檔列出 E501/E402/F401/F811/F841/F541 的既有 debt（含 pytest fixture import）。新檔預設全開。
- 檔案：`pyproject.toml:1`
- 測試結果：`py -3 -X utf8 -m pytest tests/ -q` → 1710 passed、82 skipped，60.10s。 / Fresh agent：110 檔移除 import 後 AST 差異 0；Ruff I check 通過。 / `py -3 -X utf8 -m ruff check .` → All checks passed。 / `py -3 -X utf8 -m pytest tests/test_atomic_io.py tests/test_edit_backend.py -q` → 29 passed in 0.22s；import main 成功。
- CHANGELOG：- 導入 Ruff E/F/I 與逐檔既有問題清單，三個純邏輯模組納入 mypy，CI 增加 lint／型別檢查。
- Commit: 77045be

## C1 啟動時無條件建構兩個 QWebEngineView

- 驗收限制：375.23 → 357.32ms，改善 4.77%，未達 15%／150ms 門檻。
- 做了什麼：_edit_preview 預設 None，首次 SPLIT 透過 _ensure_edit_preview 建立並接線；設定／搜尋／捲動呼叫點皆保護 None。
- 檔案：`app/window.py:2179`
- 測試結果：`py -3 -X utf8 -m pytest tests/ -q` → 1711 passed、82 skipped，59.48s。 / fresh agent window/data safety：215 passed in 24.13s；追加 token/splitter 斷言 1 passed、157 deselected in 1.20s。Ruff/mypy 通過。
- CHANGELOG：- 並排編輯預覽改為首次進入 SPLIT 才建立，沿用目前縮放與搜尋狀態，後續切換重用元件。
- Commit: 2256b4a

## C2 `rename_folder` 在 GUI 執行緒同步 `os.walk`

- 做了什麼：QRunnable 背景掃描；取消於 rename 前生效，主執行緒 Slot 套用 mapping。準備中模態進度與樹狀停用避免重複 UI 操作。
- 檔案：`app/file_browser.py:665`, `app/file_ops.py:290`
- 測試結果：`py -3 -X utf8 -m pytest tests/test_file_ops.py tests/test_file_browser.py tests/test_document_relocation.py tests/test_relocation_workspace.py -q` → 82 passed in 7.26s。 / Fresh agent 複驗 50 passed in 5.82s，巢狀目錄外部新增安全中止；最終 cancel check 已補。
- CHANGELOG：- 資料夾改名改為背景準備，支援取消並檢查掃描期間的目錄異動，完成後才由主執行緒更新路徑索引。
- Commit: 15d8be1

## C3 Pygments 讓程式碼密集的小檔渲染變慢

- 驗收限制：warm 100KB 為 18.22ms；冷快取 142.79ms，未達冷開 <80ms。
- 做了什麼：lexer LRU 64 項；高亮區塊 LRU 計入 lang/code/HTML 字串的 sys.getsizeof，預算 4 MiB（不含容器與 lexer 開銷）。超長語言名稱不入快取。
- 檔案：`app/md_converter.py:133`
- 測試結果：`py -3 -X utf8 -m pytest tests/test_highlight_cache.py tests/test_md_converter_features.py tests/test_md_converter_body.py -q` → 133 passed in 0.97s；Ruff 通過。 / Fresh agent 額外 8 threads／200 次輸出逐字相同；9 cache tests passed in 0.30s，5MiB 語言名稱不被保留。
- CHANGELOG：- 重用程式碼 lexer，並以 4 MiB 字串預算快取高亮區塊，減少重複開啟或編輯時的重算且保持 HTML 相同。
- Commit: b96226d

## C4 TagIndex 每次異動整檔重寫 JSON

- 做了什麼：主視窗啟用 250ms debounce；獨立 TagIndex 預設保持同步。timer／aboutToQuit／closeEvent 強制 flush；寫入失敗保留 dirty 供重試並記 log。
- 檔案：`app/tag_index.py:22`
- 測試結果：`py -3 -X utf8 -m pytest tests/test_tag_index.py tests/test_tag_rename.py tests/test_tag_delete_merge.py -q` → 16 passed in 1.08s。 / `py -3 -X utf8 -m pytest tests/ -q` → 1727 passed、82 skipped，62.03s；Ruff 通過。 / Fresh agent：6 tag tests + 1 真實 window close test 通過，額外 failure/retry 實跑通過。
- CHANGELOG：- 主視窗標籤索引以 250ms 合併連續寫入，正常關閉立即 flush，保留同步 API 與舊 JSON 格式。
- Commit: 01a69cb

## C5 打包多帶約 4.2MB 永遠不會載入的 vditor 資產

- 決策：實跑推翻「永遠不會載入」前提，保留資源；細節見 [仍開放清單](2026-09-21-remaining-work.md)。下列 CHANGELOG 是共用資源評估紀錄（E11），並非宣稱 Mermaid／PlantUML 已裁剪。
- 做了什麼：實際 WebEngine 載入 WYSIWYG Mermaid／PlantUML 範例時，document.scripts 包含兩者資源，Mermaid 產生 SVG；與「未使用」前提不符。
- 檔案：`docs/upgrades/2026-09-21-remaining-work.md:9`
- 測試結果：評估或仍開放項目，不宣稱未執行的測試通過。
- CHANGELOG：- 實測 Office 編輯器在亮／暗外觀切換全部程式碼主題的資源請求，記錄結果並保留尚未證明可安全刪除的高亮樣式資源。
- Commit: 151b96c

## C6 QtWebEngine 語系包佔 133MB，UI 只有繁中

- 驗收限制：語系自身減少 42.07MiB，未達 100MB；乾淨鎖版環境另有減量，不能合併歸功於語系。
- 做了什麼：PyInstaller Analysis 後只過濾 qtwebengine_locales/*.pak，保留 zh-TW、zh-CN、en-US，不影響其他資源包。
- 檔案：`tools/package_policy.py:4`
- 測試結果：`py -3 -X utf8 -m pytest tests/test_package_policy.py -q` → 1 passed in 0.06s；Ruff 通過；fresh agent 額外驗證 Windows 路徑、其他 pak 與非 pak 資源均正確保留。 / 實際 PyInstaller 建置成功（裁剪後 exit 0），輸出放 TEMP，未覆蓋現有 dist： / 使用裁剪後封裝的 locales/resources 路徑實跑 QWebEngineView，loadFinished 成功，JavaScript 讀回 `Locale resource smoke OK`，exit 0。這是資源載入驗證，尚非完整安裝版人工驗收。
- CHANGELOG：- 封裝僅保留繁中、簡中與英文 WebEngine 語系，保留其他 Chromium 資源包，減少安裝目錄體積。
- Commit: 9650100

## C7 未啟用 UPX

- 做了什麼：本機未找到 UPX；無可重現的 SmartScreen／防毒誤判驗證結果。維持 EXE 與 COLLECT 的 upx=False，不將未驗證的壓縮帶入發版，也不宣稱已測出誤判。
- 檔案：`markdown_viewer.spec:66`
- 測試結果：評估或仍開放項目，不宣稱未執行的測試通過。
- CHANGELOG：- 記錄封裝壓縮與歷史產物清理評估；維持停用 UPX，歷史產物因工具政策限制保留。
- Commit: 5ffa170

## C8 `dist/` 堆積歷史打包產物

- 做了什麼：五份指定產物已確認位於 D:\markdown_viewer\dist 直屬子目錄，無 reparse point；.gitignore:12 已涵蓋 /dist/。刪除遭工具自動審核政策拒絕，全部保留，未繞過限制。
- 檔案：`docs/upgrades/2026-09-21-remaining-work.md:12`
- 測試結果：評估或仍開放項目，不宣稱未執行的測試通過。
- CHANGELOG：- 記錄封裝壓縮與歷史產物清理評估；維持停用 UPX，歷史產物因工具政策限制保留。
- Commit: 5ffa170

## D1 wikilink 改名後其他筆記的連結全部失效

- 做了什麼：改名當下以完整可讀的 scoped files 重建 LinkIndex，確認對話框列出所有即將改寫的文件；取消不落盤，未接索引服務明確拒絕。
- 檔案：`app/backlink_rename.py:13`, `app/file_ops.py:164`
- 測試結果：`py -3 -X utf8 -m pytest tests/test_backlink_rename.py -q` → 21 passed in 0.86s。 / `py -3 -X utf8 -m pytest tests/ -q` → 1753 passed、82 skipped in 61.24s；Ruff 通過。 / Fresh agent 找出並複驗修正未閉合／跳脫反引號及特殊檔名案例；額外驗證五種草稿防護、乾淨 buffer 同步及發布失敗回滾通過。 / 完整套件首次停在舊的獨立 browser 測試之缺少索引服務警告；更新該測試接入真實準備器後全套通過，未降低正式程式的安全門檻。
- CHANGELOG：- 筆記改名會確認受影響的 wikilink 文件清單，保留原編碼與無關位元組，並將連結改寫、原檔及 sidecar 搬移納入可回滾交易。
- Commit: 98d95c2

## D2 匯出完成回饋不一致

- 做了什麼：export_actions.show_export_complete 統一四種格式成功回饋；開啟檔案／所在資料夾皆使用本機 QUrl，關閉不啟動外部程式。
- 檔案：`app/export_actions.py:502`
- 測試結果：`py -3 -X utf8 -m pytest tests/test_export_completion.py tests/test_pdf_export.py tests/test_editor_data_safety.py -q` → 92 passed in 8.84s。 / `RUN_WEBENGINE_TESTS=1 QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu py -3 -X utf8 -m pytest tests/test_pdf_export_webengine.py -q` → 1 passed in 7.08s。 / Ruff 通過。Fresh agent 複驗 92 passed in 8.75s，確認四種成功路徑、取消／失敗提前返回與中文空白路徑。
- CHANGELOG：- Word、PPT、HTML 與 PDF 匯出成功後共用完成對話框，提供開啟檔案及所在資料夾入口。
- Commit: 1ca9d50

## D3 外部檔案衝突只能二選一，沒有比較差異／保留雙方

- 做了什麼：有未儲存 QTextDocument 的外部變更對話框加入唯讀 unified diff、保留雙方、覆寫、捨棄與稍後處理；WYSIWYG 仍先完成原 snapshot gate。
- 檔案：`app/external_conflicts.py:18`
- 測試結果：`py -3 -X utf8 -m pytest tests/test_external_conflicts.py tests/test_editor_data_safety.py tests/test_window_integration.py -q` → 223 passed in 23.33s；最終失敗保留策略追加重跑 7 passed in 0.96s；Ruff 通過。 / `RUN_WEBENGINE_TESTS=1 QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu py -3 -X utf8 -m pytest tests/test_wysiwyg_webengine.py -q` → 27 passed in 34.94s。 / Fresh agent 7 passed in 0.94s；實跑關閉後外部替換競態，確認失敗處理不誤刪外部檔案。
- CHANGELOG：- 外部版本與本機編輯衝突時可查看唯讀差異或保留雙方；本機副本以排他建立避免覆寫既有檔案，儲存失敗保留草稿及待檢查副本。
- Commit: 159aab7

## D4 PDF 螢光標記無法就地編輯、卡片沒有可見的「…」選單

- 做了什麼：PdfAnnotationCard 右上「…」與右鍵共用 _build_menu，沿用作者及唯讀限制；編輯對話框確認 entry identity，避免切換文件後改錯註解。
- 檔案：`app/pdf_annotation_card.py:301`, `app/pdf_highlights_panel.py:45`
- 測試結果：`py -3 -X utf8 -m pytest tests/test_pdf_markup_editing.py tests/test_pdf_highlights.py tests/test_pdf_embedded_annotations.py -q` → 111 passed、5 skipped in 4.24s；Ruff 通過。 / Fresh agent 4 passed in 0.62s，驗證舊 JSON、幾何與標籤保留、writer 失敗不改 model 及路徑切換接線。
- CHANGELOG：- PDF 註解卡片新增可見「…」選單；螢光面板可就地編輯標記文字，成功保存後才更新畫面，切換文件會取消舊編輯情境。
- Commit: fe15b0c

## D5 沒有 Front matter／屬性 GUI 面板

- 做了什麼：右側 Properties dock 由「檢視 → 屬性」開啟，key/value 表格可新增及編輯，明確按儲存才寫檔；關閉面板不改既有唯讀 front matter 顯示。
- 檔案：`app/frontmatter_properties.py:23`, `app/properties_panel.py:22`
- 測試結果：`py -3 -X utf8 -m pytest tests/ -q` → 1785 passed、82 skipped in 61.78s；最終尾端註解修正 `tests/test_frontmatter_properties.py tests/test_properties_panel.py` → 24 passed in 0.99s；Ruff 通過。 / Fresh agent 24 passed in 0.95s，額外 nested block scalar + 尾端註解驗證通過。Qt offscreen 實跑截圖已查看，表格與新增／儲存按鈕可見，截圖在 TEMP/mdv-properties-panel.png。
- CHANGELOG：- 檢視選單新增右側屬性面板，可新增或修改 YAML front matter，保留本文位元組及未修改欄位；不合法 YAML、草稿或外部異動會阻止儲存。
- Commit: c3e5b55

## D6 其餘已知限制與缺口（仍開放，依需要排入）

- 做了什麼：原需求 D6 為「仍開放，依需要排入」清單，本輪不擴充 PDF 本文編輯、i18n、雲端同步等大型能力。相關既有限制維持追蹤；F6 補程式碼反向連結，不把它們宣稱為已實作。
- 檔案：`docs/upgrades/2026-09-21-remaining-work.md:13`
- 測試結果：評估或仍開放項目，不宣稱未執行的測試通過。
- CHANGELOG：- 關鍵交易、PDF 與甘特圖程式碼加入可追溯的待辦連結，集中列出未達效能門檻、未驗證平台情境及仍開放需求。
- Commit: 4fcdf25

## E1 拆解 `window.py` god-object

- 做了什麼：6 個方法抽至 translation_flow，window 保留原名稱 wrapper；window.py 6862 → 6796 行（以換行符計）。 10 個標籤方法與 merged_tag_rows 抽至 tag_flow；保留 window 掛鉤及 helper re-export，sidecar／索引格式不變。window.py 6796 → 6581 行。 34 個 PDF 操作方法抽至 pdf_flow，模組說明 window 共享狀態、UI 與回呼契約；保存仍用既有 Store/writer，未改 WYSIWYG 同步。window.py 6581 → 6293 行；三群累計 6862 → 6293（減少 569 行）。
- 檔案：`app/translation_flow.py:19`, `app/tag_flow.py:14`, `app/pdf_flow.py:26`
- 測試結果：`py -3 -X utf8 -m pytest tests/ -q` → 1790 passed、82 skipped in 61.31s；Ruff/mypy 通過。 / `RUN_WEBENGINE_TESTS=1 QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu py -3 -X utf8 -m pytest tests/test_translate_menus.py -q` → 9 passed in 0.60s。 / Fresh agent 比對 6 個 body AST、簽章及 wrapper 參數完全一致；獨立 63 passed in 1.05s。 / `py -3 -X utf8 -m pytest tests/ -q` → 1790 passed、82 skipped in 60.69s；Ruff/mypy 通過。
- CHANGELOG：- 翻譯操作抽至 translation_flow，保留視窗薄掛鉤、快取與過期回應防護，未改動編輯器同步機制。 / - 標籤面板與文件標籤 CRUD 抽至 tag_flow，保留既有視窗掛鉤、索引與 sidecar 格式。 / - PDF 開啟、註記、螢光與內嵌註解操作抽至 pdf_flow，明列視窗契約並沿用既有保存層與回呼。
- Commit: f5de081 08b3577 0008304

## E2 `_tab_state` 沒有 schema

- 做了什麼：新增 TabState(TypedDict, total=False) 的 17 個可選欄位，_tab_state 及接收分頁狀態的方法使用該契約；仍為相同 runtime dict，不改 session/sidecar 格式。
- 檔案：`app/tab_state.py:11`
- 測試結果：`py -3 -X utf8 -m mypy` → 5 files 通過；Ruff 通過。 / `py -3 -X utf8 -m pytest tests/ -q` → 1788 passed、82 skipped in 61.07s。
- CHANGELOG：- 分頁狀態新增 17 個可選欄位的 TypedDict 契約，保留原字典及舊工作階段行為，mypy 納入 schema 與視窗的欄位名稱檢查。
- Commit: 2bdaf51

## E3 外部模組直接讀寫 window 私有屬性 160 次

- 做了什麼：MainWindow 九個 property 保留原私有欄位；session_state/export_actions 75 個存取改用公開名稱，update_flow 目前無指定九欄位，查證後不需修改。測試替身同步契約。
- 檔案：`app/window.py:674`, `app/session_state.py:256`
- 測試結果：`py -3 -X utf8 -m pytest tests/ -q` → 1790 passed、82 skipped in 60.24s；Ruff/mypy 通過。 / `RUN_WEBENGINE_TESTS=1 QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu py -3 -X utf8 -m pytest tests/test_pdf_export_webengine.py -q` → 1 passed in 6.99s。 / Fresh agent 機械替換 AST 正確，九組 getter/setter 實跑通過，session/export 48 passed in 6.78s。
- CHANGELOG：- 視窗提供九個共用狀態 property，工作階段與匯出流程改用公開契約，保留既有私有狀態與行為。
- Commit: 9289873

## E4 `_pymupdf()` lazy loader 三份逐字複製

- 做了什麼：三份 _pymupdf 改用同一 lazy loader／Lock／快取；原模組 alias 仍可局部 monkeypatch。原 unavailable 測試改 patch alias，避免依赖已移除的私有 cache。
- 檔案：`app/pymupdf_loader.py:11`
- 測試結果：PDF embedded/password/reading 121 passed、5 skipped in 5.98s；loader 新測試 2 passed in 0.22s；Ruff 通過。 / Fresh agent 104 passed、5 skipped in 5.02s；新程序驗證 import 未提前載入 PyMuPDF，8 threads／32 次呼叫取得同一 module。
- CHANGELOG：- PDF 目錄、內嵌註解讀取與寫入共用具執行緒保護的 PyMuPDF 延遲載入快取，保留缺少套件時的退化行為。
- Commit: 9fe342e

## E5 QMenu QSS 在 6 處各寫一份

- 做了什麼：六處 QMenu 改用 theme.menu_stylesheet，採 recent_files 完整樣式（selected／disabled／separator）。
- 檔案：`app/theme.py:291`
- 測試結果：`py -3 -X utf8 -m pytest tests/test_combo_popup_theme.py tests/test_settings_dialog_theme.py tests/test_mermaid_workspace_theme.py tests/test_recent_files.py tests/test_pdf_markup_editing.py -q` → 18 passed in 1.00s；Ruff 通過。 / Fresh agent 確認移除範圍及兩張截圖，獨立 18 passed in 0.98s。
- CHANGELOG：- 六處右鍵選單共用完整亮／暗主題樣式，統一停用項目、選取背景與分隔線。
- Commit: 73acfee

## E6 7 個超過 100 行的方法

- 做了什麼：純建構依功能抽出 file menu、format menu、toolbar controls/title 四個具名 helper；保留既有 format commands 資料表，主 menu 165 → 95 行、toolbar 110 → 29 行。未動 WYSIWYG 與需先補分支測試的資料安全函式。
- 檔案：`app/window.py:754`, `app/window.py:1126`
- 測試結果：window integration／shortcuts／toolbar／combo 主題 168 passed in 15.13s；Ruff/mypy 通過。 / Fresh agent 將 helper inline 後兩個 builder AST 與 HEAD 完全一致，其他原方法 AST 均未變；Qt 整合 13 passed in 1.95s。
- CHANGELOG：- 選單與工具列建構拆為具名功能群，主建構方法縮至 95／29 行，保持動作順序、快捷鍵及編輯同步行為。
- Commit: 4cca7ab

## E7 requirements 全為 `>=` 無上限、無 lock；dev 只有 pytest

- 做了什麼：乾淨 TEMP/mdv-release-venv（Python 3.13.5）安裝 runtime/dev 清單，產生 requirements.lock 的 38 個精確版本，含所有間接相依；補 pytest-qt/cov/Pillow/PyInstaller。CI/release 共用 lock，PY_PYTHON3=3.13 並印出/斷言實際版本，各 native 步驟失敗立即退出。
- 檔案：`requirements.lock:1`, `.github/workflows/release.yml:11`
- 測試結果：隔離環境 pip check／Ruff／mypy 通過；`py -3 -X utf8 $env:TEMP\mdv-venv.py -m pytest tests/ -q` → 1792 passed、82 skipped in 67.12s。TEMP runner 只以隔離環境 python.exe -X utf8 轉交參數，未混入全域 site-packages。
- CHANGELOG：- 新增乾淨 Python 3.13 環境產生的固定依賴清單，CI／發版共用並檢查實際 Python 版本，補齊測試與打包開發工具。
- Commit: 100c3f4

## E8 13 個 app 模組在 tests/ 完全沒被引用

- 做了什麼：四個優先模組新增 15 項直接測試：甘特圖模型獨立狀態與查找、Markdown 真解析的資源路徑／標籤 round-trip、獨立程序 QApplication 前 scheme 註冊、片段渲染重試／失敗快取／跳脫／watchdog 清理。 重現 add/add/remove-first/add 後新任務 task_id=task2 且 start=after task2。新增 allocator 同時避開既有 Mermaid ID 與 after 參照；新任務為 task3，不把尚未修正的舊 after task1 重新指向新物件。原刪除造成的失效參照維持可見，不擅改使用者排程。
- 檔案：`tests/test_gantt_model.py:6`, `tests/test_fragment_render.py:18`, `tests/test_resource_links.py:9`, `tests/test_url_schemes.py:5`
- 測試結果：隔離 lock 環境 15 passed in 0.72s；Ruff 通過。Fresh agent 15 passed in 0.70s。 / E7 PyInstaller 已成功 exit 0（122.866s），乾淨鎖版封裝 627,589,065 bytes，僅三個 WebEngine locale。相較全域環境語系精簡後 1,001,996,347 bytes 減少 374,407,282 bytes；相較最初全域封裝 1,046,108,202 bytes 減少 418,519,137 bytes。不同相依環境的整體差異，不能全歸因 locale。 / model／Mermaid round-trip 7 passed in 0.12s；Ruff 通過。Fresh agent 4 passed in 0.08s，原反例、自訂 ID、多 after 參照實跑通過。
- CHANGELOG：- 補齊甘特圖模型、資源連結編解碼、啟動 URL scheme 與片段渲染失敗／重試／逾時清理的直接測試。
- Commit: 5c2c25e

## E9 release 把整份 32KB CHANGELOG 當 release body

- 做了什麼：tools/write_release_notes.py 讀取當版 VERSION／RELEASE_NOTES，UTF-8 輸出至 runner TEMP；workflow body_path 指向相同檔案，不再附歷史CHANGELOG。CLI版號不符會非零退出。
- 檔案：`tools/write_release_notes.py:8`
- 測試結果：`tests/test_release_notes.py` 2 passed in 0.34s；Ruff 通過。Fresh agent 真CLI中文readback與2 tests（0.24s）通過。
- CHANGELOG：- GitHub Release 僅發布當版 RELEASE_NOTES，並驗證 tag 與程式版號一致，不再附上整份歷史 CHANGELOG。
- Commit: f97be4f

## E10 兩處重複的小邏輯

- 做了什麼：先比對兩份 dirnames 篩選 AST 完全相同，再抽共用 prune_directory_names；保留 root相對路徑、順序與 in-place 語意。檔案樹／recent Explorer 共用 file_ops.show_in_explorer(check=False)，recent 既有存在檢查不變。
- 檔案：`app/document_libraries.py:280`, `app/file_ops.py:32`
- 測試結果：library/search/fileops/recent/browser 70 passed in 7.06s；新helper 2 passed in 0.27s；Ruff 通過。 / Fresh agent AST 與 import檢查通過，22 passed in 0.68s。
- CHANGELOG：- 文件庫與全文搜尋共用目錄排除處理，檔案樹與最近文件共用檔案總管定位，保留 Explorer 成功回傳 1 的行為。
- Commit: f5a7f06

## E11 76 個 highlight.js 主題 CSS（1.1MB）全數打包

- 做了什麼：真實 WysiwygView／Chromium request interceptor，載入 Python code fence 並啟用 CodeMirror 區塊，light/dark 各遍歷 11 個 _CODE_THEMES，共 22 次；每次 readback data-cm-theme 等於指定值。exit 0，6 requests（HTML、index.css、index.min.js、glue、zh_TW、lute），highlight.js/styles 請求為 0。完整紀錄 TEMP/mdv-e11-theme-requests.json。
- 檔案：`docs/upgrades/2026-09-21-optimization-progress.md:369`
- 測試結果：真實 WysiwygView／Chromium request interceptor，載入 Python code fence 並啟用 CodeMirror 區塊，light/dark 各遍歷 11 個 _CODE_THEMES，共 22 次；每次 readback data-cm-theme 等於指定值。exit 0，6 requests（HTML、index.css、index.min.js、glue、zh_TW、lute），highlight.js/styles 請求為 0。完整紀錄 TEMP/mdv-e11-theme-requests.json。
- CHANGELOG：- 實測 Office 編輯器在亮／暗外觀切換全部程式碼主題的資源請求，記錄結果並保留尚未證明可安全刪除的高亮樣式資源。
- Commit: 151b96c

## F1 CHANGELOG 缺 `## [1.31.0]` 標題

- 做了什麼：CHANGELOG 補回 [1.31.0] - 2026-09-08；日期以 v1.31.0 tag 的 commit日期確認。腳本比對補標題後該段本文完整存在於原HEAD、未改動，exit0。
- 檔案：`CHANGELOG.md:101`
- 測試結果：CHANGELOG 補回 [1.31.0] - 2026-09-08；日期以 v1.31.0 tag 的 commit日期確認。腳本比對補標題後該段本文完整存在於原HEAD、未改動，exit0。
- CHANGELOG：- 補回 1.31.0 的發布標題與日期，保留該版既有條目內容。
- Commit: 3307c87

## F2 `docs/audits/2026-09-08-*.md` 未回頭標記已修

- 做了什麼：兩份2026-09-08 audit標頭與各P1章節回填已解決版本；v1.31.0資料／搜尋／設定、v1.32.0首頁／更新，長文完整解析、高DPI人工驗證等保留開放。R4標本輪Unreleased。回填v1.27歷史拖曳不能Undo的限制目前已不成立，引用既有真實WebEngine測試。
- 檔案：`docs/audits/2026-09-08-reliability.md:3`, `docs/audits/2026-09-08-consumer-experience-audit.md:5`
- 測試結果：Fresh agent對照CHANGELOG與tag、檢查兩份audit連結存在，驗收通過。
- CHANGELOG：- 回填 2026-09-08 可靠性與操作盤點的已修版本及仍開放限制，避免把歷史缺陷誤當現況。 / - 歷史限制（2026-09-21 回填：目前已不成立）：以把手拖曳搬移區塊曾無法用 `Ctrl+Z` 復原；目前由 `test_exact_block_drag_via_qwindow_and_ctrl_z_restores_document` 真實 WebEngine 測試驗證可復原。
- Commit: ed15ea1

## F3 測試數基準重建

- 做了什麼：修正 WebEngine 測試視窗生命週期後重跑全套，記錄本機、遠端與封裝證據。
- 檔案：`docs/upgrades/2026-09-21-test-baseline.md:1`
- 測試結果：default 1812 passed / 82 skipped / 61.16s; RUN_WEBENGINE_TESTS=1 1887 passed / 7 skipped / 436.86s; collection 1894 / 0 errors. 遠端一般與 WebEngine CI 均成功。
- CHANGELOG：- 重建預設與全量 WebEngine 測試基準，記錄確切環境、跳過原因、遠端 CI 與封裝證據，不再沿用無法對照的歷史測試數。
- Commit: 4c1226a

## F4 README／提示文字說明關聯圖索引哪些連結型別

- 做了什麼：README 關聯圖段落同步程式提示原句，另說明相對路徑、百分比編碼／錨點、code/image排除、重名與分群。實際 import graph_view._EMPTY_EDGE_HINT 並斷言索引範圍原文出現在README，exit0。
- 檔案：`README.md:39`
- 測試結果：README 關聯圖段落同步程式提示原句，另說明相對路徑、百分比編碼／錨點、code/image排除、重名與分群。實際 import graph_view._EMPTY_EDGE_HINT 並斷言索引範圍原文出現在README，exit0。
- CHANGELOG：- README 補上關聯圖支援的連結、排除範圍、重名路徑與分群方式，與程式提示一致。
- Commit: ed6f6e8

## F5 `DEVELOPMENT.md` 的「專案結構」嚴重過時：只列 9 個 app 模組（version/window/ribbon/left_panel/file_browser/recent_files/toc/renderer/md_converter），實際 104 個，缺 PDF 整個子系統（20+ 檔）、WYSIWYG、標籤、關聯圖、復原、更新、匯出；`assets/` 只列 `obsidian-light.css`，實際還有 21MB 的 vditor/katex/mermaid

- 做了什麼：DEVELOPMENT以啟動、編輯、PDF、復原、標籤、圖譜、匯出／更新、離線assets等功能分群，取代9模組過時tree；同步push CI與tag發版閘門／當版release notes。
- 檔案：`DEVELOPMENT.md:166`
- 測試結果：Fresh agent核對55個代表路徑存在，QTextDocument唯一真值說明與workflow一致，文件驗收通過。
- CHANGELOG：- 開發指南改以功能分群描述模組與離線資源，補上視窗委派契約及實際 CI／發布步驟。
- Commit: 8d37449

## F6 技術債完全靠外部文件追蹤、程式碼裡零 TODO/FIXME 反向連結：`grep -rn "TODO\|FIXME\|XXX\|HACK" app/ main.py tools/` 為 0 筆，技術債記在 `docs/CLAUDE-CODE-NEXT-OPTIMIZATIONS.md`（15,982 bytes）與 `docs/upgrades/` 34 份、`docs/audits/` 5 份，但沒有機制保證文件裡的待辦對應的程式碼位置還在

- 做了什麼：新增 remaining-work 清單，保留C1/C3未達標、C6語系實際減量、資源／UPX評估、C8政策拒絕、D6選配及實體補驗步驟。4個程式檔共5條TODO反向連結，AST腳本確認與HEAD完全相同、路徑存在、Ruff通過。
- 檔案：`docs/upgrades/2026-09-21-remaining-work.md:1`
- 測試結果：新增 remaining-work 清單，保留C1/C3未達標、C6語系實際減量、資源／UPX評估、C8政策拒絕、D6選配及實體補驗步驟。4個程式檔共5條TODO反向連結，AST腳本確認與HEAD完全相同、路徑存在、Ruff通過。 / Fresh agent readback與AST驗收通過；E11檔案數249CSS+2images/335039bytes再次核對相同。
- CHANGELOG：- 關鍵交易、PDF 與甘特圖程式碼加入可追溯的待辦連結，集中列出未達效能門檻、未驗證平台情境及仍開放需求。
- Commit: 4fcdf25

## Commits

```text
61f02ea Fix graph indexing of relative Markdown links
7dfbbf0 Disambiguate graph labels and show library relative tooltips
d883130 Add persistent folder and tag grouping to the graph
6a9acde Explain graph link indexing in the empty state
6f2539d Verify mixed graph links in Qt and record regression evidence
f596a72 Add Windows CI and a manual WebEngine test job
52fdee3 Restrict default pytest collection to tests
636a377 Report backup failures and clean failed atomic writes
9b3776e Log previously silent failures and render fallbacks
a24640b Centralize settings identity while preserving legacy data paths
a5a9e1c Include all opt-in WebEngine modules in manual CI
aee2f61 Sort Python imports in an isolated formatting change
77045be Add gradual Ruff and mypy gates with explicit per-file legacy ignores
2256b4a Create the split editor preview only when first requested
15d8be1 Prepare folder renames asynchronously with cancellation and race checks
b96226d Cache reusable lexers and bounded code highlighting output
01a69cb Debounce tag index writes and flush on shutdown
9650100 Limit packaged WebEngine locales to Chinese and English
5ffa170 Record UPX assessment and blocked historical build cleanup
1ca9d50 Unify export completion dialogs with file and folder actions
98d95c2 Update wiki backlinks through a confirmed rollback-safe rename transaction
159aab7 Add read-only conflict comparison and preserve-both local copies
fe15b0c Expose PDF annotation menus and editable highlight text
c3e5b55 Add conservative front matter properties editing with byte-preserved bodies
e580d74 Verify background rename responsiveness without cold Qt timing assumptions
2bdaf51 Define optional tab state schema and enable gradual window key checking
f5de081 Extract translation orchestration behind stable window hooks
08b3577 Extract tag panel and document tag operations behind stable window hooks
0008304 Extract PDF orchestration with an explicit window contract
9289873 Expose shared window properties for session and export workflows
9fe342e Share one thread-safe lazy PyMuPDF loader
73acfee Unify context menu styles for light and dark themes
4cca7ab Split menu and toolbar construction into named groups
ba42472 Separate UI responsiveness assertions from filesystem completion deadlines
100c3f4 Lock CI and release dependencies on Python 3.13
5c2c25e Cover Gantt models resource links startup schemes and fragment failures
3c48058 Avoid duplicate Gantt task identifiers after deletion
f97be4f Publish only the current version release notes
f5a7f06 Share directory pruning and Explorer selection helpers
151b96c Record real code-theme resource requests before asset pruning
3307c87 Restore the missing 1.31.0 changelog heading
ed15ea1 Annotate historical audits with resolved versions and open limitations
ed6f6e8 Document graph link indexing and grouping behavior
603b689 Dispose WebEngine test views on the GUI thread
8d37449 Refresh architecture groups and development release workflow
4fcdf25 Link remaining technical debt from the relevant code
4c1226a Record final default and full WebEngine validation baselines
```

清單截至本報告產生時；發版提交與 tag 以 GitHub 版本比較為準。
