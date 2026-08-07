# PDF A+B 非同步預覽與分塊渲染

日期：2026-08-07

## 已完成範圍

- A：縮放期間保留最近倍率的完整頁 raster，直接拉伸作即時預覽。
- A：停止縮放 120 ms 後才精確重繪，不在 `paintEvent()` 呼叫 PDF render。
- B：大頁面改用 512×512 physical-pixel tiles，只優先渲染 viewport 可見區域。
- B：使用 192 MiB byte-LRU，逐筆淘汰，不再以固定張數整批清空。
- B：`QPdfPageRenderer.MultiThreaded` 在背景產生 `QImage`；GUI thread 只做小型 `QPixmap` 上傳與合成。

## 渲染流程

```text
Ctrl+wheel packets
  → 16 ms 合併一次倍率與版面更新
  → 舊 raster 即時縮放預覽
  → 120 ms idle
  → 可見頁／可見 tiles（中心優先）
  → viewport 外一圈 prefetch
  → exact result 逐塊替換 preview
```

每次 PDF load 都建立 immutable private `QPdfDocument` render session。舊 session
可以完成已送出的最多兩個工作，但 generation、layout epoch、DPR 與 wanted-key
檢查會阻止結果污染新文件或新倍率。

## 分塊與正確性細節

- 整頁 raster 上限：最大邊 4096 physical pixels，且 ARGB 不超過 32 MiB。
- 超過任一上限時改用 tiles；另保留最大邊 2048 pixels 的 whole-page preview。
- Qt 6.11 tile clip 使用 `QRect(x, y, width + 1, height + 1)`，輸出尺寸仍為
  `width × height`，避免 Qt PDFium clip 少掉右／下邊一列。
- PDFium 的透明 raster 先對 exact tile 區域清成白色，再合成 tile，避免 preview
  與半透明 exact pixels 疊兩次而變深。
- DPR 是 cache key 的一部分；`QPainter` source rect 使用 pixmap physical pixels。
- tiled paint 只繪製與 viewport 相交的 cached tiles。

## 驗證摘要

使用 `E:\PD協定\CCGx_Power_SDK_API_Guide.pdf`（752 頁）作實檔驗證：

- DPR 1：24 個細 wheel packets 加版面更新與 preview paint，p50 1.80 ms、p90 2.49 ms。
- DPR 2 模擬：同一流程 p50 5.78 ms、p90 6.87 ms。
- DPR 2、20 個可見 tiles：preview 約 7.63 ms；輸入後 147.5 ms 出現第一個 exact tile，
  196.0 ms 內可見 tiles 全部精確化（含 120 ms idle）；GUI event gap 最大 5.23 ms。
- DPR 1 與 DPR 2 的 preview/exact 截圖已人工檢查，位置一致且未見 tile seam。

自動化測試另涵蓋 byte-LRU、MultiThreaded mode、request ID 0、tile 無縫重組、
透明合成、DPR、stale generation/epoch、同時工作上限、密碼 PDF 與切檔生命週期。
完整測試結果：`513 passed, 9 skipped`。

## 已知非阻擋限制

- Qt PDF 沒有取消已提交工作的 API；極端連續 reload 時，舊 generation 每代最多兩個
  request 會暫時繼續，完成後自動 retire。正常切檔不會混入舊內容。
- view 銷毀時 Qt 會等待 active raster：752 頁實檔的 2048 preview／512 tile smoke 為
  5.23–7.19 ms；人工 12,000 線、2600×1800 的極端頁面約 402–425 ms。
- 32 MiB whole-page `QImage → QPixmap` 仍在 GUI thread；本機上限樣本約 7.8 ms，
  尚在單幀預算內。512×512 tile 約 0.31 ms。
- 每次 render session 需同步開啟第二份 immutable document；752 頁 warm sample 約增加
  37–39 ms，換取 reload／切檔時不會把新文件內容渲染到舊 request。
- DPR 2 已用 Qt offscreen scale 模擬並做截圖 QA，仍建議發版前在實體多螢幕環境走一次
  跨螢幕 DPR smoke test。

## C：Qt Quick/RHI 後續邊界

C 可以接續實作，但屬於 canvas backend 遷移，不是 A+B 的開關。現有 scheduler、
tile planning、generation guards 與 byte-LRU 已和 QWidget compositor 分離；後續可把
cache value 從 `QPixmap` 換成 GPU texture handle，再以 Qt Quick Scene Graph/RHI
做 texture transform/composite。PDFium 的 PDF rasterization 仍會在 CPU，GPU 主要負責
預覽縮放、tile texture 與 overlay 合成。
