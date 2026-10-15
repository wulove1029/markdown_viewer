# 開啟檔案順暢度改善計畫

日期：2026-10-15。查核版本：1.34.0，commit `6914c03`。使用者選定優先範圍：**程式已開啟時，換檔或切分頁卡頓**；從檔案總管冷啟動列為後續。本次交付改善計畫，未修改應用程式。

## 查核進度

- [x] 讀取全域判斷守則、專案開發指南與既有改善紀錄；工作目錄開始時沒有 Git 異動。
- [x] 確認目前 `main.py` 已延後匯入 MainWindow，IPC 接收採事件式處理；2026-09-07 舊計畫中的這兩項不得重複列為未完成。
- [x] 確認 `docs/upgrades/2026-09-21-remaining-work.md` 仍列出真實冷啟動、冷高亮及大型 PDF 頁面尺寸工作。
- [x] 完成當前程式主要開檔關鍵路徑查核；跨文件類型的過期回傳已用受控探測確認。
- [x] 實跑 Markdown 相關 54 項、PDF 相關 47 項及受控回呼探測 3 項，保存有限範圍量測。
- [x] 整理優先順序、依賴、驗收門檻與資料安全條件。
- [x] 由獨立 agent read-back 查核文件與證據；修正引用行號、測試分組及環境描述後通過，見 `independent-review.txt`。

## 工作邊界

本次不以歷史秒數代表目前基線，也不將元件轉換時間或 offscreen 測試視為使用者看到首屏的時間。建議門檻會明確標示為待後續實作驗收的目標。

## 目前已有的實跑證據

- 同步標籤擷取元件（讀檔、front matter 與本文 hashtag 掃描；不含面板與索引）：100 KB／1 MB／5 MB 合成一般文章，各 5 次，中位數約 6.94／80.61／367.22 ms；5 MB 最大 444.64 ms。程式將這段工作放在送出背景預覽之前；實際切換總耗時仍需完整 UI 量測。
- `tests/test_fast_preview.py`、`test_renderer_async.py`、`test_upgrade_performance.py`、`test_text_support.py`：54 passed，1.72 秒，exit 0。
- 現有 Markdown benchmark 已實跑；其 `warm_reopen` 每次清除內容快取，不能當作已快取分頁切換時間。此次未啟用 WebEngine、GUI heartbeat、memory 量測。
- Markdown 測試、標籤及 Markdown 元件探測使用 Python 3.13.6；PDF 測試／探測使用 Python 3.14.4，兩組 PySide6 均為 6.11.0。未使用 `requirements.lock` 的完整鎖版環境，各組僅作診斷證據，不跨環境直接比較；正式比較需固定依賴與打包版本。

原始證據與可重跑探測腳本：`docs/benchmarks/evidence-2026-10-15-file-open/`。

## 1. 建議方向

先讓切換當下立即有回應，再縮短正確內容出現的等待。第一批應交付「移除切換前同步全文掃描、跨文件類型取消舊工作、還原編輯分頁時不啟動無用預覽」；第二批改善熱門分頁返回與 PDF。保留現有 PySide6／WebEngine 架構，延伸既有快取與 worker。

本輪先處理 A→B→A、連續點選、MD↔PDF↔TXT 與編輯分頁往返。整體視覺改版、框架遷移、啟動體積與從檔案總管冷啟動不放進第一批。

## 2. 目前原因與證據強度

| 優先點 | 目前行為與依據 | 可以下的結論 |
|---|---|---|
| Markdown 同步附屬工作 | `app/window.py:4576` 的 `_load_document`，於 `:4613` 更新標籤後才在 `:4619` 送出預覽；`app/tag_flow.py:49` 讀全文、`:53` 掃本文 hashtag | 確認在 UI 路徑做同步全文工作；5 MB 元件實測中位數 367.22 ms，是第一批優先對象。不能將此數字當作完整切頁耗時 |
| 切回 Markdown 仍走整頁載入 | `app/window.py:4415` 的 `_activate_tab` 共用 viewer；`app/renderer.py:632` 的 `load_file` 預設 `show_loading=True`，先送載入頁，`:738` 再 `setHtml` 完整結果 | 即使 body cache 命中，仍有載入頁與 WebEngine 整頁建立。可確認流程；實際閃爍／重排成本尚未用螢幕量測 |
| 跨格式切換仍接收舊 MD 回呼 | `app/window.py:4623–4643` 的 TXT/PDF 一般切換分支未使 MD generation 失效；`app/renderer.py:673` 只檢查 renderer 自身的路徑和 generation | 受控探測已重現 MD→TXT、MD→PDF 的舊 headings 被接受，MD→MD 對照則拒絕。這是回呼正確性與無效工作問題；非自然操作發生頻率的量測 |
| 編輯分頁返回有額外工作 | `_load_document` 先走 Markdown 預覽，到 `app/window.py:4663` 才 `_activate_editor_state`；TXT 首次開啟 `:3697` 同步讀取、`:3710` 建立 `QTextDocument` | 可先決定目標模式，來源／Office 編輯分頁免跑隱藏預覽。現有 per-tab 編輯文件可重用，不應改成每次重讀 |
| PDF 返回丟失已畫好的頁面 | `app/pdf_flow.py:37` 每次呼叫 view.load；`app/pdf_view.py:393` 重設渲染、`:412` 清 raster cache；`:501` 有 geometry cache，miss 時 `:506` 同步讀全頁尺寸 | 已快取尺寸不等於已快取畫面；A→B→A 仍重開文件及重建頁面，應先改善熱門 PDF 返回 |
| PDF 首屏前附屬工作 | `app/pdf_flow.py:54–71` 依序讀 sidecar、更新面板及檢查可寫；`:323` 呼叫 `writable_reason`，`app/pdf_annotation_writer.py:148` 用 PyMuPDF 再開檔 | 確認同步開檔與附屬工作；它們各占多少毫秒尚未分段量測，不能直接列為最大瓶頸 |

既有功能應保留：Markdown 256 KiB 以上可取消的子程序解析、2 MiB 以上部分預覽、32 筆／64 MiB body cache（`app/md_converter.py:96`、`:100`、`:966`）；PDF 背景 raster 排程、尺寸／大綱快取；文件樹背景掃描與約 6 ms 分批建立（`app/file_browser.py:1198`）。不要再次規劃成「新增背景解析／新增所有快取／改背景文件樹」。

## 3. 分批改善與工作量

以下為單人開發含相關測試的粗估；正式順序由 P0 的完整切換量測校正。每批各自保留前後報告並通過回歸後再往下做，不必等所有批次完成才能驗收。

### P0：補上真正的切檔量測（0.5–1 日）

沿用現有工具，新增完整 `MainWindow` 操作入口與 request ID，記錄選檔、舊編輯器快照完成、同步準備、worker 送出／完成、正確內容可讀、閱讀位置還原及面板完成。分開看「舊頁可見」「載入提示」「新文件內容可讀」，避免把提示頁誤當首屏。

- 核心情境：已啟動後首次開 B、A→B→A、每 100 ms 連點不同檔案、MD→PDF→TXT；加入乾淨來源編輯、dirty 來源編輯與 Office 快照往返。
- 文件：100 KB／1 MB／5 MB Markdown（一般文章與大量程式碼分開）、100 KB／5 MB TXT；10／100／1000 頁 PDF，3000 頁作壓力測試，文字／掃描／向量分開。使用合成或明確可用樣本。
- 整組暖場一次後，A→B→A 每組至少 30 次，三輪；新文件且應用快取未命中另列一組。實際開啟的檔案大小、快取命中、目標閱讀頁／區塊、依賴、DPI 與硬體一併保存。
- 指標：p50／p95、每次切換期間最大 UI timer gap、失效結果接受數、取消後仍執行的工作量、程序樹 RSS（含 WebEngine 與解析子程序）。不要混用元件耗時與整體切換。
- Markdown 用當前 request ID、可見 DOM 與 frame 回呼量到近似呈現時間，另做桌面錄影／截圖交叉驗證；PDF 在目標頁實際畫出 raster 的 paint 路徑取點。單獨 `loadFinished` 不算可讀驗收。

交付：可以重跑的切換 benchmark、前十大同步區段與前三項主因。若此處證據改變排序，更新本計畫並記錄原因。

### P1：先移除同步工作及過期回傳（1–2 日，依賴 P0）

1. **延後全文標籤擷取。** 開檔先顯示目標狀態並提交必要內容工作；標籤、backlinks、Properties、recent 更新按需／延後套用。優先重用同一次讀取的解碼文字與解析結果，避免另開一條完整讀檔流程。CPU 密集標籤掃描納入現有子程序或可取消分段工作；只搬到 Python thread 不代表已消除 GIL 競爭。
2. **統一文件切換 token。** 離開 Markdown 到 PDF、TXT、來源編輯或關閉分頁時，取消不再需要的解析；所有 headings、標籤、面板與畫面回傳同時核對 window 的 active document token。背景任務可完成快取，但不能更新另一份文件的 UI。
3. **先判斷目標模式。** 還原已有來源／Office 編輯 buffer 時直接恢復該模式；SPLIT 只排它需要的預覽；PREVIEW 才排主預覽。不先跑一份即將隱藏的整頁 render。
4. 標籤尚未讀完要保有「載入中」狀態，不用空集合回寫索引／sidecar；快照與草稿處理維持必要的等待及明確失敗狀態。

本批驗收：切換路徑不再直接全文擷取 hashtag；MD→TXT/PDF 舊回呼全部拒絕；恢復編輯分頁不送出多餘主預覽；未儲存文字、游標、選取及 undo 不遺失。預期減少同步阻塞，改善幅度以同機完整量測決定，不以 367 ms 直接相減宣稱收益。

### P2：讓熱門分頁返回更快、畫面更穩（1–2 日，依賴 P1）

- **取消每次先載入整頁 loading HTML。** 點選後立即更新分頁選取與目標檔名；先查既有 body cache。短工作不閃載入頁，約 120–150 ms 尚未完成時再顯示輕量覆層。若暫留舊畫面，標明切換中的目標並停用該畫面的編輯，避免誤操作。
- **延伸現有快取。** 重用 body cache 已保存的 headings，再快取／共用標籤、包裝後 HTML 及閱讀錨點，量出剩餘 WebEngine 重建成本。不要把更多 body cache 當成能解決 DOM 重建。
- **有條件保留熱門預覽。** 若仍未達門檻，再試保留最近兩個乾淨 Markdown preview page，與僅快取 HTML 的方案比較。先量 RSS 再決定是否採用；不得為所有分頁無上限建立 WebEngine。這個較重方案不算第一批必做。
- 使用「區塊／頁碼＋區塊內偏移」保存位置並保留現有 pixel fallback；只對可確認同一文件版本的結果恢復。部分預覽換完整內容、圖片載入與視窗尺寸變化後均測試不跳頁首。

本批驗收：命中快取的 A→B→A 不重做 Markdown parse／標籤掃描，不顯示整頁 loading；位置與當前文件一致。預覽快取不能取代 dirty `QTextDocument`。

### P3：PDF 與未命中快取的大檔（2–3 日，依賴 P1；可與 P2 分開交付）

1. **PDF 先回到閱讀頁。** 建立有總容量上限的熱門頁預覽快取，key 含文件身分／版本、頁碼、縮放、DPR、旋轉及必要呈現選項；重載時以經驗證的舊 raster 提供目標頁預覽，再補清晰頁。新的 load generation 必須重新關聯，不能直接移除既有過期 generation 檢查。
2. **附屬資訊分開。** 開檔一次讀 sidecar，共用給標籤與筆記面板；可寫檢查放背景，尚未完成時明確停用註解寫入，真正寫入仍再驗證權限／檔案狀態。大綱與內嵌註解已延後，延續現有機制。
3. **大頁數 PDF 首次載入。** 若 P0 顯示尺寸與 relayout 仍是主因，優先讀恢復頁和可見頁，其他尺寸分批補齊；混合紙張尺寸須保持頁碼及視口錨點。QPdfDocument 保持正確執行緒歸屬，不把 GUI 物件直接丟給 worker。
4. **中型但複雜的 Markdown。** 目前部分預覽由 2 MiB 門檻決定；1 MB code-heavy 元件轉換仍可能慢。量測後再試用耗時預算觸發快速文字預覽、延後語法高亮／圖表，保留完整渲染與明確部分內容標示；不能把部分內容送去完整匯出。
5. TXT 初次載入拆開背景 I/O／解碼及 UI 建立文件；既有 buffer 返回直接重用。若建立 `QTextDocument` 本身昂貴，再評估有總工量上限的分批載入；內容未完整就緒時不得開放會保存部分文件的操作。

本批驗收：PDF A→B→A 復用目標頁預覽且資料版本正確；首次與重訪分開報告。加密、外部改寫、混合尺寸、註解、放大倍率及快速切換都需通過。保留多份 live PDF document 的方案涉及檔案 handles 與記憶體，僅在預覽快取仍不足時另行評估。

### P4：整合驗收（1–2 日）

執行受影響測試、完整回歸與鎖版打包 EXE benchmark；人工核對快速切換、滾動位置、輸入、Office 最新文字與 PDF 註解。以每批獨立 commit 和可停用新增快取／延後工作策略提供回退，回退不得丟失 dirty buffer。這次計畫不包含發布動作。

各批加總粗估 **5.5–10 工作日**，排程可抓 **6–10 日**。建議先交 P0＋P1，加總 **1.5–3 日**，排程可抓 **2–3 日**即可獨立驗收。多 WebEngine page 保留、大型文件虛擬化或大幅 DOM 重構需依實測另估。

## 4. 驗收門檻與安全條件

以下均為提案目標，尚未達成；P0 後依固定基準機確認。以本機 SSD、無人工作答等待為主，網路／雲端慢磁碟單列，不承諾相同速度。

| 指標 | 建議門檻 |
|---|---|
| 操作回饋 | 分頁選取／目標檔名 p95 ≤ 50 ms；長工作在 150 ms 左右可見載入狀態 |
| 已快取的 100 KB Markdown 返回 | 正確文件、正確閱讀位置可讀 p95 ≤ 150 ms |
| 已快取目標頁的 PDF 返回 | 目標頁可讀預覽 p95 ≤ 200 ms；清晰結果另計 |
| 較大文件與首次未命中 | 各內容類型個別 p95 比 P0 改善至少 30%，不得只用小檔平均掩蓋退步 |
| 介面阻塞 | 每次切換的最大 UI timer gap，其 p95 ≤ 50 ms；固定樣本不出現 >100 ms 的應用主執行緒同步長工作；timer gap 不等同幀率 |
| 快速連點 | 最終只顯示最後選定文件；失效回呼接受數為 0；工作佇列不隨連點持續累積 |
| 閱讀穩定 | 原頁／區塊及相對偏移恢復，無先跳頁首再跳回；圖片與部分預覽完成後不覆寫使用者已捲動的位置 |
| 記憶體 | 沿用已有 cache 預算；新增快取先設定獨立及總預算，LRU 可測；固定 A/B 反覆 30 輪後工作集趨於平穩，並量程序樹 RSS |
| 資料安全 | dirty／Office 最新輸入、undo、草稿復原、外部變更、改名／刪除、PDF 密碼／註解均無回歸 |

快取身分至少包含正規化路徑、mtime_ns、大小、內容／渲染設定版本；watcher、重新載入、存檔、改名與刪除要失效。若來源可能同大小同時間戳替換，加入檔案身分或內容指紋／強制重讀策略。未儲存 buffer 以自身 revision 為準；密碼不落磁碟。跨檔背景載入失敗不得把空資料保存回原檔。

Office 切換前 live snapshot 是資料安全要求，**不可為追求門檻而跳過**。先量測快照耗時，合併重複請求並保持可見等待狀態；超時沿用失敗處理，不把未回傳當成功。

一般 CI 以「沒有多餘讀取／解析」「取消有效」「快取有界且正確失效」等可重現契約測試防回歸；毫秒目標由固定環境 benchmark 判定，避免不穩定的時間斷言。

## 5. 本次驗證與下一輪如何重跑

| 本次項目 | 結果 | 範圍限制 |
|---|---|---|
| Markdown 既有四模組 | 54 passed，1.72 s，exit 0 | 功能回歸，不含真實 WebEngine 畫面 |
| PDF 相關既有五模組 | 47 passed，3.39 s，exit 0 | 含 scheduler/cache/password 行為；與 Markdown 組重複執行 `test_upgrade_performance.py`，不加總成不重複測試數；pytest cache 寫入有 1 warning |
| 過期 Markdown 回呼受控探測 | 3 passed，2.85 s，exit 0 | 通過意指成功確認目前行為：TXT/PDF 接受舊 headings，MD 對照拒絕；使用 browser/PDF/TOC spies，非已修復 |
| 同步標籤擷取 | 5 MB n=5，中位 367.22 ms、最大 444.64 ms | 合成一般文章、暖檔案系統；不含 panel/index/UI 首屏 |
| Markdown 元件轉換 | 1 MB code-heavy，每次清 body cache，n=5，中位 2757.83 ms | `warm_reopen` 名稱易誤讀，每次其實清 body cache；不是熱門分頁返回時間，亦非首屏 |
| PDF load 探測 | 3000 頁，geometry cold 174.70 ms、warm 99.35 ms，exit 0 | 單次合成空白 PDF，僅 `PdfView.load`，不含首個 raster paint／MainWindow sidecar；不可推算 p95 |

證據檔：

- `docs/benchmarks/evidence-2026-10-15-file-open/markdown-tests.txt`
- `docs/benchmarks/evidence-2026-10-15-file-open/stale-markdown-probe.txt`、`test_stale_markdown_probe.py`
- `docs/benchmarks/evidence-2026-10-15-file-open/tag-scan.json`、`tag-scan.log`、`tag_scan_probe.py`
- `docs/benchmarks/evidence-2026-10-15-file-open/markdown-components.json`、`markdown-components.log`
- `docs/benchmarks/evidence-2026-10-15-file-open/pdf-audit.txt`、`pdf-load-probe.py`
- `docs/benchmarks/evidence-2026-10-15-file-open/root-run-notes.txt`、`independent-review.txt`

測試與 PDF 探測的完整命令／環境見各證據文件。標籤探測可直接重跑：

```powershell
$env:PY_PYTHON3='3.13'
py -3 -X utf8 docs/benchmarks/evidence-2026-10-15-file-open/tag_scan_probe.py
```

現有元件 benchmark 重跑方式（PowerShell 的大小清單必須加引號）：

```powershell
$env:PY_PYTHON3='3.13'
py -3 -X utf8 tools/benchmark_markdown_first_load.py --sizes '100kb,1mb' --categories 'long_prose,code_heavy' --warm-reps 5 --switch-reps 3 --contention-reps 1 --first-readable-reps 3 --skip-webengine --skip-gui --skip-memory --tmp-dir tmp/bench-file-open-2026-10-15 --output docs/benchmarks/evidence-2026-10-15-file-open/markdown-components.json
```

本次沒有完整桌面首屏基線、真實 GUI 操作驗收、完整測試或重新打包；依賴亦與 lock 不同。因此本計畫提出的是具體改善順序與可驗收目標，不宣稱產品已變快。

探測作業紀錄：第一次 benchmark 尺寸參數被 PowerShell 轉成數值，修正引用後成功；第一次標籤 probe 使用底線而不符現有 hashtag 邊界規則，改成純字母測試標籤後成功，非產品修正。PDF 首次探測的暫存清理因 handle 尚未釋放失敗，之後清理動作被自動審核以 `blocked by policy` 拒絕，未繞過；殘留路徑與成功探測 teardown 均記於 `pdf-audit.txt`。
