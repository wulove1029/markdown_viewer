# 1.36.0 完整升級實作與驗收

後續狀態：本報告記錄 10/22 升級批次。10/23 接續修改更新器、安裝腳本與發布通道，最新發布限制見 [發布前補強](2026-10-23-release-readiness.md)；下列舊產物雜湊不代表補強後重建產物，應以最新 build-manifest 為準。

基準 commit：47f6b47（1.35.0）；本輪由使用者授權持續實現[完整計畫](../plans/2026-10-22-comprehensive-upgrade-plan.md)。本檔描述實作結果，原計畫與較早的審查報告保留各自時間點，不應將舊報告的「未交付」直接當作最終狀態。

## 目前交付狀態

主要功能已整合為可供本機試用的 1.36.0 候選版；完整普通回歸、指定 Chromium 組與重建產物驗收通過，最後三組診斷均成功執行。搜尋端到端與固定工作集記憶體達到本次目標；Markdown／PDF 部分效能目標及實體驗收仍未完成，不能宣稱原計畫已全面達標。本機變更尚未提交或發布。

額外空白頁預熱在單案中重現原生崩潰，已精確撤回；最終版保留原本每 10 次導航輪換。撤回後重新完成回歸與建置，詳見[獨立驗收](2026-10-22-final-review.md)與[逐項計畫核對](2026-10-22-plan-acceptance.md)。

| 面向 | 實作結果 |
|---|---|
| 工作區與介面 | 預設精簡工具列，可切換完整工具；閱讀／寫作／並排與 Markdown／Office 選擇；明確儲存狀態；專注及退出入口；固定分頁與分頁列表；命令搜尋與最近使用；側欄寬度跨重啟記憶、右側工具位置釘選。 |
| 閱讀 | 舒適／緊湊／寬版；字體、字級、行寬、行高、段距及暖色閱讀；章節前後／返回；底部章節與閱讀百分比；PDF 頁碼；右側閱讀工具與屬性可用分頁切換。 |
| 媒體 | 圖片放大與適寬、表格／程式碼獨立水平捲動；Mermaid 接近可見範圍才繪製，大型圖提供明確展開入口；失敗保留完整來源及重試。匯出會等待所有圖表進入完成或錯誤狀態。 |
| 寫作 | IME 組字防護、區塊增量字數；來源編輯提供分批選取字數、打字機捲動／目前段落聚焦、大綱跳轉、折疊、標題升降；格式命令依可見後端路由；並排以來源行及區塊同步。 |
| 草稿與安全保存 | 可復原的未命名草稿，首次儲存才選正式路徑；範本預覽與最近範本；保留 Office live snapshot 保存契約；外部修改並列與差異顯示。 |
| 貼上 | TSV 表格預覽，可選插入表格、純文字或取消；預覽有顯示上限但完整內容不截斷；本機附件預覽及來源／編輯位置驗證。取消預覽不複製檔案。 |
| 摘錄與研究 | 篩選註記、複製含來源摘錄、整理成草稿；Markdown annotation ID 與 PDF 頁碼來源往返；原文變更後標示無法定位，保留摘錄及筆記；定位遵守減少動態效果。 |
| 搜尋與索引 | 保留中文子字串語意；64 MiB 版本化解碼快取，共用 metadata／標籤／反向連結內容；漸進結果與 GUI 時間預算；取消／過期請求防護。輸入等待由 300 ms 縮短至 150 ms，中文組字期間暫停查詢，Enter 可立即查詢已提交的文字。沒有採用 FTS5，因它是原計畫的候選方案。 |
| 背景工作 | Qt pool 上限 4，前景優先；可替代任務合併；render 子程序總上限 2；PDF native session 與 raster 快取；Properties 與大型 HTML 準備移出主執行緒。Markdown 累積 10 次頁面導航後，在新一代文件渲染時輪換瀏覽器頁面，控制 DOM 記憶體累積；實驗性的空白備用頁預熱已撤回。 |
| 大文件 | TXT 分批／超長段落背景準備，完整文字保留；超過 10 MiB Markdown 使用完整來源編輯，停用昂貴預覽／Office／高亮，畫面清楚標示限制。 |
| PDF | 增量頁面幾何、二分查找、延後 resize 與閱讀錨點；native 文件 session 重用；修正捲軸出現造成可視寬度改變後的幾何不一致。 |
| 中斷復原 | 持久化搬移 journal、OS 鎖、原子提交及啟動回放；精確比對自有暫存，不清除外來檔案；真實強制終止及再次復原案例。 |
| 診斷與維護 | 本地 256 筆數值白名單，不含文件全文；使用者主動匯出 JSON。CI／release gate 加入 Chromium 分組；mypy 管理範圍由 5 擴至 10 模組。 |

Markdown 預覽保留 140 ms 的輕量載入提示；超過 1 秒提供解析／排版仍在進行的說明與「開啟其他文件」入口。這不是對 PDF／TXT／Office 所有載入路徑的相同承諾。面板釘選是鎖定閱讀工具／屬性的位置、禁止拖動與浮動，仍保留關閉；選取字數目前限來源編輯，Office 不顯示隱藏來源編輯器的選取值。

附件已經過使用者確認並複製後，若 JavaScript 結果不明或文件已切換，保留副本以免破壞可能已插入的連結；因此可能留下尚未被文件引用的附件，不做不確定的自動刪除。

## 真實介面截圖

這些是目前 PySide6 介面的離線渲染截圖，並非概念圖，也不等於實體螢幕驗收。

- [閱讀工作區](../screenshots/upgrade-20261022/reading/workspace-light.png)
- [來源與並排預覽](../screenshots/upgrade-20261022/split/workspace-light.png)
- [深色專注模式](../screenshots/upgrade-20261022/focus/workspace-dark.png)
- [窄視窗及深色閱讀工具](../screenshots/upgrade-20261022/tools-narrow/workspace-dark.png)
- [Office 深色寫作](../screenshots/upgrade-20261022/office/workspace-dark.png)

## 驗收環境與證據

Windows 11 build 26200、Ryzen 9 5900HS（8C／16T）、約 32 GB RAM、Turbo 電源模式。鎖版環境為 Python 3.13.5、PySide6 6.11.2；本機全域 PySide6 6.11.0 的早期資料另列，不能混用。

以下列出最終命令、通過數、原始 JSON 及產物雜湊。各子集合可能重疊，不能加總成完整回歸數字；[終端執行摘要](evidence-20261022/final-execution-summary.json)明確區分原始日誌與主代理根據實際工具結果整理的退出碼。

鎖版 Python 為 `.\tmp\upgrade-venv\Scripts\python.exe`，下列 `python` 均指此執行檔，使用 `-X utf8`。Qt 測試設定 `QT_QPA_PLATFORM=offscreen`；Chromium 組另設 `RUN_WEBENGINE_TESTS=1`、`QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu`。

| 實際命令／範圍 | 關鍵輸出 | 原始證據 |
|---|---|---|
| `python -m pytest tests/ -x -q --tb=short` | 2160 passed, 143 skipped, 2 warnings in 121.86s；exit 0 | [回退後完整回歸](evidence-20261022/pytest.log) |
| `python -m pytest` 加 `.github/workflows/ci.yml` 的 renderer 組 12 個測試檔，`-q --tb=short` | 131 passed in 259.77s；exit 0；包含無預熱頁的連續立即導航回歸 | [回退後 Chromium 完整組](evidence-20261022/renderer.log)；已撤回實驗的失敗另存 prewarm-native-crash.log |
| `python -m pytest tests/test_wysiwyg_webengine.py -q --tb=short` | 27 passed in 35.45s；exit 0 | [Office](evidence-20261022/office.log) |
| `python -m pytest tests/test_office_commands_webengine.py tests/test_paste_preview_webengine.py -q --tb=short` | 22 passed in 21.47s；exit 0；同組連續三次通過 | [Office 命令／貼上](evidence-20261022/office-commands.log) |
| `python -m ruff check .`／`python -m mypy`／`python -m pip check` | All checks passed；10 source files 無型別問題；No broken requirements found；各 exit 0 | [最終靜態檢查](evidence-20261022/final-static-checks.log)；mypy 僅指 pyproject 管理範圍 |
| `node tests/js/vditor_glue_harness.js` | OK；exit 0 | [Office JavaScript 合約檢查](evidence-20261022/final-static-checks.log) |

兩則 RuntimeWarning 來自刻意在搬移暫存放入外來檔案的測試：產品保留非自有資料並發出警告，是預期保護行為。143 個跳過包含 opt-in 與環境條件，不能當作已通過，也不能把上表不同範圍直接相加。Office 最終功能後未再變更，以上保留其最後驗證結果；後續來源選取字數明確排除 Office。

Renderer 組實際檔案為 `test_annotation_bridge.py`、`test_renderer_source_navigation.py`、`test_translate_menus.py`、`test_inline_edit_webengine.py`、`test_table_edit_webengine.py`、`test_pdf_export_webengine.py`、`test_reading_style_webengine.py`、`test_renderer_switching.py`、`test_release_validation.py`、`test_html_loading.py`、`test_diagram_loading_webengine.py`、`test_renderer_page_rotation.py`，均位於 `tests/`。初始 130 案的 headless Chromium 日誌曾含 GPU mailbox 訊息；最終日誌為上述 131 案全部通過且程序 exit 0，兩份證據分開保留。

介面圖檔已用修正後的隔離工具重新產生。深色窄視窗及釘選入口已實際看圖，guard exit 0；另一次並排截圖雖成功產圖，離線原生程序結束曾出現 `0xC0000409`，重跑也有強制退出期間的 native traceback，因此不把截圖工具的結束視為真實關閉／保存驗收。相關產品生命週期以獨立測試證據為準。

## 效能結果與邊界

效能以 offscreen DOM 雙 frame、PDF raster 或 Qt paint 作為代理，使用明確 INI 沙箱；不等於實體螢幕、原生設定儲存或已安裝版冷啟動。暖樣本 P95 採排序後 `ceil(n × 0.95)`；JSON `passed` 代表內容及流程檢查，不代表自動通過性能目標。

空白頁預熱雖曾降低尾端延遲，但在單案中重現 `0xC0000005`，已完全撤回；189 ms 的實驗數字不計入交付成果。獨立核對初始 manifest 的 617 個來源，最後版本僅 `app/global_search.py` 與 `tools/benchmark_library_search.py` 不同。下列保留初始候選對未再變更核心的實測，不冒稱它們是在最後 EXE 重新執行。

| 已有量測／原始資料 | 結果 | 判定 |
|---|---|---|
| [100 KB baseline](evidence-20261022/baseline-100kb.json)／[來源候選版](evidence-20261022/candidate-100kb.json)，各 60 暖樣本 | 可讀 P95 173.786 → 233.871 ms（+34.574%）；候選完整顯示 237.227 ms | 150 ms 目標未達，且這個 workload 比 baseline 慢；不以搜尋或元件速度替代。 |
| [1 MB 程式碼密集 Markdown](evidence-20261022/candidate-1mb-code.json)，30 暖樣本 | 首屏 P95 662.361 ms；完整顯示 783.782 ms；UI gap 98.406 ms | 首屏 500 ms 目標未達。首開另為 941.024／3117.074 ms，不能當作已安裝版冷啟動。 |
| [3000 頁 PDF 完整視窗](evidence-20261022/candidate-pdf-3000.json)，30 暖樣本，回到第 1500 頁 | 可讀 P95 64.698 ms；UI gap P95 60.026 ms | UI gap 50 ms 目標未達；未混用早期 PdfView 元件的較快數字。 |
| [100 KB／1 MB 來源輸入](evidence-20261022/editor-100.json)，各 100 次合成 commit | commit＋統計＋offscreen paint P95 5.987／5.489 ms；逐字正確 | 來源元件代理低於 50／80 ms；真實 IME、Office、完整 MainWindow 與 EXE 打字尚未覆蓋。 |
| [TXT 載入](evidence-20261022/text-loading.json)，5 MB／50 MB／百萬 Unicode 字元單行，各單次 | 完整往返與儲存均正確；最大 UI gap 66.462／658.220／3863.538 ms | 仍有 Qt attach／字形排版停頓；百萬字元為 2.75 MB，單次不是 P95。 |

既有九組命令與 guard 見 [初始量測命令](evidence-20261022/benchmark-commands.json)；每組均 exit 0，設定與指定持久資料摘要前後一致。

最後三組在建置及其他 CPU 測試結束後依序執行；[完整命令與退出碼](evidence-20261022/final-benchmark-commands.json)保留參數。各組 exit 0，三份 guard 的 `native_unchanged`／`persistent_data_unchanged` 均 true，檢查範圍仍限下述設定及資料摘要。

| 最終量測 | 結果 | 判定 |
|---|---|---|
| [新版 EXE，10 文件／100 次暖切換](evidence-20261022/final-packaged-100-switches.json)，各 101,168 bytes，最後關閉全部分頁 | 可讀 P50 149.476／P95 226.375 ms；完整 P95 233.248 ms；UI gap P95 40.197 ms | 切換 150 ms 目標未達；100 次切換全部成功，全部分頁正常關閉。 |
| 同一 EXE 程序樹 RSS | 暖樣本首末 −1.295%；首尾十次中位數 +2.788%；峰值 374,280,192 bytes；全關後 256,655,360 bytes | 此工作集淨增 ≤10% 達標；這是 100 次切換後關閉全部分頁，不是 100 輪開關。 |
| [10,000 文件搜尋](evidence-20261022/final-search-10000.json)，共 4,923,400 bytes；英文／中文單字／雙字，每組各 30 次立即與等待查詢 | 含 150 ms 等待的首列端到端 P95 **173.341／173.083／171.775 ms**；立即查詢首列 P95 31.697／19.852／24.288 ms | 此語料端到端 ≤200 ms 達標；初始 300 ms 等待約 335–338 ms。每組命中 100 文件，暖查詢沒有重新解碼全部文件。 |
| 同一搜尋的結果套用 trace | 1236 批，P95 2.469 ms、最大 8.195 ms；含等待的完整結果 P95 631.022／561.013／535.290 ms | 首批與完整結果分列；最大批次略超過 8 ms，不能宣稱所有 UI 批次均達標。 |
| [新版 EXE 第二實例轉送](evidence-20261022/final-packaged-ipc.json) | 正常 exit 0、路徑吻合、305.256 ms | 此 IPC smoke 通過；不涵蓋主視窗第一幀、安裝關聯或冷啟動 P95。 |

## 本機候選版產物

建置命令使用鎖版環境：`python -m PyInstaller --noconfirm --distpath dist/upgrade-20261022 --workpath build/upgrade-20261022 markdown_viewer.spec`，再以 Inno Setup `ISCC.exe /DMySourceDir=D:\markdown_viewer\dist\upgrade-20261022\MarkdownViewer /OD:\markdown_viewer\installer_output\upgrade-20261022 installer.iss` 產生安裝包。兩階段 exit 0，Inno 輸出 `Successful compile (195.000 sec)`；[PyInstaller log](evidence-20261022/pyinstaller.log)、[Inno log](evidence-20261022/installer.log)及[來源／產物 manifest](evidence-20261022/build-manifest.json)完整保留。

| 1.36.0 產物 | 大小 | SHA-256 |
|---|---:|---|
| [免安裝執行檔](../../dist/upgrade-20261022/MarkdownViewer/MarkdownViewer.exe)，須保留同資料夾全部檔案 | 9,754,561 bytes | `244e834c5734194d68682290560dcaeaafb9c3c7f05c672852b7d88be0b9fef6` |
| [安裝包](../../installer_output/upgrade-20261022/MarkdownViewer_Setup_v1.36.0.exe) | 166,129,564 bytes | `51325e5c1959c0d401e28c7526ff706494b8758d3d131f6ccaf611a0937339d1` |

上述為撤回預熱後重新建置的產物。已撤回版本的證據另存 `prewarm-*`；初始 manifest 及日誌保存在 `initial-*`，不混用雜湊。本輪沒有執行 installer、寫入系統檔案關聯、建立 release tag 或對外發布。

獨立代理重新計算 [617 個來源及兩個產物雜湊](evidence-20261022/final-artifact-audit.json)，均與 manifest 一致；原始檔清單沒有新增或遺漏。此為產物一致性驗收，實際安裝仍待下述步驟。

## 診斷工具設定事故與修正

早期量測與截圖工具錯把 `QSettings.setDefaultFormat(IniFormat)` 視為足以隔離 Windows 的組織／應用程式建構子；實際仍寫入目前帳號的原生設定。因此撤回早期「完全隔離、未動本機設定」的聲稱，早期效能結果只保留為定位證據，不作最終凍結版成績。

已先在本機忽略目錄備份該登錄鍵，再以備份值一致性檢查，只移除六個設定鍵中可確認由診斷產生的暫存文件紀錄。沒有事故前的完整偏好快照，無法保證原本主題、視窗尺寸與更新檢查偏好已還原，亦未猜測覆寫這些值。備份含私人路徑，不納入版本庫或交付證據。

修正後所有相關工具使用明確的暫存 INI 檔、隔離文件庫／復原／標籤／搬移紀錄及 WebEngine 儲存。獨立驗收涵蓋合法 QSettings 建構子、既有匯入別名、來源入口、真實 render 子程序及重啟。最終診斷另由唯讀父程序比較執行前後的 HKCU／HKLM 應用程式設定鍵與三個既有應用程式資料路徑的 JSON／recovery 摘要；此檢查不代表掃描了整個磁碟或全部使用者文件。

## 已知限制與待實體補驗

- 預熱整合期間，一組相關測試曾出現註記 JavaScript 回覆逾時（1 failed／59 passed）；獨立單案及註記／輪換／PDF／搜尋 45 案均通過，原因尚未確認。未放寬等待上限或斷言，也不將未重現寫成已修復。
- 真正的 Microsoft 注音、倉頡／速成候選窗、DPI 切換與雙螢幕移動尚未驗證。合成 composition 與 offscreen 畫面只驗證程式行為。
- 乾淨安裝、覆蓋升級、系統檔案關聯及更新失敗復原需要隔離 Windows 帳號／VM；本輪不把編譯 installer 視為安裝測試。
- 5 位代表使用者、六項任務的 90% 完成率／20% 耗時改善尚未進行，不宣稱已達成。
- 50 MB 文字或極長中文單行，Qt 掛接文件與字形排版仍可能造成數百毫秒至秒級停頓；不能宣稱每個 UI turn 都在 8 ms 內。
- DOM 雙 frame、offscreen repaint、PDF raster 是量測代理，不是實體螢幕第一幀；暖 OS cache 不等於冷啟動。已安裝 EXE 的冷啟動 P95 尚未驗證。
- HTML 匯出保留來源資料夾作為圖片 base，並非將所有資源打包成可攜單一 HTML。
- Office 保留相容性警告；不承諾所有 Markdown 語法經 Office 編輯均逐位元組往返，也不新增跨後端 Undo 契約。

補驗步驟：在隔離帳號安裝候選版，依序於 100／125／150／200% DPI 開啟 MD、Office、PDF並移動至另一螢幕；以注音與倉頡／速成輸入繁中及 Emoji，涵蓋組字中 Enter／Esc／方向／選字／切頁；執行 Undo／Redo、儲存及重開逐字比對，再測外部修改、唯讀、草稿重開、檔案關聯與單一實例。保留版本、環境、預期／實際內容與截圖。效能需在沒有建置／測試並行時重跑，冷啟動另記 OS cache／重開機條件。

## 範圍邊界

原計畫明列章節拖曳排序為後續加入；雲端同步、多人協作、外掛平台、PDF 本文編輯／簽名、AI 寫作助理及框架替換屬後續需求池，本輪未實作。
