# 發布前補強與剩餘驗收

## 最新候選：共用高亮行表（2026-10-23）

最新候選與證據改見[共用行表更新報告](2026-10-23-shared-lines-release.md)：2175項一般測試與119項WebEngine整合通過，封裝smoke通過；同環境首屏P95 1429.63→973.30ms，500ms目標未達。安裝、升級、解除安裝由使用者驗收，不再要求額外Windows VM。以下段落保留為歷史紀錄；未正式發布。

## 後續產品改善：大型程式碼預覽

本輪已在產品加入完整文字保留的分段高亮；30暖樣本完整文字載入P95由9.30秒降至1.49秒、同步切換P95 40ms。詳見[實作、證據與限制](2026-10-23-code-preview-performance.md)。以下「只改驗收工具」及舊候選包描述是前一階段紀錄，不代表本輪最新工作樹；新的本機候選包核對資訊將列於新報告。正式發布與實機驗收仍未放行。

## 完整載入驗收標準修正

**歷史 `full_ready_ms` 只檢查非部分預覽與文件標記，不能證明整份 DOM／資源載入完成。** [Browser Timing 診斷](evidence-20261022/browser-timing.json)的多筆樣本在舊探針通過時 `domComplete`／`loadEventEnd` 仍為0。本輪修正 `tools/benchmark_file_switching.py`：完整載入必須先通過 `document.readyState === 'complete'`，再檢查標記與獨立雙frame token；等待載入時不反覆讀取全文。首屏及完整載入不得共用已通過的frame token。

`RUN_WEBENGINE_TESTS=1 python -X utf8 -m pytest tests/test_benchmark_file_switching.py -q --tb=short`：**3 passed in 0.61s，exit 0**；Ruff通過。真Chromium測試覆蓋loading不讀全文、完成後獨立frame、partial及錯誤文件拒絕。loading狀態使用屬性覆寫模擬，並非慢速資源整合測試；complete＋雙frame也不是所有延後工作或實體螢幕完成。

此輪只改驗收工具與測試，非產品加速。既有候選EXE內仍是舊探針；其功能smoke證據保留，但舊完整載入時間不可與新指標混比。第一版新增complete但在全文讀取後檢查的中間測量已停止（child exit15、guard資料不變），不作完整回歸證據。

修正後執行 `tools/benchmark_file_switching.py --repeats 15 --size 1000000 --category code_heavy --overall-timeout 600`，2文件共30暖樣本：[結果](evidence-20261022/complete-probe-early.json)首屏可讀P95 **1777.14ms**、完整載入P95 **9303.96ms**、同步P95 **57.14ms**；exit0，[guard](evidence-20261022/complete-probe-early-guard.json)確認設定與持久資料不變。每份文件等待完整載入才切換，工作負載時序與舊流程不同，不得將首屏差異也直接當成產品回歸。大型程式碼文件完整載入成本仍未解決，禁止據舊full_ready宣稱已達標。

重新核對建置manifest，唯一變動為 `tools/benchmark_file_switching.py`；app／assets均未變更。產品候選包保留，打包診斷入口須在未來重建後才能使用新版完整載入標準。

獨立審查建議補強錯文件測試的獨立性：移除partial標記後，再驗證錯generation／marker各自拒絕。最終版本重跑同一真Chromium指令：**3 passed in 2.91s，exit0**，Ruff通過。

## 後續渲染瓶頸診斷（未修改產品）

分段診斷 [render-stages-safe](evidence-20261022/render-stages-safe.json) 最後一個暖樣本：開檔後約51ms提交HTML、73ms開始導航、481ms通過可讀探針；同步主視窗約34ms。前述51／73ms為時間點，不是方法執行耗時（對應方法約0.15／2.98ms）。此為4個暖樣本的定位資料，不是新的P95驗收。初版包裝Qt訊號接收方法後逾時，移除該包裝後完成；初版失敗不可據以斷定產品有相同問題。

兩個隔離試驗均未納入產品：

- 將探針整份body.innerText改讀首個h2：30暖樣本可讀P95 626.22ms，[結果](evidence-20261022/heading-probe.json)。探針不同，不能直接與舊數字視為產品改善；500ms目標仍未達。
- 對程式碼區塊試用 `content-visibility:auto` 及估計高度：30暖樣本可讀P95 767.73ms，[結果](evidence-20261022/code-visibility-warm.json)。未帶來所需效益，不引入可能影響捲動與匯出的排版變動。較早code-visibility.json每輪清除cache，不能當作暖開檔比較。

有效試驗均exit0且各自guard確認設定／持久資料不變。產品與上輪候選版保持一致，未重建或發布；大型文件瀏覽器排版與DOM成本仍待進一步分析。

## 最新候選產物（2026-10-23）

含 ASCII 計字與最近清單寫入修正。`python -X utf8 tmp/build_recent_delivery.py` 的 PyInstaller／Inno 均 exit 0；[建置 manifest](evidence-20261022/recent-build-manifest.json)確認建置前後來源一致。仍為待實機驗收候選版，未安裝、未發布。

獨立驗收重算617個來源及2個產物，全部符合：[驗收結果](evidence-20261022/recent-artifact-audit.json)，passed=true。

| 產物 | bytes | SHA-256 |
|---|---:|---|
| [EXE（須保留整個資料夾）](../../dist/upgrade-20261023/MarkdownViewer/MarkdownViewer.exe) | 9754959 | `08fd57249dcc25e81936e4165c9ff71dcfe9d0e1ecb7845832a7f79f7a750717` |
| [候選安裝包](../../installer_output/upgrade-20261023/MarkdownViewer_Setup_v1.36.0.exe) | 166136173 | `9fdafd56fc36f03addb4aa0ab4a5f5233d60f548f3afbdf78f86e21b8e1bdf11` |

最新來源完整回歸：`python -X utf8 -m pytest tests/ -vv --tb=short -p tests.qt_lifetime_probe`（offscreen、disable-gpu）→ **2167 passed, 143 skipped, 2 warnings in 130.89s；exit 0**，見[日誌](evidence-20261022/recent-final-pytest.log)。跳過的實機／opt-in項目仍未驗證。

新版 `dist/upgrade-20261023/MarkdownViewer/MarkdownViewer.exe` 的隔離開檔 smoke：`tools/benchmark_packaged.py --working-set 2 --repeats 2 --size 100000` → **passed=true、exit_code=0**；[結果](evidence-20261022/recent-exe-smoke.json)與[guard](evidence-20261022/recent-exe-smoke-guard.json)確認原生設定及持久資料不變。此輪與安裝包壓縮並行，只有4暖樣本，僅作打包功能驗證，不用來宣稱效能或記憶體達標。

## 最近清單寫入與完整視窗追查

本輪將最近清單路徑與時間戳改為共用單一 QSettings 生命週期，避免 INI 儲存方式在每次開檔時分別 flush。排序、上限、清除與遷移行為保持；不是原子交易，也未驗證一般 Windows Registry 的速度收益。相關 recent_files／window_integration 含 probe：167 passed in 24.19s；新增獨立程序讀回單筆繁中、多筆、清空三種情境後 recent_files：11 passed in 1.44s，Ruff 通過。讀回須遵循現有 `_load` 對 INI 單筆字串與空值的正規化。

完整視窗 1 MB code_heavy、2 文件、15 輪，共30暖樣本：修正前 [測量](evidence-20261022/ascii-full-window.json)可讀 P95 953.69ms，修正後 [測量](evidence-20261022/recent-batched.json)649.17ms；同步 P95 159.54→52.42ms。額外細項追蹤中 recent.add 中位數40.77ms（4暖樣本）→25.61ms（30暖樣本），樣本數不同且非交錯A/B，不能將完整差異全歸因此改動。仍未達可讀500ms目標。兩次測量均exit0，[修正前guard](evidence-20261022/ascii-full-window-guard.json)／[修正後guard](evidence-20261022/recent-batched-guard.json)確認原生設定與持久資料不變。測試使用offscreen及隔離INI，包含Chromium GLES context回退訊息，不代表實機顯示延遲。

**下列 upgrade-20261022 產物與 build-manifest.json 為歷史版本，未包含本輪計字及最近清單修正；最新產物見本文開頭 upgrade-20261023 表格。**

本輪 `count_writing_units` 對 ASCII 內容使用等價的單字掃描，混合語言保留 Unicode 路徑。驗證連字號、撇號、控制字元及所有 ASCII 字元的邊界語意；初版相關 `pytest tests/test_editor_status.py tests/test_editor_data_safety.py tests/test_session_restore.py -q --tb=short -p tests.qt_lifetime_probe`：105 passed in 29.54s，exit 0，Ruff 通過。最終 regex 修改後的驗證另列下段，沒有將初版105案冒充最終版全套。

獨立審查發現初版重複群組在百萬字元連字詞上配置約62.8MB暫存，已改固定長度 lookbehind，只辨識單字起點。新增長連字詞 peak < 1MB 回歸測試，最終 editor_status 含 probe：36 passed in 2.03s，Ruff 通過。

以 `PYTHONPATH=.` 執行 `python -X utf8 tmp/benchmark_ascii_count.py`，同程序交替比較原分類迴圈與最終路徑各7次；1.08 MB／144000字的中位數從134.19降至34.46ms，約3.9倍。[數據](evidence-20261022/ascii-count-benchmark.json)僅代表計字元件，不代表完整開檔、中文超長行或畫面渲染已達標。

本輪接續 1.36.0 候選版，尚未對外發布。GitHub workflow 改成建立 draft 且固定 prerelease，正式穩定版須在完成驗收後明確修改發布設定。設定參照 [action-gh-release 官方輸入說明](https://github.com/softprops/action-gh-release#inputs)；本機尚未實跑 GitHub Actions。

更新器另拒絕 draft／prerelease metadata，避免其進入穩定更新提示。實跑鎖版 Python：`-m pytest tests/test_updater.py tests/test_update_flow.py tests/test_release_notes.py -q --tb=short`，77 passed in 1.80s；Ruff 通過。獨立代理另跑 updater，46 passed in 3.45s。

新版安裝腳本僅登錄 `.md`／`.markdown` 的 `OpenWithProgids` 具名值，不覆寫目前預設處理程式及 Content Type。乾淨安裝時，新腳本的解除安裝項目僅移除自己的具名值與 ProgID；實際行為仍待下列 VM 驗證。

**既有版本升級仍未驗證**：相同 AppId 可能累積舊 uninstall log，當中的舊 `uninsdeletevalue` 紀錄可能在解除安裝時移除原預設關聯。新版腳本不會自動消除舊安裝日誌；不得宣稱覆蓋升級後解除安裝已安全。沒有操作使用者真實 Registry 或執行安裝來猜測結果。

本機未找到 WindowsSandbox.exe；未建立乾淨隔離 Windows 帳號或 VM。因此下列仍須實機補驗：

1. 在 VM 為 `.md`／`.markdown` 設定其他程式為預設，記錄預設值、Content Type 與 OpenWithProgids。
2. 乾淨安裝候選版，確認預設不變、開啟方式可選 Markdown Viewer，路徑含空白及繁中正常。
3. 解除安裝，確認原預設保留且只移除自己的開啟方式。
4. 回復 VM 快照，安裝舊版後覆蓋候選版，再解除安裝，核對舊 uninstall log 的影響；失敗則禁止正式發布。
5. 安裝後測中文組字、DPI／雙螢幕、儲存重開與更新失敗回退。

先前 2160／131 測試與效能資料屬上一輪凍結來源；本輪 updater／installer 改動後須使用新產物與建置 manifest，不能沿用先前產物雜湊。

## 本輪完整回歸

實跑 `python -X utf8 -m pytest tests/ -x -vv --tb=short`，設定 `QT_QPA_PLATFORM=offscreen`、`QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu`，取消 opt-in `RUN_WEBENGINE_TESTS`：**2162 passed, 143 skipped, 2 warnings in 121.75s；exit 0**。[完整日誌](evidence-20261022/release-readiness-pytest.log)。兩警告仍為保留外來暫存檔的預期測試。跳過不是通過；未重跑未受修改影響的 Chromium 指定組，其上一輪 131 案證據另列。

本輪第一次普通回歸未設定上述 Chromium flags，在 monkeypatch teardown 出現 Windows access violation，程序 exit 3；[失敗日誌](evidence-20261022/release-readiness-first-failure.log)完整保留。使用與前輪一致環境並改記逐案日誌後通過，**尚未確認原生錯誤根因，不能將環境差異直接當作因果或已修復**。這也是正式發布前必須追查的穩定性項目。

## 補強後產物

### Qt 測試生命週期追查

已確認 `test_status_document_api_avoids_plain_text_snapshot` 在 QTextDocument 已銷毀後才還原其 `toPlainText` 方法。診斷原案例得到 `1 passed, 1 error`，錯誤為 `monkeypatch restore targets deleted QObject`；修正為在持有 editor 時以獨立 context 還原後，editor_status 組 34 passed。這是已定位的測試清理缺陷，但尚未直接重現第一次原生 AV，不能宣稱是唯一根因。

加入明確啟用的 `tests.qt_lifetime_probe`，在還原前檢查 QObject 有效性；無效目標會造成測試錯誤，仍先清理其餘 patch，避免假設定洩漏。修正後的診斷工具首次完整執行得到 2162 passed、143 skipped、11 teardown errors，見 [修正前日誌](evidence-20261022/qt-lifetime-probe-before.log)。這 11 案已調整還原作用域；刻意銷毀視窗的測試改為 patch 類別方法，保持其關閉取消情境與斷言。

早期診斷工具在清理其他 patch 前即拋錯，該次不完整執行已停止，不能作為驗收證據。本次只修改測試；重新核對建置 manifest 的所有來源，`changed_since_build: []`，產品無需因測試修正重建。

修正後實跑 `python -X utf8 -m pytest tests/ -vv --tb=short -p tests.qt_lifetime_probe`（offscreen、disable-gpu、未啟用 RUN_WEBENGINE_TESTS）：**2162 passed, 143 skipped, 2 warnings in 114.72s；exit 0**，無 teardown error，見 [完整日誌](evidence-20261022/qt-lifetime-probe-pytest.log)。兩個警告仍為保留外來暫存檔情境。獨立代理唯讀審查所有修正，確認斷言保留、假設定未提前解除。這排除了本輪普通測試涵蓋的已知無效還原路徑，不能替代原生 AV 唯一根因證明及 Windows 實機驗收。

另取消 `QTWEBENGINE_CHROMIUM_FLAGS`，保留 offscreen，執行 `python -X utf8 -m pytest tests/test_editor_status.py tests/test_annotation_orphans.py tests/test_editor_data_safety.py tests/test_session_restore.py tests/test_window_integration.py -q --tb=short -p tests.qt_lifetime_probe`：**267 passed in 38.28s；exit 0**（終端 session 24719）。本輪相關組不依賴 disable-gpu 通過，但這不是全套無 flags 或實機穩定性證明。

使用 `python -X utf8 tmp/build_upgrade_delivery.py` 重建，PyInstaller 與 Inno 均 exit 0（根代理終端 session 85214），Inno `Successful compile (206.922 sec)`。建置前後來源 hash 相同，[最新 manifest](evidence-20261022/build-manifest.json)記錄完整來源清單。

| 產物 | bytes | SHA-256 |
|---|---:|---|
| [EXE](../../dist/upgrade-20261022/MarkdownViewer/MarkdownViewer.exe)，需保留整個資料夾 | 9754633 | `d7d39014b746badf0954b950d7012b3be239311d7ef890d6f51bf1fee4565512` |
| [安裝包](../../installer_output/upgrade-20261022/MarkdownViewer_Setup_v1.36.0.exe) | 166128651 | `ba5455a80b8ad98bf7799f05915fb542a3d88d85b74433bd62eea10c8766d840` |

此產物仍是待實機驗收候選版，不是正式穩定版。未建立遠端 release、未推送標籤、未執行安裝。

獨立代理重算 [617 個來源與兩個產物](evidence-20261022/release-readiness-artifact-audit.json)，全部符合 manifest。

新版第二實例 smoke 使用隔離 guard 執行，但偵測到既有 MarkdownViewer 程序，工具主動跳過（child exit 2／guard exit 1），沒有終止現有程式。[結果](evidence-20261022/release-readiness-ipc.json)與[guard](evidence-20261022/release-readiness-ipc-guard.json)已保存；指定設定與持久資料前後一致，但 **新版第二實例轉送未驗證**。待關閉日常程式或在獨立帳號執行 `python tools/smoke_packaged_forwarding.py dist/upgrade-20261022/MarkdownViewer/MarkdownViewer.exe --output tmp/release-ipc.json`，需正常退出且 path_matches=true。
