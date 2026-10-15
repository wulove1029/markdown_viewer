# 開檔順暢度實作進度

日期：2026-10-15（Asia/Taipei）。基準 commit：6914c03，版本 1.34.0。
使用者已授權直接完成三批改善及必要驗收，範圍以已啟動程式的換檔與切分頁為主。

## 執行與驗收清單

- [x] 讀取既有計畫、現況及全域判斷規則；保留前一輪計畫／證據。
- [x] 切分平行實作：主視窗整合／量測、Markdown renderer、metadata、PDF。
- [x] 保存基準程式快照並建立端到端切換量測工具。
- [x] 第一批：非同步標籤、跨類型取消、還原編輯器不建立隱藏預覽。
- [x] 第二批：延後輕量載入提示、重用既有快取、閱讀位置保留。
- [x] 第三批：PDF 有界熱門頁預覽、附屬資訊背景化、分批尺寸；中型複雜 Markdown 與 TXT 開檔。
- [x] 相關測試、完整預設回歸、MainWindow offscreen smoke、同機前後量測。
- [x] Fresh agent 獨立驗收與 PDF 部分尺寸快取追加審查。
- [ ] 鎖版打包 EXE、實體桌面操作與程序樹 RSS 驗證（本次未完成，不能視為發版驗收）。

## 已確定的介面

- `RendererView.deactivate()` 取消不再顯示的工作；`content_ready(generation, path, partial)` 支援呈現近似量測。
- `document_metadata` 保持純 Python；標籤優先共用 converter 讀取／解析結果，編輯模式可單獨背景擷取。
- PDF 保留 generation guard，跨文件預覽另外受總容量與文件版本約束。
- Office live snapshot、dirty QTextDocument、undo、草稿與外部修改衝突保持既有資料安全流程。

證據將逐步寫入 `docs/upgrades/evidence-2026-10-15-file-open-implementation/`。未完成的項目不得以測試通過或元件耗時代替整體驗收。

## 續作驗證紀錄

- 主視窗 100 KB Markdown 暖切換前後各 60 次：可讀代理時間 P95 219.88 → 136.78 ms；UI heartbeat gap P95 41.51 → 22.45 ms。原始結果 `mainwindow-before.json`、`mainwindow-after.json`，兩者 exit 0。此為 offscreen DOM 雙 frame 代理，非實體螢幕首幀。
- 新增 TXT／metadata／benchmark helper 測試：27 passed，exit 0。
- 主視窗與編輯工作區首輪：206 passed、2 failed；兩項舊 fake renderer 尚未發送非同步完成通知，已修測試，其後完整回歸已通過，見下節。
- 獨立審查通過；另獨立 TXT 安全探測 2 passed。補修來源／Office parked 返回預覽時重新建立 renderer 文件身份、離開 split 取消隱藏工作。
- 初期 PDF 100 頁暖切換改善，但 3,000 頁 P95 84.53 → 128.22 ms 退步；當時待定位，後續修正見下節。
- properties panel 目前為下一 UI turn 延後同步讀取，沒有全面搬到背景。
- 本機部分檔案 I/O 曾停頓超過 120 秒；中止的回歸執行不計通過。測試環境為 Python 3.13.6／PySide6 6.11.0，非 lock 版打包驗證。

## 最終修正與驗收

大型 PDF 的初期退步已定位：快速切換早於 idle 尺寸批次完成，完整尺寸快取一直未建立，返回時反覆呼叫 PDFium 查詢閱讀頁尺寸。現在部分尺寸與完整尺寸共用既有 8 文件／50,000 頁 LRU，以 `None` 標示未知頁面；返回時保留已知尺寸並繼續分批補齊。強制重載與外部變更仍失效，部分資料不會被當作完整尺寸。

| 情境與範圍 | 原版 P95 | 最終 P95 | 證據 |
|---|---:|---:|---|
| MainWindow 100 KB Markdown 暖切換可讀代理，各 60 次 | 219.88 ms | 136.78 ms | `mainwindow-before.json` / `mainwindow-after.json` |
| 同組 Markdown UI timer 最大 gap | 41.51 ms | 22.45 ms | 同上 |
| PdfView 100 頁返回目標 raster，各 90 次 | 18.84 ms | 10.97 ms | `pdf-raster-before.json` / `pdf-raster-after.json` |
| PdfView 3,000 頁返回目標 raster，各 90 次 | 84.53 ms | 60.36 ms | `pdf-raster-before.json` / `pdf-raster-fixed-3000.json` |

PDF 表格是元件量測，不能當作完整 MainWindow 數字。3,000 頁最終 timer gap P95 55.85 ms，仍高於提案的 50 ms；首次冷開與網路／雲端檔案未證實全面達到改善 30% 的目標。Markdown 100 KB 可讀 P95 已低於提案的 150 ms。

完整預設回歸：**1,864 passed、89 skipped，86.55 秒，exit 0**，原始 JUnit 在 `full-regression.xml`。跳過原因：82 個需啟用 WebEngine、5 個缺私人 Acrobat PDF、2 個缺第二磁碟 fixture。補修聚焦測試 19 passed；新增模組測試 27 passed；Ruff 全 app/tests/tools、mypy 設定的 5 個來源檔與 `git diff --check` 全部通過。

真實 Chromium 組合執行：123 passed、2 failed，兩項失敗為 Office 首次 `ready()` 等待逾時；其餘 98 個非 Office 案例與 25 個 Office 案例成功。將 Office 組隔離到新程序後 **27 passed，45.60 秒，exit 0**；另真實 renderer context menu **1 passed**。因此分組已涵蓋原先跳過的 82 個 WebEngine 案例，但不能宣稱最初組合程序全綠，也未證明逾時的確切環境原因。保留 `webengine-combined.xml`、`webengine-office-isolated.xml`。上述組別含重疊測試，不加總為不重複總數。

獨立驗收證據：`independent-review-final.txt`、`independent-partial-geometry-review.txt`；另有真實 TXT 跨檔取消／載入中誤存防護測試 2 passed。MainWindow 截圖已核對可讀內容與分頁；offscreen 原生中文字型顯示方框，不能作為實體桌面字型／視覺驗收。

原始碼重跑：

```powershell
$env:PY_PYTHON3='3.13'
py -3 -X utf8 -m pytest tests -q -p no:cacheprovider
$env:RUN_WEBENGINE_TESTS='1'
py -3 -X utf8 -m pytest tests/test_renderer_switching.py tests/test_renderer_source_navigation.py tests/test_wysiwyg_webengine.py -q
py -3 -X utf8 tools/benchmark_file_switching.py --output tmp/switch-result.json --repeats 30
py -3 -X utf8 tools/benchmark_pdf_switching.py --pages 3000 --reps 30 --rounds 3 --output tmp/pdf-switch-result.json
```

桌面／EXE 待驗步驟：依 DEVELOPMENT.md 在鎖版環境打包；開 A.md、B.md、PDF 與 5 MB TXT，連續切換並核對最後選定文件、捲動位置；來源與 Office 輸入未儲存文字後往返，確認文字與 undo 保留；PDF 回到原頁、外部修改後重載，確認不顯示舊 raster。現有安裝版 EXE 未被覆寫，本次沒有發版。
