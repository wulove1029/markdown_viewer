# 2026-09-21 六批次優化進度

需求來源：2026-09-20 Markdown Viewer 優化開發需求。
基準 commit：7fa7d2c；開始時 git status --short 為空。

依序執行 A → B → C → D → E（E2 先於 E1）→ F。
每項分別測試、更新 CHANGELOG、commit；未通過的驗收明列，不視為完成。
WYSIWYG 區段不重構，維持 QTextDocument 為唯一真值。

## A1

加入來源相對 Markdown 連結解析，保留原有 wikilink 行為與 raw_targets 相容介面。
測試：`py -3 -X utf8 -m pytest tests/test_links.py tests/test_graph_model.py tests/test_graph_view.py -q` → 29 passed in 0.52s。
1000 檔（每檔一條 Markdown、一條 wiki）、10 次 build + graph：1000 節點、2000 邊，p50 140.37ms、max 144.87ms（不含磁碟讀取）。
獨立覆核發現角括號圖片及複雜 fence 假邊，已加入回歸案例並修正，複驗通過。
與需求做法差異：wikilink 沿用 mask_markdown_code；Markdown 交由 CommonMark parser 排除程式碼，因既有 masker 無法保留 tilde／不同長度 fence 語意。
初次未設定 applicationName 的查找讀到空的 DocumentLibraryStore；歷史記載 E:\\Puritygo 不存在。正確文件庫位置及驗證見 A2、A3。

## 待辦

B1–B6、C1–C8、D1–D5、E1–E11、F1–F6。
D6 為仍開放的候選清單，依需求文件保留追蹤。
選配共享標籤弱邊暫不納入。

A1 fresh agent 複驗通過：25 passed in 0.33s，額外 9 種語法情境通過，混合文件庫 4 節點／3 邊。

## A2

重名 label 逐層補父資料夾；tooltip 以最深層包含該檔的文件庫根目錄計算相對路徑。
測試：`py -3 -X utf8 -m pytest tests/test_links.py tests/test_graph_model.py tests/test_graph_view.py -q` → 31 passed in 0.54s。
Fresh agent 獨立驗收：graph model/view 16 passed in 1.31s，額外檢查副檔名碰撞與巢狀文件庫通過。

更正 A1 查找：必須設定正式 QCoreApplication 組織與名稱才會讀到實際 AppData。設定指向 D:\Puritygo，確實存在。
唯讀實測 D:\Puritygo：84 檔、84 節點、29 條 markdown 邊，索引讀取加建圖 1.447s；真實節點標籤重複數 0。
README 範例：air_quality/README、app_flutter/README、docs/README。UI 與完整回歸結果見 A3、A5。

## A3

新增依文件庫／資料夾／標籤切換，QSettings graph/group_mode 缺省 library；沿用群組按鈕隱藏／展開，圖例水平捲動。
標籤使用本次文件索引及註解標籤，排除快取中已過期的 front/body tags；多標籤顯示排序後組合。
測試：`py -3 -X utf8 -m pytest tests/test_links.py tests/test_graph_model.py tests/test_graph_view.py tests/test_tag_index.py -q` → 40 passed in 0.66s。
含 18 節點／3 群 180 次 layout_step 的群內平均距離 < 跨群 50% 斷言、舊設定與重開保存、過期標籤案例。
Fresh agent 複驗 40 passed in 0.64s；亮暗 Qt 截圖已檢視（offscreen 無內建字型，測試程序明確載入系統 Segoe UI 與微軟正黑體）。
Puritygo Qt GraphWindow 實跑 84 node items、29 edge items、18 folder groups；不修改文件庫。


## A4

無邊提示明列 wikilink、Markdown 相對連結與排除範圍；選配共享標籤弱邊不實作。
`py -3 -X utf8 -m pytest tests/test_graph_view.py -q` → 7 passed in 0.49s；fresh agent 7 passed in 0.40s。

## A5

A1–A3 已新增 12 個案例；A5 再補混合連結實際 Qt edge items 驗證。
完整回歸（A1–A3）：`py -3 -X utf8 -m pytest tests/ -q` → 1703 passed、82 skipped、0 failed，62.20s。
A4/A5 追加後 graph_view：8 passed in 0.43s。
Puritygo 真實資料及 UI：84 節點、29 條邊、0 個重複真實標籤；見 A2、A3。
需求列 1773 個測試為原始基準，本次全套 1785（增加 12）；追加 A5 後為 1786。
本批不涉及 WebEngine 渲染實作；Qt 原生 graph offscreen 實跑已完成。

## B1（本機檢查通過，遠端待授權）

新增 Windows push/PR tests job、手動 WebEngine job、JUnit artifacts；失敗不允許忽略。
PyYAML BaseLoader 實際解析與觸發器／runner／job 斷言通過；fresh agent 獨立檢查通過。
GitHub runner 綠燈與故意失敗紅燈尚未執行，已依全域規則 R3 詢問推送驗證分支授權。
A5 fresh agent 補驗：graph_view 8 passed in 0.43s，進度文件 read-back 通過。

## B2

pytest.ini 設定 testpaths 與 norecursedirs。
`py -3 -X utf8 -m pytest --collect-only -q` → 1786 tests collected in 1.66s、0 error。
`py -3 -X utf8 -m pytest tests/ --collect-only -q` → 1786 tests collected in 1.54s、0 error。
兩者收集數一致。

## B3

atomic_write_bytes/text 回傳備份警示（正常仍 None），記錄 traceback；MainWindow 成功存檔後顯示警示、不留下 dirty 狀態。
finally 清理暫存檔；清理若失敗記 log 且不遮蔽原始寫入錯誤；既有 Windows replace 重試保持不變。
`py -3 -X utf8 -m pytest tests/test_atomic_io.py tests/test_editor_data_safety.py -q` → 72 passed in 8.41s。
Fresh agent：72 passed in 8.29s；B2 補驗兩種 collection 的 1789 個 node IDs 完全相同（B3 增加 3 個測試）。

## B4

AST 重測 broad Exception + pass：17 → 0。高頻 fragment_render 用 debug，其餘 warning；窄型別合理防禦保持不變。
需求所述 fallback 實際入口位於 md_converter._try_remote_body；同時記錄 transport failure 及本機 fallback。
`py -3 -X utf8 -m pytest tests/ -q` → 1707 passed、82 skipped，55.71s。
`py -3 -X utf8 -m pytest tests/test_render_service.py -q` → 23 passed in 6.79s；測試實際寫入 fallback.log 並確認錯誤原因。
Fresh agent：23 passed in 6.93s，AST 與 WYSIWYG 不重構限制確認通過。
RUN_WEBENGINE_TESTS=1 的四個 *_webengine.py 正在執行，結果後補；實際此 glob 為 4 檔，非需求記載的 7 檔。

## B5

集中 ORG/APP 與 settings()；applicationName 對齊 MarkdownViewer、顯示名稱仍 Markdown Viewer。
額外查證：AppDataLocation 受 applicationName 影響，故使用 legacy_data_path 保留 5 類舊資料位置；不能只改名稱。
完整回歸：1709 passed、82 skipped，62.57s。Fresh agent 發現 PDF 設定漏匯入，已修正並補真實對話框取消測試。
`py -3 -X utf8 -m pytest tests/test_settings_store.py tests/test_pdf_export.py -q` → 34 passed in 0.71s；fresh agent 34 passed in 0.64s。
獨立驗證舊／新 applicationName 的文件庫、標籤索引、標籤顏色、復原、logs 五個實際路徑完全一致。
設定保存重開以隔離 INI 實跑，不修改使用者登錄偏好；正式 QSettings() 與 settings() 的 fileName 一致。

## WebEngine 驗證備註

首輪四模組同程序出現 Chromium GPU context lost 後卡住，已中止（exit 1），不計通過。
後續改用 QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu 並逐模組驗證。
全文搜尋發現 7 個模組使用 RUN_WEBENGINE_TESTS，但只有 4 個檔名含 webengine；CI 手動 job 應明列全部 7 個。

停用 GPU 後 inline_edit_webengine：9 passed in 43.81s；其餘六模組繼續驗證。

## B6 第一階段：獨立 import 排序

`py -3 -X utf8 -m ruff check app main.py tests tools --select I --fix` → 124 violations fixed、0 remaining，110 個檔案異動。
`py -3 -X utf8 -m pytest tests/ -q` → 1710 passed、82 skipped，60.10s。
僅 import 排序，與 lint/type 規則導入分開 commit。

Fresh agent：110 檔移除 import 後 AST 差異 0；Ruff I check 通過。

## B6 第二階段：漸進品質閘門

pyproject.toml 開啟 E/F/I；74 個既有檔案逐檔列出 E501/E402/F401/F811/F841/F541 的既有 debt（含 pytest fixture import）。新檔預設全開。
MainWindow 型別使用 TYPE_CHECKING 匯入；ruff/mypy 加入 dev requirements 及 CI。
`py -3 -X utf8 -m ruff check .` → All checks passed。
`py -3 -X utf8 -m mypy` → Success: no issues found in 3 source files（atomic_io/file_types/edit_backend）。

`py -3 -X utf8 -m pytest tests/test_atomic_io.py tests/test_edit_backend.py -q` → 29 passed in 0.22s；import main 成功。
Fresh agent 確認 74 個 ignore 均為既有確切檔名，新檔 F821 確實攔截；ruff/mypy 獨立執行通過。

## C1（功能通過，效能門檻未達）

_edit_preview 預設 None，首次 SPLIT 透過 _ensure_edit_preview 建立並接線；設定／搜尋／捲動呼叫點皆保護 None。
`py -3 -X utf8 -m pytest tests/ -q` → 1711 passed、82 skipped，59.48s。
fresh agent window/data safety：215 passed in 24.13s；追加 token/splitter 斷言 1 passed、157 deselected in 1.20s。Ruff/mypy 通過。
修改前後各 10 次 subprocess（offscreen、disable-gpu、隔離設定）p50：

| 量測 | 前 | 後 |
|---|---:|---:|
| 既有 import main 牆鐘 | 249.18ms | 246.07ms |
| 真實 MainWindow 建構 | 375.23ms | 357.32ms |

import main 不建構視窗，不能衡量此改動；建構改善 17.91ms／4.77%，未達 15% 或 150ms 門檻。
主 RendererView 仍承擔首次 Chromium 初始化，因此第二個元件的省時有限。未為追求門檻擴大 PDF 懶建構範圍。
原始 40 筆量測在系統暫存 mdv-startup-benchmark.json；測量包含本機背景驗證負載，不作普遍啟動速度承諾。

## C2

QRunnable 背景掃描；取消於 rename 前生效，主執行緒 Slot 套用 mapping。準備中模態進度與樹狀停用避免重複 UI 操作。
掃描失敗不更名；commit 前驗證來源 identity、目的碰撞、每層目錄 metadata 和檔名集合，避免外部同步新增造成 mapping 遺漏。
NTFS 目錄 mtime 實測可能延遲，不能單靠 timestamp，故加入 scandir 集合驗證；最終 rename 前再次確認取消。
`py -3 -X utf8 -m pytest tests/test_file_ops.py tests/test_file_browser.py tests/test_document_relocation.py tests/test_relocation_workspace.py -q` → 82 passed in 7.26s。
2000 檔測試 QElapsedTimer 觸發 1ms；斷言 os.walk 不在 UI thread、mapping callback 在 UI thread。
Fresh agent 複驗 50 passed in 5.82s，巢狀目錄外部新增安全中止；最終 cancel check 已補。

## C3（暖快取達標，冷解析未達）

lexer LRU 64 項；高亮區塊 LRU 計入 lang/code/HTML 字串的 sys.getsizeof，預算 4 MiB（不含容器與 lexer 開銷）。超長語言名稱不入快取。
`py -3 -X utf8 -m pytest tests/test_highlight_cache.py tests/test_md_converter_features.py tests/test_md_converter_body.py -q` → 133 passed in 0.97s；Ruff 通過。
Fresh agent 額外 8 threads／200 次輸出逐字相同；9 cache tests passed in 0.30s，5MiB 語言名稱不被保留。
基準指令：`py -3 -X utf8 tools/benchmark_markdown_first_load.py --output <TEMP>/mdv-code-{before,cached}.json --tmp-dir <TEMP>/mdv-code-bench --categories code_heavy --sizes 100kb --warm-reps 1 --switch-reps 1 --contention-reps 1 --first-readable-reps 5 --skip-cold --skip-gui --skip-webengine --skip-memory`。
相同參數 first_readable p50：150.91ms → lexer-only 143.57ms → 區塊快取 18.22ms。
該工具此前已渲染相同文件；額外每次清空兩層快取的冷解析 p50 142.79ms，未達 <80ms。不能將暖快取速度宣稱為首次開啟速度。
未降低子程序門檻：100KB 純解析約143ms，啟動子程序本身成本會抵銷收益；目前既有 GUI 背景渲染仍保留。

## C4

主視窗啟用 250ms debounce；獨立 TagIndex 預設保持同步。timer／aboutToQuit／closeEvent 強制 flush；寫入失敗保留 dirty 供重試並記 log。
`py -3 -X utf8 -m pytest tests/test_tag_index.py tests/test_tag_rename.py tests/test_tag_delete_merge.py -q` → 16 passed in 1.08s。
`py -3 -X utf8 -m pytest tests/ -q` → 1727 passed、82 skipped，62.03s；Ruff 通過。
Fresh agent：6 tag tests + 1 真實 window close test 通過，額外 failure/retry 實跑通過。
限制：程序遭強制終止時無法保證執行 flush；250ms 內尚未落盤的是可重建的 tag cache，筆記及註解原檔仍各自同步保存。

## C5（待決策）

實際 WebEngine 載入 WYSIWYG Mermaid／PlantUML 範例時，document.scripts 包含兩者資源，Mermaid 產生 SVG；與「未使用」前提不符。
已詢問保留功能或停用後裁剪，尚未收到選擇，故保留資源。沒有將可能破壞既有功能的裁剪當成完成。

## C6（裁剪通過，體積目標未達）

PyInstaller Analysis 後只過濾 qtwebengine_locales/*.pak，保留 zh-TW、zh-CN、en-US，不影響其他資源包。
`py -3 -X utf8 -m pytest tests/test_package_policy.py -q` → 1 passed in 0.06s；Ruff 通過；fresh agent 額外驗證 Windows 路徑、其他 pak 與非 pak 資源均正確保留。
實際 PyInstaller 建置成功（裁剪後 exit 0），輸出放 TEMP，未覆蓋現有 dist：

| 指標（bytes） | 裁剪前 | 裁剪後 |
|---|---:|---:|
| 全封裝 | 1,046,108,202 | 1,001,996,347 |
| 語系 | 45,767,560（53 檔） | 1,655,705（3 檔） |

減少 44,111,855 bytes，約 42.07 MiB／4.22%，未達盤點預估 100 MB。建置環境額外套件被 hooks 收入，總量亦不同於盤點；後續 E7 以隔離鎖版環境重驗。
使用裁剪後封裝的 locales/resources 路徑實跑 QWebEngineView，loadFinished 成功，JavaScript 讀回 `Locale resource smoke OK`，exit 0。這是資源載入驗證，尚非完整安裝版人工驗收。

## C7（評估後本輪不啟用）

本機未找到 UPX；無可重現的 SmartScreen／防毒誤判驗證結果。維持 EXE 與 COLLECT 的 upx=False，不將未驗證的壓縮帶入發版，也不宣稱已測出誤判。

## C8（受工具政策阻擋，未刪除）

五份指定產物已確認位於 D:\markdown_viewer\dist 直屬子目錄，無 reparse point；.gitignore:12 已涵蓋 /dist/。
逐目錄檔案數皆為 3452，bytes 分別為 consumer-upgrade-1.31.0=691166451、pdf-hand-tools=691284796、reading-upgrade=691104725、reading-upgrade-popup-fix=691105932、session-restore=691287285。
原生 PowerShell Remove-Item -LiteralPath 命令遭自動審核 blocked by policy，沒有提供更細原因；未嘗試換工具繞過，所有產物仍保留。
目前開啟的 MarkdownViewer.exe 位於 C:\Program Files\Markdown Viewer，未操作該程序。
使用者可在檔案總管刪除上述五個指定資料夾，保留 dist/MarkdownViewer；刪除後確認 dist 只剩目前版本。

## D2

export_actions.show_export_complete 統一四種格式成功回饋；開啟檔案／所在資料夾皆使用本機 QUrl，關閉不啟動外部程式。
`py -3 -X utf8 -m pytest tests/test_export_completion.py tests/test_pdf_export.py tests/test_editor_data_safety.py -q` → 92 passed in 8.84s。
`RUN_WEBENGINE_TESTS=1 QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu py -3 -X utf8 -m pytest tests/test_pdf_export_webengine.py -q` → 1 passed in 7.08s。
Ruff 通過。Fresh agent 複驗 92 passed in 8.75s，確認四種成功路徑、取消／失敗提前返回與中文空白路徑。

## 使用者追加授權

使用者要求全部完成後測試，確認無問題再更新版號、提交並發布；已授權後續 git push 與 release。仍以完整驗證通過為發布前置，未將目前 WebEngine 失敗或未驗證項目視為通過。

使用者另要求無人值守持續執行。C5 採保留已被使用的 Mermaid／PlantUML 功能與資源，不盲目裁剪；其餘依原優先序推進並持續落檔。

## D1

改名當下以完整可讀的 scoped files 重建 LinkIndex，確認對話框列出所有即將改寫的文件；取消不落盤，未接索引服務明確拒絕。
位元組改寫保留 BOM、UTF-16 endian、CRLF、alias、heading 與無關內容；CommonMark 程式區塊、行內程式碼、HTML 標記及跳脫括號不改。
連結檔與原文件／sidecar 先全部準備，再發布；寫入或發布失敗回滾。來源外部異動拒絕；rollback 受 OS 阻擋時保留 originals 並回報位置。
確認前拒絕 dirty／正在編輯／待復原引用文件，避免改寫磁碟後被舊緩衝覆蓋；已載入的乾淨緩衝同步文字與 signature。
保守限制：沿用文件庫排除設定；超過 8000 檔、不可解碼／大於 2MiB 的文件、跨磁碟引用及無法表示為有效 wikilink 的新名稱會停止整批，不靜默略過。
`py -3 -X utf8 -m pytest tests/test_backlink_rename.py -q` → 21 passed in 0.86s。
`py -3 -X utf8 -m pytest tests/ -q` → 1753 passed、82 skipped in 61.24s；Ruff 通過。
Fresh agent 找出並複驗修正未閉合／跳脫反引號及特殊檔名案例；額外驗證五種草稿防護、乾淨 buffer 同步及發布失敗回滾通過。
完整套件首次停在舊的獨立 browser 測試之缺少索引服務警告；更新該測試接入真實準備器後全套通過，未降低正式程式的安全門檻。

## D3

有未儲存 QTextDocument 的外部變更對話框加入唯讀 unified diff、保留雙方、覆寫、捨棄與稍後處理；WYSIWYG 仍先完成原 snapshot gate。
本機副本以 xb 排他建立、碰撞遞增名稱，flush/fsync 成功才載入外部版本；失敗保留草稿與副本路徑，不刪除可能被外部替換的檔案。
預覽就地編輯沒有完整 QTextDocument，沿用既有捨棄確認；須先完成就地編輯才可使用完整文件差異／另存副本。
`py -3 -X utf8 -m pytest tests/test_external_conflicts.py tests/test_editor_data_safety.py tests/test_window_integration.py -q` → 223 passed in 23.33s；最終失敗保留策略追加重跑 7 passed in 0.96s；Ruff 通過。
`RUN_WEBENGINE_TESTS=1 QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu py -3 -X utf8 -m pytest tests/test_wysiwyg_webengine.py -q` → 27 passed in 34.94s。
Fresh agent 7 passed in 0.94s；實跑關閉後外部替換競態，確認失敗處理不誤刪外部檔案。

## D4

PdfAnnotationCard 右上「…」與右鍵共用 _build_menu，沿用作者及唯讀限制；編輯對話框確認 entry identity，避免切換文件後改錯註解。
PdfHighlightsPanel 雙擊或右鍵進入就地文字編輯；新文字以既有 atomic sidecar 保存成功後才替換 model，失敗保持 draft。
PdfMarkupPanel 以文件路徑切換取消舊草稿，避免複製 PDF/sidecar 的相同 ID 造成跨檔寫入；同檔刷新保持失敗草稿。
`py -3 -X utf8 -m pytest tests/test_pdf_markup_editing.py tests/test_pdf_highlights.py tests/test_pdf_embedded_annotations.py -q` → 111 passed、5 skipped in 4.24s；Ruff 通過。
Fresh agent 4 passed in 0.62s，驗證舊 JSON、幾何與標籤保留、writer 失敗不改 model 及路徑切換接線。

## D5

右側 Properties dock 由「檢視 → 屬性」開啟，key/value 表格可新增及編輯，明確按儲存才寫檔；關閉面板不改既有唯讀 front matter 顯示。
PyYAML safe parser 提供節點範圍，只替換已編輯值並拼回原始本文 bytes；保留 UTF-8/BOM/UTF-16 endian/Big5、未知欄位與尾端註解。新增 runtime dependency PyYAML>=6.0。
重複 key、非字串 key、anchors/aliases、非區塊式頂層及不合法 YAML 保守拒絕 GUI 修改；既有未知標準 YAML 欄位不重寫。新值使用 JSON-compatible YAML，日期字串需加引號。
儲存前確認目前路徑、無編輯／草稿衝突且原始 bytes 未被外部修改；經 atomic_io 保存與備份，刷新渲染及索引。
`py -3 -X utf8 -m pytest tests/ -q` → 1785 passed、82 skipped in 61.78s；最終尾端註解修正 `tests/test_frontmatter_properties.py tests/test_properties_panel.py` → 24 passed in 0.99s；Ruff 通過。
Fresh agent 24 passed in 0.95s，額外 nested block scalar + 尾端註解驗證通過。Qt offscreen 實跑截圖已查看，表格與新增／儲存按鈕可見，截圖在 TEMP/mdv-properties-panel.png。

## B1 遠端驗證進行中

使用者追加發布授權後，推送驗證分支 optimization-20260921（截至 D4）；遠端 main 尚維持 7fa7d2c。
CI run：https://github.com/wulove1029/markdown_viewer/actions/runs/35526630330 。尚未得出綠燈／刻意紅燈結論。

首輪 CI：1 failed、1763 passed、82 skipped in 75.19s。失敗是 C2 測試將首次建立 Qt 進度對話框的 750ms 與固定 100ms 門檻比較。
改以 Event 阻住 worker，驗證呼叫已返回、UI QTimer 仍觸發、尚未收到完成事件且掃描／完成回呼分別位於正確執行緒；耗時保留 print，不以 runner 冷啟動速度替代非阻塞契約。

## D6 範圍

原需求 D6 為「仍開放，依需要排入」清單，本輪不擴充 PDF 本文編輯、i18n、雲端同步等大型能力。相關既有限制維持追蹤；F6 補程式碼反向連結，不把它們宣稱為已實作。

## E2

新增 TabState(TypedDict, total=False) 的 17 個可選欄位，_tab_state 及接收分頁狀態的方法使用該契約；仍為相同 runtime dict，不改 session/sidecar 格式。
window 納入漸進 mypy：原有 34 個 Qt／Optional 動態診斷按 arg-type/assignment/attr-defined/union-attr 暫不納管，TypedDict item/unknown-key 持續開啟；並非宣稱 window 所有型別問題已消除。
`py -3 -X utf8 -m mypy` → 5 files 通過；Ruff 通過。
`py -3 -X utf8 -m pytest tests/ -q` → 1788 passed、82 skipped in 61.07s。
Fresh agent 移除 import/annotation 後 window AST 完全相同；TEMP typo `edtor_document` 負向測試確實觸發 typeddict-unknown-key。
