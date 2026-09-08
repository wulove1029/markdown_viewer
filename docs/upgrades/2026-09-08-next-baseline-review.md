# 驗收報告：2026-09-08 Markdown 首次載入基線工具

驗收對象：`tools/benchmark_markdown_first_load.py`、`tests/test_benchmark_markdown_first_load.py`、
`docs/benchmarks/first-load-baseline-2026-09-08.json`、`docs/upgrades/2026-09-08-next-baseline.md`。
環境：Windows 11、Python 3.13.6（`py -3`）。以獨立查證方式逐條核對，不依賴撰寫者自述。

## 逐條結果

1. **`--help`**：exit 0，PASS。
   **`--quick`**：用 `--output tmp/next-bench-review/quick-verify.json --tmp-dir tmp/next-bench-review/fixtures`
   跑，`real 0m16.951s`，exit 0，PASS（遠低於 5 分鐘）。
   **輸出覆蓋風險：FAIL（設計缺陷）**——`DEFAULT_OUTPUT` 硬編碼為
   `docs/benchmarks/first-load-baseline-2026-09-08.json`，與已提交的基線檔**完全同一路徑**。
   若之後有人單純執行 `--quick`（不加 `--output`，文件 docstring 第 20 行示範的正是這種裸執行），
   會直接覆蓋現有基線 JSON。撰寫者自己在 `2026-09-08-next-baseline.md` 的證據指令裡刻意加了
   `--output tmp/bench/quick-test.json` 迴避此問題，代表他知道風險但沒有把預設值改安全
   （例如預設輸出到 tmp/ 或要求正式量測必須明示 `--output`）。本次驗證用不同路徑跑，
   確認未動到原檔（MD5 前後一致）。

2. **pytest**：`6 passed in 0.16s`（我的跑法 `real 0m0.566s`），exit 0，PASS。

3. **測試文件生成**：`SEED=20260908` 固定；`SIZES` 固定 100KB/1MB/5MB/10MB；`generate_doc()`
   對每個 (category, size) 用 `random.Random(f"{SEED}:{category}:{size}")`，確定性可重現
   （測試 `test_generate_doc_is_deterministic` 覆蓋）。五種內容型態齊全：`long_prose`（一般長文）、
   `code_heavy`（多段程式碼）、`big_table`（大表格）、`math_mermaid`（數學+Mermaid）、
   `long_paragraph`（長單一段落）。`STRESS_CATEGORIES` 明確把後四種標記為「語法壓力案例」，
   JSON 各案例各自一個 key（如 `code_heavy/5mb`），**不會**與 `long_prose` 混成單一平均。PASS。

4. **JSON 結構**：`cold_subprocess` / `warm_reopen` / `sequential_switch`（大檔切小檔）/
   `parser_lock_contention` 分開記錄；`environment` 含 Python 3.14.4、PySide6/Qt 6.11.0、
   CPU 型號與核數；每案例統計含 `n`／`p50_ms`／`p95_ms`／`max_ms`／`min_ms`。`limitations`
   陣列明列 OS 檔案快取干擾、offscreen/WebEngine 數字不可信、body_render_ms 混合 parsing 與
   Pygments/anchor 兩階段。PASS，符合規格要求。

5. **Parser lock 阻塞實測——獨立重跑**：確認程式碼裡 `parser_lock_contention_once()` 是
   背景執行緒跑 5MB/10MB 轉換、主執行緒同時（等 `big_started` + 10ms）請求 100KB，屬於真正並行
   而非循序。我另寫最小腳本重跑一次（同一份 `long_prose` 5MB fixture，同一 `md_converter`）：
   - 單獨開啟 100KB：32.3ms
   - 5MB 轉換耗時：2508.6ms
   - 5MB 進行中同時請求 100KB：2513.4ms（幾乎等於整個大檔轉完的時間）

   與紀錄檔宣稱的 p50 3215ms（5MB）同量級（本機負載/背景程序不同造成量值差異，但「小檔延遲
   ≈大檔總耗時」的定性結論一致）。PASS（結論成立）。

   **但發現一個統計方法瑕疵**：`small_blocked_until_big_finished_fraction` 是靠
   `thread.join(timeout=60)` **之後**才讀 `not big_done.is_set()`。因為主執行緒本來就會呼叫
   `thread.join()` 等待背景執行緒收尾，所以除非等了 60 秒都沒完成，這個旗標幾乎必然回報
   「大檔已完成」，跟小檔請求當下是否真的仍卡著無關——它是套套邏輯（tautological），
   不是獨立證據。真正有效的證據是 `small_request_latency_ms` 本身（與 solo baseline 對比），
   這部分是對的；但文件敘述「3 次重複裡 100% 都要等到大檔完全轉換完才拿到結果」把這個
   空泛旗標講得像是強力佐證，稍嫌誇大其量測價值（結論本身仍然成立，只是這個特定欄位
   量錯了東西）。

   **取消（cancel）證據**：讀 `app/md_converter.py` 740-762 行，`render_body()` 只在
   `with _CONVERT_LOCK:` 剛進入時檢查一次 `cancel.is_set()`（748 行），之後呼叫
   `_PARSER.render(text)`（753 行）期間完全不再檢查 cancel。文件「無法中止已進入
   `md.render()` 的呼叫」的結論由原始碼直接可驗證，證據充分。PASS。

6. **未做量測揭露**：`2026-09-08-next-baseline.md` 明確列出四種語法壓力案例
   （`code_heavy`/`big_table`/`math_mermaid`/`long_paragraph`）**未跑** GUI 心跳／WebEngine／
   parser-lock contention／記憶體成長，原因是 `code_heavy` 的 Pygments 逐 fence 呼叫成本
   （10MB ≈87秒/次）會讓單機執行時間爆掉。記憶體量測只在 `long_prose/5mb` 跑（未含 10MB
   全套），且 tracemalloc 明確自陳「僅 Python 層配置，不含原生/C 擴充堆」。WebEngine 數字
   標為「不可信任」而非隱藏不提。這些揭露落在 `.md` 文件的散文段落裡，但**JSON 本身的
   `limitations` 陣列只有 4 條通用項，沒有把「語法壓力案例缺完整量測」「10MB 記憶體未測」
   這兩件事寫進機器可讀的 JSON**——只存在人讀的 md 文件裡，這是小缺口（若之後只讀 JSON
   不讀 md，會漏看這些限制）。基本符合要求，但不完整。PASS（有條件）。

7. **`git status`**：`git status --porcelain | grep '^..app/'` 無輸出，`app/` 未被本批動過。PASS。

## 挑錯清單（quality bar：不寫「無」）

- **預設輸出路徑會覆蓋既有基線 JSON**（見第 1 條），是本次驗收發現的實質風險，不是文件自曝
  的已知限制。
- `small_blocked_until_big_finished_fraction` 的計算邏輯是套套邏輯，幾乎恆為 1.0，不具備
  區分力；文件把它當作「100% 被卡住」的量化證據略嫌誇大（第 5 條）。
- `summarize()` 的 p95 用最近秩（nearest-rank）四捨五入，對 n=5～10 的樣本數幾乎必然等於
  `max_ms`（例如 n=10 時 `round(10*0.95)=10`，`min(9,10)=9` 即最後一筆）。JSON 表格裡確實
  多處 p95==max，這不是程式錯誤，但對小樣本而言「p95」這個欄位名稱有誤導性，實質上只是
  「max」。文件沒有特別提醒這點。
- `warm_reopen_once()` 沒有丟棄同程序內第一次量測（無 warm-up discard）；同程序前幾次可能
  混入 Pygments lexer 延遲載入等一次性成本，尤其對 `code_heavy` 案例影響更明顯，文件未提及
  這個潛在偏差來源。
- 檔案生成時間本身沒有算進轉換計時：`write_fixture()` 在計時迴圈之前的獨立「[1/6]」階段
  執行，量測階段呼叫的 `warm_reopen_once`/`run_cold_subprocess`/`sequential_switch_once`
  都不重新生成檔案，這點是對的，沒有把生成時間誤算進轉換時間。
- JSON 的 `limitations` 陣列本身沒有涵蓋「語法壓力案例未跑完整測項」「10MB 記憶體未測」
  這兩件事，只有人讀的 md 文件寫了（見第 6 條）。

## 結論：是否可 commit

可以 commit，但建議先修正「預設輸出路徑覆蓋既有基線」這個設計缺陷（例如把
`DEFAULT_OUTPUT` 改到 `tmp/` 或加一道「輸出檔已存在需 `--force`」保護），並在下一批把
`small_blocked_until_big_finished_fraction` 的敘述改得不那麼武斷，其餘量測方法與揭露均誠實、
可重現，核心結論（parser lock 完全序列化＋cancel 無法中止已進入 render 的呼叫）由原始碼與
獨立重跑證據雙重支持，成立。
