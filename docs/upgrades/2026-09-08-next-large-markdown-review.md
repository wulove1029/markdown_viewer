# 驗收覆核：大型 Markdown 載入（commit 959f1c0）

覆核者：獨立 fresh reviewer（未參與實作）。日期 2026-09-08。
對象：worktree `E:\markdown_viewer\.claude\worktrees\batch3-large-markdown`，
分支 `feature/large-markdown-render`，commit `959f1c0`（父 `26d17e0`）。
規格：`docs/CLAUDE-CODE-NEXT-OPTIMIZATIONS.md` 第 3 節 B／C／D 與第 6 節。
環境：Windows 11、Python 3.14.4、PySide6/Qt 6.11.0、`QT_QPA_PLATFORM=offscreen`、未封裝。

## 逐條結論

### 1. 安全（loopback socket / token / pickle）— PASS（有一項待改善）

- 只綁 127.0.0.1：`app/render_service.py:_spawn()` 用 `server.bind(("127.0.0.1", 0))`，
  ephemeral port，`listen(1)`，**accept 一次後 `finally: server.close()`**，不留常駐監聽埠。
- token：`secrets.token_bytes(32)`，子程序連上後先送 32 bytes raw greeting，
  父程序用 `hmac.compare_digest` 比對；**比對在任何 `pickle.loads` 之前**（`_spawn()` 用
  `_recv_exactly()` 讀原始 bytes，不經 `recv_frame`）。不符即 `sock.close()` + `proc.kill()`。
- **缺點（Medium-low）**：token 以 **命令列參數** 傳遞
  （`_worker_command()`：`[exe, "--render-worker", str(port), token.hex()]`），
  同機可讀取該程序 CommandLine 者（同使用者可直接讀；他人需 admin/SeDebugPrivilege）能取得 token。
  取得後仍須在父程序 `accept()` 前搶先連上（實測子程序啟動約 145 ms，競爭窗口小但存在），
  搶到即可送 pickle frame，父程序 `pickle.loads` 便是主程序內任意程式碼執行。
  建議：改由 stdin（目前是 DEVNULL，正好可用）或繼承 handle 傳 token，並加 per-connection challenge。
- 無 token 的本機程序：只能搶下唯一一次 accept 讓握手失敗，導致 `RenderWorkerError` 並退回行內解析（DoS，非 RCE）。
- 其他：`_MAX_FRAME_BYTES = 256 MB`，惡意或異常子程序可迫使一次大量配置（Low）。
- 缺測試：沒有「token 不符被拒」「非預期連線」的自動測試（Low）。

### 2. 程序生命週期 — PASS

- 取消等於真殺程序：`RenderService.render()` 收到 `RenderWorkerCancelled` 即 `_retire()` 呼叫 `_Worker.kill()`
  （`sock.close()` + `proc.kill()` + `wait(timeout=5)`）。取消輪詢間隔 `_POLL_S = 0.02`，有界返回；
  實測 `cancel_switch.big_stop_after_cancel_ms` p50 = 37.6 ms（我重跑）／44.6 ms（紀錄），
  `render_worker_processes_killed = 1`。
- 逾時：`_REQUEST_TIMEOUT_S = 180`，`_recv_exactly()` 每次 recv 前檢查 deadline；
  握手 `_HANDSHAKE_TIMEOUT_S = 30`。崩潰或逾時會 `_retire()` + `prewarm()` + 行內回退。
- 主程序退出：`atexit.register(shutdown)`；被強制殺時子程序的 `recv` 得到 EOF，`_serve_connection` return，程序自行結束。
  **我實測**：以 `main.py --render-worker` 起子程序，父端關閉 socket 後 `p.wait()` 回 0，無殘留。
- 沒有 `QThread`：渲染走 `QRunnable`／`QThreadPool`（`app/renderer.py:_MarkdownRenderWorker`），
  `render_service` 只有 daemon 的 `render-prewarm` thread，無執行中銷毀 QThread 的情形；
  未使用 `QThread.terminate()`（符合規格 3.B）。
- Windows 旗標：`subprocess.Popen(..., stdin/stdout/stderr=DEVNULL, creationflags=CREATE_NO_WINDOW)`，
  windowed 封裝版不應彈黑窗（讀碼判斷；未實跑封裝版）。
- 測試：`tests/test_render_service.py` 20 項全過（真子程序），含 kill／timeout／crash 換新／
  `shutdown_leaves_no_child_process_behind`／20 次重複開啟回收（`_live <= 2`）。

### 3. frozen 模式 — PASS（附一項文件不實）

- `main.py` 的 `--render-worker` 分流在 `if __name__ == "__main__":` 最前面，
  **早於 `main()` 與 `QApplication(sys.argv)`**，也早於單一實例 IPC。正確。
- `.spec` `hiddenimports` 加了 `app.render_service`、`app.render_worker`；
  子程序真正需要的 markdown-it／mdit_py_plugins／pygments 既有條目已涵蓋。
- **實跑 smoke（我做的）**：`py -3 -X utf8 main.py --render-worker <port> <token>`，
  握手成功（startup 144.5 ms）、`render_text` 回傳正確 body、未知 op 回錯誤而不崩、
  父端關閉後子程序 exit 0。`main.py --render-worker`（缺參數）回 usage、exit 2。
- **不實描述（Low）**：紀錄與 docstring 稱子程序「不 import Qt」。
  源碼模式屬實（我實測子程序 `sys.modules` 無 PySide6）；但**封裝模式**執行的是 `main.py`，
  其 module-level 已 import PySide6，故 frozen 子程序會載入 Qt，RSS 與啟動成本高於量測值。未封裝驗證。

### 4. 規格 B（最新文件優先／過期不回寫）— PASS

- 過期排隊任務不開始昂貴轉換：`_MarkdownRenderWorker.run()` 開頭與 `_emit_fast_preview()` 內多處
  `cancel.is_set()` 檢查；大檔在子程序，取消時直接殺程序（不只丟 callback）。
- 過期結果不回寫：`RendererView._stale_render()`（generation + path + is_markdown）同時守住
  `_on_file_render_ready` 與 `_on_file_partial_ready`。行為測試：
  `tests/test_fast_preview.py::test_stale_partial_view_never_touches_the_page`
  （舊 generation／別的文件時不 setHtml、不設 partial 旗標、不推 TOC）、
  `tests/test_renderer_async.py` 既有 generation 測試全過。
- 目錄：`_on_headings_ready` 只在通過 `_stale_render` 後呼叫。

### 5. 規格 C 資料安全 — PASS（一項部分達成）

- 部分內容來源只有磁碟：`_emit_fast_preview()` 用 `md_converter.read_text(path)`，不碰草稿緩衝區。
- 不會被當完整內容儲存、匯出、寫復原快照：
  - 儲存與復原快照一律來自編輯器 buffer（`app/window.py` 的 `document().toPlainText()` 與
    `app/recovery.py:RecoveryStore.save`），渲染器 HTML 從不進入這條路徑。
  - PDF 匯出：`export_pdf()` 在 `_partial_preview_active` 時把請求存入 `_pending_export`，
    等完整頁 `_on_markdown_load_checked` 才執行；文件被換走時 `_begin_render` 會回呼 `on_done(path, False)`，
    不靜默吞掉。測試 `test_pdf_export_waits_for_the_full_document`。
- 切到編輯載入完整內容：`app/window.py` 進編輯讀 `read_text_detailed(self._current_file)`（整檔）。
- 明確告知範圍：`partial_preview_notice_html()` sticky 橫幅寫出百分比、KB/MB，
  並明講「目錄、搜尋與行號定位只涵蓋已載入的區塊」。
- **部分達成**：部分頁上使用者手動搜尋只出現在後段的詞，**只會找不到、無提示**（實作者自述屬實）。
  規格要求「導航到對應區塊或明確要求完整預覽，不可定位到錯誤位置」。
  **沒有錯誤定位**（前綴的 `data-src-start` 由檔首開始，行號真實），但缺主動提示，判定部分達成（Low）。

### 6. 分段邊界 — PASS

`fast_preview_split()` 以 token／區塊邊界切：只在 **空行** 切，且該空行必須不在
front matter、fenced code（``` 與 ~~~，長度與字元皆比對，支援巢狀）、`$$` 數學、`<details>` 內。
掃描超過 `max_scan_bytes` 仍無安全點就整份返回（呼叫端改顯示載入頁，不假裝部分渲染）。

我另寫的刁鑽案例實跑結果（全部符合預期）：

| 案例 | 結果 |
| --- | --- |
| 切點落在會關閉的 fence 內 | 不切開，延到 fence 之後（1219 B） |
| fence 永不關閉 + scan 上限 | 放棄（回傳整份） |
| 切點落在表格中間 | 延到表格結束（3425 B） |
| 切點落在 `$$…$$` 數學區塊 | 延到 `$$` 之後（1411 B） |
| 切點落在 `<details>` 內 | 延到 `</details>` 之後（1457 B） |
| 4 個 backtick fence 內含 3 個 backtick 巢狀 fence | 只認 4 個收尾，正確 |
| `~~~` fence | 正確 |
| 清單跨界 | 延到清單結束 |
| frontmatter 內含空行 | 不在 `---` 之間切 |
| CRLF 檔 | 正常 |
| reference link 定義在後段 | 前綴以字面 `[ref][a]` 呈現（橫幅已說明） |
| footnote 定義在後段 | 前綴不產生 footnote，無錯誤連結 |
| 單一巨大 paragraph | `render_partial_body()` 回 `None`（保留載入頁） |

已知未追蹤：**4 空格縮排的 code block** 內的空行是合法切點，會被切開（Low，視覺瑕疵，不影響行號）。

### 7. 效能證據 — PASS（同工具、同量級）

`docs/benchmarks/large-markdown-after-2026-09-08.json` 的 `config.output` 即該路徑，
`environment` 與 `seed`（20260908）與工具一致，確為同一工具產出（`--categories long_prose`，非全部壓力案例）。

我重跑（`--sizes 100kb,5mb --categories long_prose --skip-cold --skip-webengine --skip-memory`，
輸出到 scratchpad，未寫入 repo）：

| 指標 | 紀錄 | 我重跑 | 判定 |
| --- | --- | --- | --- |
| 5 MB 可閱讀首屏 p50 | 228.2 ms（fast_text_prefix，覆蓋 10.5%） | **204.8 ms**（同模式、同覆蓋率） | 同量級 |
| 5 MB 解析中切 100 KB p50 | 36.3 ms | **41.6 ms** | 同量級 |
| 100 KB 單獨基準 p50 | 40.4 ms（warm）／32.7 ms（first_readable） | **37.1 ms**（solo 與 FR 皆是） | 同量級 |

附帶：`--quick` 另跑一次 exit 0（100 KB 41.1 ms、1 MB 319.9 ms、big_table 1 MB 1125 ms）。
5 MB 完整預覽 warm 重開 3369.7 ms（紀錄 3156.8 ms），確實只靠快速模式達標，紀錄有分開列出未混報。

**`_CONVERT_LOCK` 對小檔仍會阻塞**（我實測，兩份 200 KB 同時）：

- 標題與連結密集的 200 KB：單獨 262 ms，併發時後到的那份 **523.8 ms**（完全串行化）。
- 純長段落 200 KB：單獨 31 ms，併發 66.8 ms。

即 `SUBPROCESS_MIN_BYTES = 256 KB` 註解寫的「門檻以下小於等於約 90 ms」只對 long_prose 成立，
對密集語法內容低估約 3 倍；門檻以下仍可能互相阻塞約 0.5 秒（未違反 1 秒目標，但註解宣稱未經壓力案例驗證）。

### 8. 測試實跑 — PASS

```
py -3 -X utf8 -m pytest tests -q -p no:cacheprovider --basetemp tmp/next-render-review
→ 1542 passed, 75 skipped in 42.51s，exit code 0

RUN_WEBENGINE_TESTS=1 py -3 -X utf8 -m pytest tests/test_renderer_async.py \
  tests/test_renderer_source_navigation.py tests/test_render_service.py tests/test_fast_preview.py \
  -q -p no:cacheprovider --basetemp tmp/next-render-review-web
→ 49 passed in 4.85s，exit code 0（0 skipped，WebEngine 案例確實有跑）
```

跑完檢查：

- 真實 recovery 目錄 `C:\Users\USER01\AppData\Roaming\markdown-viewer\Markdown Viewer\markdown-viewer\recovery`
  為 **0 個檔案，無殘留**。
- `C:\Users\USER01\AppData\Roaming\python\markdown-viewer\recovery-leaked-backup-20260908` 仍在，
  但那是 **批次 2（更新下載）** 已記錄的舊外洩備份（見 `docs/upgrades/2026-09-08-next-update-download.md:187`），
  ctime 2026-08-25、mtime 10:58（早於本次測試），**與本分支無關**。
- `Get-CimInstance Win32_Process` 顯示 **無任何 `--render-worker` 殘留程序**（只有其他 session 的 pytest 與 MCP 程序）。

### 9. 保留 1.31.0 行為 — PASS

`tests/test_renderer_source_navigation.py`（含 `RUN_WEBENGINE_TESTS=1`）全過；
`app/renderer.py:show_pending_recovery` 未被本次 diff 觸及；pending recovery 相關測試在全套 1542 內通過。
部分頁會把被吃掉的 pending find 與 scroll 存進 `_partial_restore`，完整頁載入時還原
（`test_partial_page_does_not_swallow_a_pending_search_or_scroll`、
`test_full_page_keeps_the_reader_where_they_scrolled_in_the_prefix`）。

## 挑錯清單（依嚴重度）

1. **[Medium-low, 安全]** token 經 argv 傳遞（`app/render_service.py:_worker_command`）。
   能讀 CommandLine 又搶贏 accept 競賽者可送 pickle，造成主程序 RCE。建議改 stdin 或繼承 handle。
2. **[Medium-low, 效能宣稱]** `SUBPROCESS_MIN_BYTES` 註解「小於等於約 90 ms」未經壓力案例驗證；
   實測密集語法 200 KB 單獨 262 ms、併發 524 ms，門檻以下仍完全串行化。
3. **[Low, 文件不實]** 「子程序不 import Qt」只在源碼模式成立；frozen 走 `main.py`，會載入 PySide6。
4. **[Low, 證據品質]** 交付的 after JSON 仍帶著永遠為 1.0 的
   `small_blocked_until_big_finished_fraction`（`tools/benchmark_markdown_first_load.py:419-425`
   在 `thread.join()` 之後才讀 `big_done`）。實作者已在紀錄 5.1 揭露，但 JSON 本身仍誤導。
5. **[Low, 規格部分達成]** 部分頁手動搜尋後段內容只會「找不到」，無「請等完整預覽」提示。
6. **[Low, 測試缺口]** 無 token 不符或惡意連線被拒的測試；無「父程序被殺後子程序自清」的自動測試
   （我手動驗過，exit 0）。
7. **[Low, 邊界]** `fast_preview_split` 不追蹤 4 空格縮排 code block，其中的空行是合法切點。
8. **[Low, DoS]** `_MAX_FRAME_BYTES = 256 MB` 允許單次大量配置。
9. **[Info]** 5 MB 完整預覽冷開 2345.7 到 2487.1 ms（+6.0%，子程序啟動成本），紀錄已誠實列出未混報。
10. **[Info]** 封裝版（PyInstaller）與實體視窗首屏 **未驗**，規格第 6 節明文要求須跑封裝版。

沒有發現「宣稱做了但沒做」的項目：紀錄第 4 節列的 7 項未做或未驗，我逐項核對後全部屬實且無隱瞞。

## 結論

**可合併**。合併前建議修第 1 項（token 移出 argv，小改動）與第 4 項（修掉或移除誤導欄位）。
第 2、3 項建議同批改註解與文件。**發布前必須補**：第 10 項封裝版實跑
（`py -3 -m PyInstaller markdown_viewer.spec` 後開 5 MB 文件，確認出現第二個 `MarkdownViewer.exe`
子程序、無主控台黑窗、橫幅顯示並自動接上完整版）。
