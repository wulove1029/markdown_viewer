# 2026-10-22 計畫逐項驗收

驗收者：fresh reviewer `/root/plan_delivery_audit`；基準為[完整升級計畫](../plans/2026-10-22-comprehensive-upgrade-plan.md)與當前未提交工作樹。已讀 `C:\Users\woo75\.claude\playbook\03-judgment.md`；根目錄沒有 `AGENTS.md` 或 `CLAUDE.md`。本報告區分實作、實跑證據與尚未通過的產品門檻，不能用元件測試替代整個計畫驗收。

角色邊界：最初以獨立 reviewer 檢查全部計畫；發現缺口後受派實作 `diagnostics.py`、`background_jobs.py` 及相關掛接，收尾另實作 renderer 兩階段載入提示。因此對其餘作者變更是獨立驗收，對上述變更是開發者驗證；另一位 reviewer 已在[最終獨立審查](2026-10-22-final-review.md)查核新增模組，並另讀回載入提示的 timer／generation／開檔接線，未發現 blocker。本報告不是已安裝 EXE 或實體桌面通過證明。

## 目前結論

**空白備援頁預熱已撤回，回退後完整回歸與重建通過；仍有性能門檻與實體驗收未完成。** 最終普通組 `2160 passed, 143 skipped, 2 warnings`，renderer 12 檔 `131 passed`，主代理實跑皆 exit `0`，下方有原始 log 與產物身分。含預熱版先前在 `test_unready_spare_never_delays_navigation` 發生 Windows native access violation；窄重現第一案也崩潰，程序退出 `3221225477`（`0xC0000005`），詳見[重現紀錄](evidence-20261022/prewarm-native-crash-minimal.json)與[原始堆疊](evidence-20261022/prewarm-native-crash-minimal.log)。原生根因尚未確認，整項方案已移除；既保留失敗歷史，也不將已撤回路徑當成最終版仍存在的缺陷。

本 reviewer 已獨立唯讀核對撤回範圍：`app/renderer.py` 的 SHA-256 與 initial manifest 完全相同（`0e42380d638a07ec66bba3d6e6f42a8fb7b6a6fa205b364fac3f2c90a0b44b9d`），renderer 與 rotation／switching／source-navigation 測試沒有 spare 或相關 timer hook 殘留；原每 10 次 page 輪換、設定／script／bridge 重綁、列印保護、慢載提示、orphan 與 HTML 背景準備保留。新 regression 驗沒有 spare 的 10 次立即導覽，已納入最終 131 案；回退與自動回歸範圍完成，不表示全部產品 KPI 或實體驗收完成。

**產品功能及初始候選版自動回歸已有證據，初始性能量測仍有未達標項目，尚非最終結案。** 本次更新重新讀取完整大檔來源模式、按需圖表、貼上預覽、底部閱讀狀態、右側 dock 分頁及 10 模組型別檢查配置；下方不再把早期缺件當成目前未交付。已讀回初始候選版普通回歸 2,157 passed、Chromium renderer 130 passed 及兩組 Office 原始 log，另讀回九個隔離診斷命令。100 KB Markdown 暖切換、1 MB 首屏及 PDF gap 仍未達原計畫門檻；預熱改善數字屬撤回方案，不計入交付成績。初始 manifest 與測試結果不替代回退後重新驗證；跳過案例與實體桌面證據不算通過。

本 reviewer 先逐檔比對當前來源，再直接解析初始與最終兩份 manifest：各含 617 個來源，沒有新增／遺漏，只有 `app/global_search.py` 與 `tools/benchmark_library_search.py` 雜湊不同，其餘 615 個一致；搜尋 150 ms debounce／IME 防護保留。這支持保留初始 MD／PDF／編輯器／TXT 的原始性能證據，搜尋、重建 EXE 工作集與 IPC 另重測。舊結果仍明示原量測時間與範圍，不改寫成全部重新執行；測試檔不在產物來源清單內，其驗證另由實際 pytest log 證明。

**保留事故紀錄：早期 QSettings 隔離未生效。** 早期 benchmark／截圖工具只設定 `setDefaultFormat(IniFormat)` 與 `setPath`，但產品使用的 `QSettings(org, app)` overload 仍落到原生登錄，先前「個人偏好未觸及」的判斷撤回。後續已有 `isolated_runtime.py` 顯式 INI factory、七支工具掛接，以及另一位 fresh reviewer 的真 CLI／native digest／持久資料 hash／worker tripwire 證據，詳見最終獨立審查；修後指定執行的前後資料一致。這不抹除早期工具觸及設定的事實，也不等於未知原偏好已還原；原偏好影響處理與重測數字仍由主代理保留獨立證據。

初次獨立檢查的缺口及後續結果：

1. 大型 HTML 原本仍在 UI thread 編碼／寫檔；已由另一作者新增 [html_loading.py](../../app/html_loading.py)，本 reviewer 核對 `_Request` 的鎖、取消與 lease 所有權、serial／generation guard、destroyed 清理，另實跑 6 個生命週期案例通過，未發現本範圍的 blocking 問題。這不代表所有慢磁碟時序已驗。
2. 原本只有各服務配額；現由 [background_jobs.py](../../app/background_jobs.py) 將現有 Qt global pool 設為最多 4 threads，顯示／搜尋優先級為 1、metadata／Properties／PDF metadata／寫作大綱為 -1；Properties、tag 與 HTML 的待執行工作以 owner key 保留最新一個。TXT 與搜尋保留既有 completion／取消清理。PDF 換檔或關閉會取消尚未開始的 metadata I/O。**MD child pool 最多 2、PDF native inflight 最多 2、link QThread 仍為另外的限制，不是整個程序樹只有 4 個工作。**
3. 原本缺少診斷匯出；現有 [diagnostics.py](../../app/diagnostics.py) 與「說明 → 匯出效能診斷…」。固定事件白名單、預設 256 筆有界記憶體與固定彙總；只接受數字與固定操作名，記錄搜尋／render 耗時、工作數、取消及 fallback_requested，匯出時抽樣共享 cache／child 數字。明確選擇匯出才以唯一暫存＋fsync＋replace 寫入本地 JSON；不持續寫磁碟，不記錄文件內容、路徑、搜尋字串或例外文字。
4. 圖表與超大 Markdown 的早期缺口已補實作：[diagram_loading.js](../../assets/diagram_loading.js) 對接近 viewport 的一般圖逐一排程，大圖須明確展開，失敗保留完整原碼與重試；匯出另等待圖表完成或進入錯誤狀態。[large_source.py](../../app/large_source.py) 以實際 `10 * 1024 * 1024` bytes 邊界導向完整來源編輯，明示暫停預覽／Office／高亮，沒有截斷後儲存。圖表測試已納入下方凍結 Chromium 130 案成功結果；真實大檔效能仍需另外量測，不能宣稱全部 KPI 已過。

### 收尾查核與補齊結果

再次逐項對照原計畫後，曾確認下列產品缺口；已補者列出當前結果，不繼續保留為目前未交付。載入提示由本 reviewer 實作，其他各項由不同作者完成或續作；驗證角色分開列明。

| 原計畫要求 | 初次查核的缺口 | 當前結果 |
|---|---|---|
| 失效錨點明示 | JS 已回報，但 window orphan handler 為 `pass`；來源跳轉只查 ID | 已補當前 DOM／generation 查詢、兩個註記列表標記及定位失敗提示，保留摘錄／筆記；本 reviewer 獨立 8 案及真 Chromium 11 案通過。 |
| 記住左側面板寬度 | 初始及重新展開使用固定 `PANEL_WIDTH`，沒有寬度保存 | 已新增 `SidebarWidthState`；另一位 fresh reviewer 的 sidebar／隔離 helper `21 passed in 5.39s`，精確命令已讀回最終獨立審查。既有文件樹選取／展開記憶仍存在。 |
| 寫作狀態包含選取字數 | 只有全文增量字數與行欄 | 來源編輯已補 4096 UTF-16 片段／約 4 ms 計數，tooltip 明示按來源文字計算；無選取、Office、IME 組字時清除。獨立 21 案通過，含非零起點／surrogate／詞邊界及真 MainWindow 換文件取消；沒有冒稱 Office 原生選取數已提供。 |
| 導覽支援減少動態 | 註記 `activateAnnotation` 固定 smooth | 已與章節導覽一致檢查 `prefers-reduced-motion`；真 Chromium 測試確認 reduced 時使用 `auto`。 |
| 右側工具一鍵釘選 | 只有 Qt dock 的顯示／隱藏 | 已有釘選按鈕與持久化；限制兩 dock 的拖動／浮出並保留關閉。包含於本 reviewer 獨立 8 案。 |
| 載入超過約 1 秒的說明與動作 | 僅 140 ms 輕提示 | Renderer 保留 140 ms 提示，1000 ms 後說明預覽仍在準備、可切頁或開其他文件，按鈕接主視窗原開檔流程。完成／切換／deactivate 停止 timer，靜默 reload 保留原抑制契約。開發者真 Chromium 16 案通過，另一 reviewer 已讀碼確認；不泛稱 PDF／TXT／Office 都新增同一種提示。 |

PDF 縮放已有底部 2 秒提示，沒有因其不是永久 label 就判定整個縮放狀態未實作。FTS5、整篇 DOM 虛擬化與大規模架構拆分是候選方案，不新增為本輪硬性缺件。

## 計畫交付矩陣

「已實作」表示讀到實際產品掛接；「部分驗證」表示已有測試，仍欠表列驗收；「未驗證」與「未交付」不互相替代。

| 計畫項目 | 實作狀態／程式依據 | 驗收狀態 |
|---|---|---|
| 階段 0：固定語料、兩款布局、命令契約、baseline | 既有 benchmark 加本輪 editor／PDF geometry 工具；compact／expanded 布局可切換；MD baseline 有當日 JSON | 部分驗證。沒有閱讀／寫作／搜尋／保存全套 EXE baseline，也沒有兩款介面形成性比較結果。 |
| 精簡／完整工具、三模式與後端選擇、保存文字狀態 | `workspace_controls.py`；模式仍沿用既有持久化值 | 已實作；workspace tests 與獨立核心回歸通過。尚未完整實體鍵盤巡覽。 |
| 專注工作區、退出入口、分頁固定與列表 | `workspace_controls.py`、`document_tabs.py`、`window.py`；固定分頁與 session 遷移 | 已實作；取消／復原可逆性、固定分頁核心案例通過。 |
| 左側面板寬度與文件庫選取記憶 | 文件樹選取／展開已有 session state；新增 `SidebarWidthState` | 已實作；另一位 fresh reviewer 的 sidebar／隔離 helper 21 案通過，非本 reviewer 重跑。 |
| 共用視覺規格、繁中字體、暖色、設定向後相容 | `theme.py`、`reading_style.py`、`settings_dialog.py`；舒適／緊湊／寬版與自訂 | 已實作；設定往返、無效值、legacy key、暖色文字對比測試通過。Qt／Office 全控制項對比與讀屏未驗證。 |
| 右側大綱／註記／屬性與底部閱讀狀態 | `reading_tools.py` 將閱讀工具與 Properties dock tabify；可持久化一鍵釘選；底部 Markdown 章節／百分比及 PDF 頁碼 | 已實作；本 reviewer 獨立 8 案涵蓋狀態重設、寫作隱藏、dock 分頁與釘選。完整實體鍵盤與讀屏流程未驗。 |
| 延遲提示、正確文件身份、減少動態 | Renderer 140／1000 ms 兩階段 loading shield、可開其他文件；章節與註記均遵循 `prefers-reduced-motion` | 已在上述路徑實作並有 Chromium 證據；沒有把此 renderer 流程泛稱成全部後端統一提示。 |
| 閱讀預設、寬表／程式碼、圖片檢視 | `reading_style.py`、`obsidian-light.css`、`reading_media.js` | 已實作；元件／DOM 測試存在，完整 H1–H6／公式／晚載圖的實體畫面矩陣未驗。 |
| 閱讀位置與並排區塊定位 | `renderer.py` 來源行／區塊 offset、使用者互動後停止還原；`window.py` 來源行同步 | 已實作；獨立核心報告的真實 WebEngine source navigation 案例通過。不能視為全 corpus 長時間位置穩定已驗。 |
| 上／下章與返回 | `reading_tools.py`、`toc.py` | 已實作；顯示 dock 後相同大綱不重設目前章節，核心案例通過。 |
| 註記篩選、來源摘錄、Markdown 草稿與 PDF 頁碼往返 | `reading_tools.py`、`drafts.py`、`window.py::_open_document_target`；orphan 查詢與失效提示 | 已實作；缺文件／ID／原文三種情況有對應提示與測試，跨文件／generation 過期結果拒絕更新。沒有把功能案例等同全部真實語料長期錨點穩定。 |
| 格式／範本／附件／保存／匯出路由 | `editor_commands.py`、`window.py`、`export_actions.py`、`vditor_glue.js` | 已實作並修正來源匯出按鈕；核心報告有 22 個 Chromium command/navigation 案例，來源匯出另有專屬案例。 |
| 繁中 IME guard | `editor.py`、Office glue 合成 composition guard | 已實作／合成事件已驗；Microsoft 注音、倉頡／速成候選窗與鍵盤操作仍未驗證。 |
| 保存真值、Office live snapshot、Undo、語法往返 | 沿用 QTextDocument 真值與 Office snapshot／ack；unsupported source 警告仍在 | 部分驗證。獨立核心驗了最新 snapshot 首次保存、取消與失效大綱；不可宣稱全部語法無損往返或跨後端 Undo 新契約。 |
| 外部衝突並列／差異、未命名草稿 | `external_conflicts.py`、`drafts.py`、`session_state.py` | 已實作；首次儲存取消、避免覆寫、關閉後狀態釋放、checkpoint 重開等有核心獨立證據。 |
| 打字機／段落聚焦、大綱編輯 | `editor.py`、`writing_outline.py`；跳轉、折疊、升降級 | 已實作於來源後端；Office 不支援時入口停用。標題／Unicode／Undo／後端切換案例通過。 |
| 範本預覽／最近、命令搜尋／最近／可改快捷鍵 | `template_picker.py`、`command_palette.py`、`workspace_controls.py`、`shortcuts.py` | 已實作；不覆蓋 Ctrl+Shift+P PDF 匯出。主代理報告 locked 環境 window 159 passed，見下方環境邊界。 |
| 增量字數與選取字數 | `editor_status.py` 全文字數增量；來源選取字數以有界 UTF-16 片段計算 | 已實作；本 reviewer 獨立 21 案通過。Office／IME 時不顯示隱藏來源編輯器的選取數；既有 100 KB／1 MB 元件 benchmark 不代替整體打字產品 KPI。 |
| 貼表格／附件預覽、格式一致性 | `paste_preview.py`、`editor.py`、Office glue／window；TSV 可選表格／純文字／取消，本機附件先預覽再複製 | 已實作；source 案例納入凍結普通回歸，真 Chromium 命令／貼上組 22 passed。預覽有界但插入完整資料；版本／位置重驗；取消不複製。任意 HTML 表格及 IME 組字沿用 native 行為，沒有宣稱所有剪貼簿格式都被轉換。 |
| 搜尋增量解碼、中文語意、漸進結果 | `search_index.py`、`global_search.py`；版本快取、原 substring 語意、QListWidget delegate 與 8 ms 批次 | 已實作；Windows ChangeTime、取消、過期信號與 1,202 文件／6,001 行無遺漏案例通過。沒有採 FTS5，不構成缺件，FTS 是原計畫候選。 |
| 共享 metadata／tag／backlink 內容 | `library_content.py`、`document_metadata.py`、`links.py`、`tag_flow.py` | 已實作；1,000 文件暖重用零次解碼／單檔更動一次解碼有 JSON。仍重新列舉 freshness，不能稱所有掃描均消除。 |
| 背景配額與主執行緒 I/O | `background_jobs.py`、`properties_panel.py`、`html_loading.py` | 已實作於上述範圍；優先級、上限、待跑合併／取消與 HTML thread／lease 已驗。低優先工作不會中途搶占已跑 worker。 |
| PDF 增量幾何、native session 快取 | `pdf_view.py`、`pdf_render_scheduler.py` | 已實作；初始候選版完整視窗 3,000 頁返回第 1,500 頁，30 次暖樣本 UI gap P95 60.026 ms，未達 50 ms。多尺寸文字選取實體矩陣仍待驗證。 |
| TXT 時間預算／超長單行 | `text_loading.py` 的 8 ms 常規批次及長段落背景建構／NoWrap | 已實作；初始候選版 5／50 MB 及百萬字元繁中單行完整保存皆通過。單行實際 2.75 MB，單次最大 UI gap 3,863.538 ms；50 MB 為 658.220 ms，不能稱所有 UI turn ≤8 ms。 |
| 大 MD／圖表／DOM 分階段 | `large_source.py` 完整大來源模式；`diagram_loading.js` viewport lazy／大圖展開／逐圖排程；HTML 背景準備與既有快速文字 prefix | 已實作；大來源獨立案例涵蓋 UTF-16／CRLF／Emoji 完整保存、過期載入取消及禁止切回昂貴預覽。測試降低門檻，不冒稱真實 >10 MiB 性能已驗。圖表已納入凍結 Chromium 130 passed；沒有改成整篇 DOM 虛擬化。 |
| 搬移強制中斷與啟動回放 | `relocation_journal.py`、`file_ops.py`、`session_state.py` | 已實作；程序 kill 測試存在，本 reviewer 重新讀關鍵修正並實跑 8 個額外 adversarial 案例通過。此項不等於草稿強殺重啟已驗。 |
| 本地診斷 | `diagnostics.py`，Help 選單匯出 | 已實作；隱私白名單、併發、限量、取消、不覆寫原匯出及真搜尋／render 掛接已驗。執行期資料只存記憶體，關程式即消失，使用者匯出後才存磁碟。 |
| CI／型別／發布 | WebEngine 改為 push／PR 常態分組 job；mypy 管理範圍由 5 擴至 10 個現存模組 | 已實作配置；最終本機普通／renderer 組、mypy 10 模組及 EXE／installer 建置通過，產物有獨立雜湊驗收。原有 ignore 規則仍存在，不能稱全專案型別嚴格化；沒有本輪遠端 CI run 或實際安裝證據，未發布。 |

實作依據並不自動等於驗收通過。完整產品結案還取決於下方 KPI、實體輸入／UI 及安裝版證據。

## 本 reviewer 實跑證據

```powershell
$env:PY_PYTHON3='3.13'
$env:QT_QPA_PLATFORM='offscreen'
py -3 -X utf8 -m pytest tests/test_search_journal_review.py tests/test_workspace_controls.py tests/test_reading_style.py -q -p no:cacheprovider
```

輸出：`25 passed, 2 warnings in 3.84s`，exit code `0`。兩個 warning 來自外來暫存檔保留測試，非資料遭刪除；檔案內容與復原斷言通過。這批只涵蓋相應元件，不是全部產品回歸。

上述與下一批使用 Python 3.13.5／PySide6 6.11.0；後續另外在鎖版 venv 的 PySide6 6.11.2 重跑核心新增案例，沒有混稱相同環境。

```powershell
py -3 -X utf8 -m pytest tests/test_diagnostics.py tests/test_global_search.py tests/test_render_service.py -q -p no:cacheprovider
# 45 passed in 10.30s; exit 0
py -3 -X utf8 -m pytest tests/test_background_jobs.py tests/test_properties_panel.py tests/test_search_index.py tests/test_pdf_view.py tests/test_pdf_embedded_annotations.py -q -p no:cacheprovider
# 138 passed, 7 skipped in 6.29s; exit 0
py -3 -X utf8 -m pytest tests/test_html_loading.py -k 'destroyed or completed_files or newest_submission or chunks or prepare_failure or encoding_and_io' -q -p no:cacheprovider
# 6 passed, 1 skipped, 3 deselected in 0.19s; exit 0
```

HTML 的 skip 為真實 WebEngine opt-in 案例；以上 6 passed 是 queued／destroyed／UTF-8／背景 thread 等生命週期案例，不能冒稱 Chromium 像素驗證。加入 window integration 的前一批為 `295 passed, 7 skipped, 2 failed`；兩個失敗是 template preview fixture 與非同步 tag 斷言。主代理修正後另報 locked 環境 window `159 passed`，此數字是主代理實跑，不是本 reviewer 的獨立重跑。

```powershell
.\tmp\upgrade-venv\Scripts\python.exe -X utf8 -c "import sys,PySide6; print(sys.version.split()[0], PySide6.__version__)"
# 3.13.5 6.11.2
.\tmp\upgrade-venv\Scripts\python.exe -X utf8 -m pytest tests/test_diagnostics.py tests/test_background_jobs.py tests/test_search_journal_review.py -q -p no:cacheprovider
# 24 passed, 2 warnings in 2.86s; exit 0
py -3 -X utf8 -m ruff check app/background_jobs.py app/diagnostics.py app/global_search.py app/properties_panel.py app/tag_flow.py app/pdf_view.py tests/test_background_jobs.py tests/test_diagnostics.py tests/test_search_index.py tests/test_pdf_view.py tests/test_pdf_embedded_annotations.py
# All checks passed!; exit 0
py -3 -X utf8 -m mypy app/background_jobs.py app/diagnostics.py
# Success: no issues found in 2 source files; exit 0
```

搜尋／交易的 8 個 fresh adversarial 驗收涵蓋：originals 與 prepared 的外來 `0.tmp` 都保留；`KeyboardInterrupt`／`RuntimeError` 發生於來源已暫存後仍有可回復副本，回放後一致且再次回放不重做；最後 progress callback 才取消仍丟棄 report；1,202 文件和長檔完整命中數；晚到的舊 query signal 不覆寫新結果；Windows junction 被拒絕且原資料留下。讀回 `file_ops.py` 的 `cleanup_safe`、精確 owned filename／fingerprint 與 `relocation_journal.py` scope／resolved path 驗證和回放流程，未發現此範圍仍未修正的 blocker。

既有[獨立核心驗收](2026-10-22-independent-core-review.md)的 12／42／22 案例有各自命令與環境；本報告只引用其明確範圍，沒有把這些重疊案例加總成新的完整回歸總數。

## 本次 source 更新查核

重新核對產品入口與測試，而非只讀交付說明：

- 大來源：`large_source.requires_source`、window 寫作／Office 路由、`workspace_controls` 模式限制及 `tests/test_large_source.py`。來源完整載入後才開放編輯與儲存；不是只把原來的預覽拒絕訊息改名。
- 圖表：converter／fragment 的 lazy／eager policy、`diagram_loading.js` 的排程與完整原碼、renderer／source export 的完成等待和 generation 防線。獨立審查曾記錄第 13 個 Office fragment 案例失敗；後續凍結 renderer 組含 `test_diagram_loading_webengine.py`，130 案全部成功，舊失敗保留為修復歷史。
- 貼上：TSV 的 quoted multiline／escape、有限預覽與完整插入、本機附件 identity snapshot、來源 Undo 與 stale document guard，以及 Office 插入位置驗證。附件確認後若 JavaScript ack 不明，保留已複製副本的限制已在實作報告揭露。
- 閱讀：章節／比例／頁碼 signal、換檔 reset、寫作時隱藏與右側 `tabifyDockWidget`；最終獨立審查已實跑 `tests/test_drafts_reading_tools.py`，輸出 `4 passed in 1.56s`。
- 型別：`pyproject.toml` 管理 `atomic_io`、`file_types`、`edit_backend`、`tab_state`、`window`、`background_jobs`、`diagnostics`、`large_source`、`library_content`、`search_index` 共 10 個模組。配置存在與整組 mypy 實跑通過分開記錄。

[實作說明](2026-10-22-implementation.md)現含閱讀／並排／專注／窄工具／Office 共 5 張實際 PySide6 offscreen 截圖，不是實體桌面驗收；本地連結已讀回。修後隔離工具重新產圖；實作說明另揭露一次並排截圖的 native 結束異常，不能用成功產圖取代關閉／保存驗收。此輪沒有修改該檔。

## 歷史性能證據與邊界

下表保留當時實際輸出，不作最終發布依據。本 reviewer 重新讀回 `upgrade-md-baseline`／`lazy-tools`／`profile`／`switch`、`upgrade-exe-10docs`／`child`、`upgrade-memory-debug`／`clear-history`、`upgrade-rotation-mainwindow` 共 9 份 JSON，其 `settings` 均為原生登錄位置，沒有原值快照。故涉及這些工具的數字也不能稱為完整偏好隔離的乾淨比較；修正後需重跑。

| 證據 | 實際結果 | 能支持的結論／限制 |
|---|---|---|
| `tmp/editor-writing-benchmark.json` | 100 KB／1 MB，各 100 次合成 commit，P95 5.625／5.651 ms | 來源 EditorView＋統計＋offscreen paint；沒有 Windows 候選視窗、Office、MainWindow 或 EXE。 |
| `tmp/upgrade-md-baseline.json` | baseline 100 KB、60 次暖樣本，可讀 P95 153.531 ms | 實際 baseline；OS cache warm、offscreen DOM 雙 frame 代理。 |
| `tmp/upgrade-md-lazy-tools.json` | 當時 candidate 100 KB、60 次暖樣本，可讀 P95 178.776 ms；UI gap P95 19.066 ms | 該版未達計畫可讀 ≤150 ms，不能因 JSON `passed: true` 而宣稱 KPI 達標；該欄位只表示 readiness 成功。此數字不代表後續 source／EXE 最終版。 |
| `tmp/pdf-session-memory-after.json` | 3,000 頁、100 次暖樣本，raster P95 20.918 ms、gap P95 15.002 ms、早晚十次 RSS 中位數 +1.635% | PdfView 元件與 2 份文件；不能替代完整視窗或固定 10 文件切換／關閉工作集。 |
| `tmp/pdf-geometry-before.json`／`after.json` | 3,000 頁混合尺寸、100 次；每批 geometry P95 3.417 → 0.330 ms | 純合成幾何，不含 PDFium I/O／raster。 |
| `tmp/library-content-result.json` | 1,000 文件冷讀 1,000 份，三次 warm 讀取 0 份；單檔變更只解碼 1 份 | 證明共享內容重用；三次不是 30 次尾端樣本，也不是萬檔搜尋首批端到端。 |
| `tmp/text-loading-hybrid.json` | 5 MB／50 MB／1 MB 單行 exact round-trip 均 true；各單次最大 UI gap 約 58／637／418 ms | offscreen TXT 元件 probe，資料完整性有證據；只有單次而非 P95，長文 attach／paint 停頓仍存在。 |

這些 JSON 的時間在最後幾項整合前，不能冒充 frozen 最終 build 的數字。舊 `tests/test_search_index.py::test_search_benchmark` 的 7 次暖樣本及最大值欄位不滿足正式 P95；現已另增 [benchmark_library_search.py](../../tools/benchmark_library_search.py)，每種英文／繁中單字／雙字 query 至少 30 次，分立即派送及 debounce，量完整結果、首 row、UI batch duration 與解碼次數。新工具的定義已經另一位 reviewer 檢查；初始候選版的實跑數字見下表，與早期未隔離量測分列。

## 初始候選版回歸證據

以下由主代理實跑，本 reviewer 直接讀回四份原始 log，並核對 `tmp/validate_upgrade_webengine.py` 與實作說明中的 renderer 12 檔清單一致。Office 兩份 log 已轉為 UTF-8 並成功讀回。不是本 reviewer 的再次重跑，不把重疊範圍相加。初始性能量測後產品繼續修改，故此組只證明修改前的候選版；最後發布前須補上受影響範圍的新結果。

執行檔均為 `.\tmp\upgrade-venv\Scripts\python.exe`，Python 3.13.5／PySide6 6.11.2，使用 `-X utf8`。Qt 設 `QT_QPA_PLATFORM=offscreen`；Chromium 組另設 `RUN_WEBENGINE_TESTS=1` 與 `QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu`。[機器環境](evidence-20261022/environment.json)記錄 Windows 11 build 26200、Ryzen 9 5900HS 8C／16T、約 32 GB RAM、Turbo 電源設定。

| 實際命令／範圍 | 讀回關鍵結果 | 判定 |
|---|---|---|
| `python -X utf8 -m pytest tests/ -x -q --tb=short` | [initial-pytest.log](evidence-20261022/initial-pytest.log)：`2157 passed, 142 skipped, 2 warnings in 118.94s`；主代理記錄 exit 0 | 初始候選版普通回歸通過，142 skips 不算成功案例。兩 warning 是外來暫存檔保留的預期保護訊息。 |
| `python -X utf8 -m pytest` 加 renderer 12 檔，`-q --tb=short` | [initial-renderer.log](evidence-20261022/initial-renderer.log)：`130 passed in 256.46s`；主代理記錄 exit 0 | 初始候選版真 Chromium 指定範圍通過；包含 annotations、source navigation、inline／table、export、reading、switching、HTML、diagrams、page rotation。完整檔案名見實作說明，不冒稱全部 WebEngine 測試集合。 |
| `python -X utf8 -m pytest tests/test_wysiwyg_webengine.py -q --tb=short` | [office.log](evidence-20261022/office.log)：`27 passed in 35.45s`；主代理記錄 exit 0 | Office 凍結範圍通過；主代理確認此後該 scope 未再更動。 |
| `python -X utf8 -m pytest tests/test_office_commands_webengine.py tests/test_paste_preview_webengine.py -q --tb=short` | [office-commands.log](evidence-20261022/office-commands.log)：`22 passed in 21.47s`；主代理記錄 exit 0 | 命令／貼上範圍通過；作者前兩次同組穩定結果另見 final-review，不重複加總。 |

Renderer log 有 headless Chromium GPU mailbox 訊息，仍完成所有斷言並 exit 0；不改寫成無任何診斷訊息。`ruff`／`mypy`／`pip check`／JavaScript harness 的初始候選版成功由主代理實作說明記錄；型別檢查只涵蓋 pyproject 的 10 模組。

### 初始量測後的局部獨立回歸

當時曾整合 blank spare 預熱，仍保留每 10 次導覽的輪換；**該預熱後因可重現 native crash 全面撤回，以下只保留嘗試過程的局部證據**。搜尋 debounce 由 300 改為 150 ms，加入組字暫停與提交後排程，該修改仍保留。兩者由不同作者實作，本 reviewer 當時唯讀檢查後，在主代理授權的時段執行：

```powershell
.\tmp\upgrade-venv\Scripts\python.exe -X utf8 -m pytest tests/test_annotation_bridge.py::test_stored_annotation_renders_mark -q -p no:cacheprovider --tb=short
# 1 passed in 4.75s; exit 0
.\tmp\upgrade-venv\Scripts\python.exe -X utf8 -m pytest tests/test_annotation_bridge.py tests/test_renderer_page_rotation.py tests/test_pdf_export_webengine.py tests/test_search_input_composition.py -q -p no:cacheprovider --tb=short
# 45 passed in 58.02s; exit 0
```

沿用 `QT_QPA_PLATFORM=offscreen`、`RUN_WEBENGINE_TESTS=1`、`QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu`。本輪沒有產品修改、timeout 放寬或 skip。作者另一組曾有 `1 failed, 59 passed`（隱藏 view 的註記 JS 回覆逾時）；獨立單案及 45 案未重現該逾時，根因未確認，保留於[最終獨立審查](2026-10-22-final-review.md)，不得宣稱已證實 flaky。之後新增 unready-spare 案發生且窄重現的 access violation 是另一個明確失敗，已導致整項預熱撤回；不以此前通過數抵銷。下方初始搜尋表仍是 **300 ms debounce**，不可拿它冒充新 150 ms 的量測。

## 撤回後最終回歸與產物

以下由主代理實跑，本 reviewer 直接讀回原始 log 及 [final-execution-summary.json](evidence-20261022/final-execution-summary.json)。命令使用相同鎖版 Python 3.13.5／PySide6 6.11.2；普通組未設 `RUN_WEBENGINE_TESTS`，renderer 12 檔另設為 `1`，均為 offscreen／`--disable-gpu`。結果紀錄屬主代理終端證據，本 reviewer 沒有重跑此完整組。

| 最終命令／範圍 | 原始結果 | 判定 |
|---|---|---|
| `python -X utf8 -m pytest tests/ -x -q --tb=short` | [pytest.log](evidence-20261022/pytest.log)：`2160 passed, 143 skipped, 2 warnings in 121.86s`；exit 0 | 普通回歸通過；143 skips 不算成功，兩 warning 仍為外來暫存檔保留保護。 |
| `python -X utf8 -m pytest` 加相同 renderer 12 檔，`-q --tb=short` | [renderer.log](evidence-20261022/renderer.log)：`131 passed in 259.77s`；exit 0 | 包含無 spare 的十次立即導覽 regression；此次沒有 native crash。此範圍完成，不外推所有環境無原生問題。 |
| `python -X utf8 -m ruff check .`／`python -X utf8 -m mypy`／`python -X utf8 -m pip check`／`node tests/js/vditor_glue_harness.js` | [final-static-checks.log](evidence-20261022/final-static-checks.log)：All checks passed／10 source files 無問題／No broken requirements found／OK；各 exit 0 | 最終凍結來源靜態與依賴檢查通過；mypy 限設定內十模組。 |
| `python -X utf8 tmp/build_upgrade_delivery.py` | [PyInstaller](evidence-20261022/pyinstaller.log)：`Build complete!`；[Inno Setup](evidence-20261022/installer.log)：`Successful compile (195.000 sec)`；driver 及兩子程序 exit 0 | 最終 1.36.0 本地候選重建成功；沒有執行安裝，也未提交或發布。 |

兩組 Office 的 `27 passed in 35.45s` 與命令／貼上 `22 passed in 21.47s` 保留上節原始證據；相關來源與初始 manifest 相同，沒有改稱本輪再次執行。

[最終 build-manifest.json](evidence-20261022/build-manifest.json) 建置時間為 `2026-10-22T16:52:09Z`；與初始 manifest 比對僅搜尋產品／搜尋工具兩檔不同。另一位獨立 reviewer `/root/independent_core_review` 對磁碟實體重新計算 617／617 來源及兩產物，皆吻合，清單無新增／遺漏且讀取期間穩定，詳見 [final-artifact-audit.json](evidence-20261022/final-artifact-audit.json)。本 reviewer 讀回並核對兩份 manifest 與 audit，沒有在 idle 效能量測期間另做一次整批 hash。

| 最終候選產物 | Bytes | SHA-256 |
|---|---:|---|
| `dist/upgrade-20261022/MarkdownViewer/MarkdownViewer.exe` | 9,754,561 | `244e834c5734194d68682290560dcaeaafb9c3c7f05c672852b7d88be0b9fef6` |
| `installer_output/upgrade-20261022/MarkdownViewer_Setup_v1.36.0.exe` | 166,129,564 | `51325e5c1959c0d401e28c7526ff706494b8758d3d131f6ccaf611a0937339d1` |

上述是撤回後交付候選的身分；`initial-*` 與 `prewarm-*` 產物／log 分別保留為初始及撤回實驗，不能混用雜湊。最終三組診斷已完整讀回，結果如下；程序成功不等於每項產品 KPI 通過。

### 最終重測：搜尋、EXE 工作集與 IPC

本 reviewer 直接讀取 [final-benchmark-commands.json](evidence-20261022/final-benchmark-commands.json) 的完整命令、三份原始 JSON，以及對應 [搜尋 guard](evidence-20261022/final-search-10000-guard.json)、[EXE guard](evidence-20261022/final-packaged-100-switches-guard.json)、[IPC guard](evidence-20261022/final-packaged-ipc-guard.json)。三命令 exit `0`，guard 皆為 `native_unchanged: true` 與 `persistent_data_unchanged: true`。執行時間為 UTC `16:53:58`–`16:55:49` 完成，主代理確認沒有建置／測試並行；本 reviewer 只讀取結果，沒有重跑性能。

| 最終重測 | 精確範圍與結果 | 判定 |
|---|---|---|
| [10,000 文件搜尋](evidence-20261022/final-search-10000.json) | 同一 4,923,400 bytes 語料；`needle`／`尋`／`找資` 各 30 次，150 ms debounce。含 debounce 首列端到端 P95 **173.341／173.083／171.775 ms**；派送後 P95 15.648／15.618／13.906 ms。立即派送首列 P95 31.697／19.852／24.288 ms。 | 此語料的暖查詢首列端到端 **≤200 ms 達標**。不將首列誤寫成完整搜尋完成；含 debounce 完整結果 P95 631.022／561.013／535.290 ms。 |
| 同次搜尋的 1,236 個 GUI 結果批次 | batch P95 **2.469 ms**，最大 **8.195 ms** | 大多數批次低於 8 ms，但至少最大值超標，**不能宣稱每批皆 ≤8 ms**，更不能外推全產品事件迴圈。 |
| [最終 EXE 10 文件工作集](evidence-20261022/final-packaged-100-switches.json) | 每文件 101,168 bytes，10 次暖身＋100 次暖切換後關閉全部分頁。ready P95 **226.375 ms**、full-ready 233.248 ms、gap 40.197 ms；程序樹 RSS 首末 **−1.295%**、首尾十次中位數 **+2.788%**，全部分頁成功關閉。 | 此工作集 RSS 淨增 ≤10% 達標；100 KB 暖切換 **仍未達 150 ms**。這是 100 次切換後全關，不是 100 輪開／關。 |
| [最終 EXE 第二實例 IPC](evidence-20261022/final-packaged-ipc.json) | 正常 exit `0`、轉送路徑一致、**305.256 ms** | 此轉送 smoke 通過，不包含主視窗首屏、實際安裝關聯或完整單一實例操作。 |

最終 EXE 的單次 launch-to-window 為 4,177.729 ms，包含診斷語料建立、排除首文件畫面、OS cache warm；不作已安裝 EXE 暖啟動 ≤1.5 s／冷啟動 ≤3 s 的 P95 證據。初始與最終版本沒有改動 MD／PDF／TXT／編輯器的相關來源，因此下一表保留其實測限制：source 100 KB 可讀 233.871 ms、1 MB 首屏 662.361 ms、PDF gap 60.026 ms 仍未達各自門檻。沒有把撤回預熱的約 189 ms 納入交付成績。

## 初始候選版性能讀回

本 reviewer 直接解析下列原始 JSON、九份對應 guard 及 [benchmark-commands.json](evidence-20261022/benchmark-commands.json)。九個命令均記錄 exit `0`，guard 均為 `native_unchanged: true` 與 `persistent_data_unchanged: true`；這只涵蓋工具明定的原生設定鍵及應用程式資料摘要，不代表掃描全部使用者文件，也不抹除先前設定事故。命令清單保留完整參數與 UTC 完成時間（2026-10-22 16:11–16:14，即台灣 10/23 00:11–00:14）。

以下是**初始候選版**的原始量測。最終 manifest 的 MD／PDF／編輯器／TXT 相關來源與它相同，因此保留作這些範圍的證據；搜尋與 EXE 另列重測，不聲稱下表全部再次執行。`passed: true` 與 exit `0` 表示樣本完成、readiness／完整性斷言通過，不會自動比較產品 KPI。暖樣本 P95 採排序後 `ceil(n × 0.95)`；首屏為 offscreen DOM 雙 frame、PDF 為 raster 代理，均非實體螢幕第一幀。OS cache 已暖，不含冷啟動。

| 初始量測與原始證據 | 讀回數字 | 原計畫判定與範圍 |
|---|---|---|
| [100 KB baseline](evidence-20261022/baseline-100kb.json)／[source candidate](evidence-20261022/candidate-100kb.json)；2 文件各 101,168 bytes，暖樣本各 60 次 | ready P95 **173.786 → 233.871 ms**（+34.574%）；candidate full-ready 237.227 ms、gap 49.749 ms | 暖切換 ≤150 ms 未達標，且相同 workload 較 baseline 退步。baseline 本身也未達 150 ms；不可引用更早未隔離結果取代本批比較。 |
| [1 MB 程式碼密集 MD](evidence-20261022/candidate-1mb-code.json)；2 文件各 1,047,495 bytes，暖樣本 30 次 | ready P95 **662.361 ms**、full-ready 783.782 ms、gap 98.406 ms | 首屏 ≤500 ms 未達標。首開樣本另有 ready 941.024 ms／full-ready 3,117.074 ms，不混入暖 P95，也不是已安裝 EXE 的冷啟動。 |
| [3,000 頁 PDF](evidence-20261022/candidate-pdf-3000.json)；2 文件，第 1,500 頁，暖樣本 30 次 | 完整 MainWindow gap P95 **60.026 ms**、可讀 64.698 ms | 返回原頁 gap ≤50 ms 未達標；此次是完整視窗證據，沒有用早期元件 raster 數字替代。 |
| [10,000 文件搜尋](evidence-20261022/search-10000.json)；總 4,923,400 bytes，`needle`／`尋`／`找資`，每組 30 次、各命中 100 文件 | 立即派送首列 P95 12.480／13.490／15.520 ms；含既有 300 ms debounce 端到端 P95 337.463／338.490／335.234 ms；含 debounce 的派送後首列 P95 26.133／19.588／14.494 ms | 暖查詢派送後首列均低於 200 ms；**從輸入起算的端到端仍超過 200 ms**，不得只報 12–16 ms 為整體互動耗時。立即派送完整結果 P95 392.082／374.243／475.440 ms；含 debounce 為 892.885／794.612／691.337 ms。暖樣本沒有額外解碼，累積解碼維持 10,000。 |
| 同一搜尋原始 trace，1,200 個 GUI 結果批次 | 每批 P95 2.084 ms，最大 **7.464 ms** | 此工具／語料的結果套用符合 ≤8 ms；不能外推所有背景工作或全產品 UI turn。 |
| [來源編輯輸入](evidence-20261022/editor-100.json)；100,032／1,000,000 bytes，各 100 次合成 commit | commit＋統計＋offscreen paint P95 **5.987／5.489 ms**；未用全文 fallback | 元件代理低於 50／80 ms，但不含真實 IME 候選窗、Office、EXE 或完整 MainWindow，產品打字 KPI 仍需補驗。 |
| [打包 EXE 固定 10 文件](evidence-20261022/packaged-100-switches.json)；各 101,168 bytes，10 次暖身＋100 次暖切換，最後關閉全部分頁 | 程序樹 RSS 首末 **−0.775%**、首尾十次中位數 **+0.993%**，峰值 359.422 MiB，關閉後 245.570 MiB；ready P95 227.239 ms、gap 40.338 ms | 此工作集 RSS 淨增 ≤10% 達標；這是 100 次切換後關閉全部分頁，沒有做 100 輪開／關。EXE 暖切換仍未達 150 ms。啟動至視窗的單次 5,350.229 ms 包含診斷語料建立，排除首文件畫面，不能作已安裝版暖／冷啟動 P95。 |
| [TXT source-editor probe](evidence-20261022/text-loading.json)；5 MB、50 MB、百萬字元繁中單行（2.75 MB），各單次 | 完整往返與儲存均 true；最大 UI gap **66.462／658.220／3,863.538 ms** | 資料完整性有證據；attach／字形排版仍造成停頓。單次不是 P95，百萬字元亦不能誤寫成 1 MB；較早 ASCII／不同元件 probe 不替代此結果。 |
| [打包第二實例轉送](evidence-20261022/packaged-ipc.json) | 正常 exit `0`、路徑相符、308.392 ms | 此第二實例 IPC smoke 通過；不含主視窗、首屏、安裝關聯或完整單一實例操作流程。 |

[initial-build-manifest.json](evidence-20261022/initial-build-manifest.json) 記錄當時 1.36.0 未提交／未發布產物、617 個來源雜湊及建置期間來源未變；EXE 9,753,909 bytes，SHA-256 `6659ba5d1afdbbd14178fc0f6631680c61e1675bae82edac2de3b149302a8c86`；installer 166,147,790 bytes，SHA-256 `41bfcf2f9e5abf8c86cebd7f0036c0768c229650ee774711666d7a0abf0e8cab`。本 reviewer 讀回的是該 manifest 紀錄，未將其套用到後續重建產物，也未執行安裝。預熱方案已撤回，其 probe 改善不計入成績；保留的搜尋修改及回退後產物須另有驗證。

### 設定備份唯讀查核

受主代理追加委派，只搜尋本專案既有檔案，不讀寫登錄。`rg --files` 後以 Python 檢查可讀 `tmp` 的 1,322 個檔案（排除 venv、build 與 source copy），未找到 `.ini`／`.reg`／`.bak`／`.backup` 或 settings／backup JSON／TXT 備份候選；121 個歷史目錄存取被拒，不能宣稱完整涵蓋。另核對 docs 與根目錄的 45 份 JSON，相關內容僅有位置或 `native_session_unchanged` 等結果旗標，沒有可用的原偏好快照。

`verify_session_restart.py` 的 `before` 字典只保存在當時程序記憶體；輸出是未變更布林，不是備份。10/15 的三份 MainWindow JSON 同樣記錄 native settings，因此也不能作原設定還原來源。本次沒有找到可可靠還原未知原值的證據；主代理只應依可識別測試殘留與另外保存的可靠證據處理，不能猜測原值。

## 待結案項目

- 最終候選版自動回歸、重建與產物身分已依上表完成；143 skips 與實體情境另列，不把已撤回預熱的 native crash 當作目前仍含該缺陷，也不把本次通過延伸成所有環境沒有原生問題。
- 最終三組 smoke／benchmark 已沿用顯式 INI 隔離，實際路徑與前後 guard 已讀回且不變。早期偏好事故仍保留；沒有事故前快照，不能把未知原值稱為已還原。
- 最終萬檔搜尋首列端到端及指定 EXE RSS 工作集達標；MD 暖切換／1 MB 首屏／PDF gap 未達門檻，搜尋最大 batch 8.195 ms 也不能宣稱每批 ≤8 ms。已安裝 EXE 暖／冷啟動 P95 尚無合格證據；來源編輯元件的合成輸入數字不替代完整產品與實體 IME KPI。
- 真實 Microsoft 注音／倉頡／速成候選視窗、100–200% DPI、雙螢幕、讀屏、五位代表使用者六項任務的 90%／20% 指標尚未驗證。
- 乾淨安裝、覆蓋升級、檔案關聯、完整單一實例流程、離線資源、更新失敗還原需實際安裝候選版後補驗；最終第二實例轉送通過及建立 installer 本身不等於全部通過。

實體補驗可直接依計畫操作：在隔離測試帳號安裝指定候選版 → 100／125／150／200% DPI 開 MD／Office／PDF、於兩螢幕移動 → 注音及倉頡／速成輸入繁中與 Emoji，組字中 Enter／Esc／方向／選字／切頁 → Undo／Redo 與儲存後逐字比對 → 關閉重開核對草稿／頁碼／段落 → 外部修改、只讀及磁碟滿故障。需保留建置版本、環境、命令／退出碼、截圖與預期／實際內容；本報告沒有操作真實候選窗或注入實體磁碟滿。

## 原計畫明確後續項目

章節拖曳排序為「後續加入」；雲端同步、多人協作、外掛平台、PDF 本文編輯／簽名、AI 寫作助理與更換編輯器／框架是後續需求池。這些不應冒列已實作，也不應誤列本輪必修缺陷。
