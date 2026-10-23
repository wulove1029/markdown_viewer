# 大型程式碼預覽效能改善

本輪改善已實作於工作樹，尚未對外發布。目標是加快大型文件閱讀與分頁切換，同時保留完整文字、搜尋、註記和編輯能力。

## 實作與行為

- `app/code_loading.py` 在背景渲染階段處理超過 2 MiB 的 HTML；僅將含至少 100 個 Pygments span 的程式碼區塊延後套色。全部原文仍在 `<pre>`，保留開頭換行及來源行號。
- `assets/code_loading.js` 在可視區附近還原原有語法標示；JSON payload 對 `<` 轉義且不放進 `<pre>`。已選取、已註記或正在編輯的節點不被替換。
- 原生搜尋期間暫停後續套色，避免搜尋範圍因節點替換失效；清除搜尋後恢復。搜尋期間尚未套色的區塊仍能閱讀與搜尋。
- 既有註記載入與新增註記前準備語法節點；預覽直接 PDF／列印準備所有可安全替換的區塊。受選取、註記或編輯保護的區塊可能維持純文字樣式，文字內容完整。`export_actions._SourceSnapshotExport.start` 的獨立 HTML／PDF 匯出預覽明確設定 `defer_code=False`，避免 `toHtml` 輸出遺失離屏高亮所需資料；Office 匯出路徑不變。
- 以完整 HTML 的 SHA-256 作鍵，快取轉換結果並限制鍵值合計 64 MiB；避免來回切換時反覆轉換，主題或內容變更不會命中舊結果。

## 同一探針的前後比較

環境：Windows、Python 3.13.5／PySide6 6.11.2，offscreen、`--disable-gpu`，兩份約 1 MB、無註記的 code-heavy 文件、15 輪，共 30 個暖樣本；設定與資料均隔離。數字為本機量測，不能外推到所有機器。既有註記會先還原可安全替換的高亮，HTML 匯出使用完整渲染，故不把上述提速比例套用到這兩種情境。

| 指標（暖樣本 P95） | 改善前 | 改善後 |
|---|---:|---:|
| 開檔同步耗時 | 57.14 ms | 40.45 ms |
| 首個可讀畫面探針 | 1777.14 ms | 1233.83 ms |
| 完整文字載入探針 | 9303.96 ms | 1492.70 ms |
| GUI 心跳最大間隔 | 60.61 ms | 42.56 ms |

完整文字載入 P95 減少約 **84%**。兩個冷樣本完整文字由 9.05／8.31 秒變為 4.10／3.56 秒；冷樣本數不足，僅列原始觀察。

探針檢查文件世代、標記、無錯誤頁、無 partial notice、`document.readyState === 'complete'` 與獨立兩個 animation frame。它是載入／繪製代理，不是實體螢幕計時。改善後不等待離屏的所有語法 span；因此此處「完整」指全部文字，不代表全頁所有語法標示均已建立。原先 500 ms 首屏目標仍未達成。

關閉分頁前的程序樹 RSS 單一時點：改善前 4,012,941,312 bytes、改善後 1,265,737,728 bytes；這不是全程峰值或固定記憶體上限。

證據：[改善前 30 暖樣本](evidence-20261022/complete-probe-early.json)、[改善後 30 暖樣本](evidence-20261022/lazy-code-final.json)、[隔離核對](evidence-20261022/lazy-code-final-guard.json)。兩份結果均 passed；改善後 guard 為 exit 0、native_unchanged=true、persistent_data_unchanged=true。

實跑指令（Python 使用 `tmp/upgrade-venv/Scripts/python.exe -X utf8`）：

```powershell
python tools/verify_diagnostic_isolation.py --output docs/upgrades/evidence-20261022/lazy-code-final-guard.json -- python tools/benchmark_file_switching.py --repeats 15 --size 1000000 --category code_heavy --overall-timeout 600 --output docs/upgrades/evidence-20261022/lazy-code-final.json
```

## 功能驗證與限制

`RUN_WEBENGINE_TESTS=1`、offscreen、disable-gpu：`python -m pytest tests/test_code_loading.py tests/test_code_loading_webengine.py -q` → **11 passed in 6.83s**。涵蓋文字與開頭空行、JSON escaping、快取容量、過期搜尋回覆、轉換失敗回報、原生搜尋、1.5× 縮放、遠端捲動補色、選取後解除補色、註記／編輯節點保護、PDF 首末區塊文字、大型 RendererView 既有註記與行內編輯寫回。Ruff 通過。

包含匯出修正的最終非 opt-in 回歸：`python -m pytest tests/ -vv --tb=short -p tests.qt_lifetime_probe` → **2173 passed, 150 skipped, 2 warnings in 119.08s；exit 0**。[日誌](evidence-20261022/code-preview-final-pytest.log)、[隔離 guard](evidence-20261022/code-preview-final-pytest-guard.json)。兩個 warning 為保留外來暫存檔的預期情境；150 個跳過不算通過。初跑兩個失敗是舊測試 stub 缺新增 helper，補齊後完整重跑通過，原始失敗日誌仍保留。

fresh 獨立驗收另跑新功能、renderer async、source navigation：**22 passed in 8.26s，exit 0**，並核對效能 JSON 與本報告數值／限制。

擴大 Chromium 組初跑 105 個功能斷言通過，但 lifetime probe 揭露 4 個既有測試在 QObject 刪除後才還原 instance monkeypatch。改為 class patch，保留原斷言；同時補獨立 HTML 匯出完整高亮測試。修正後相關組 **55 passed in 35.68s**。

最終設定 `RUN_WEBENGINE_TESTS=1`，實跑：

```powershell
python -m pytest tests/test_code_loading.py tests/test_code_loading_webengine.py tests/test_renderer_async.py tests/test_renderer_source_navigation.py tests/test_renderer_page_rotation.py tests/test_renderer_switching.py tests/test_annotation_bridge.py tests/test_inline_edit_webengine.py tests/test_pdf_export_webengine.py tests/test_benchmark_file_switching.py tests/test_reading_style_webengine.py tests/test_fast_preview.py tests/test_source_export.py -vv --tb=short -p tests.qt_lifetime_probe
```

結果 **117 passed in 105.36s；exit 0，無 teardown error**。[日誌](evidence-20261022/code-preview-final-webengine.log)、[隔離 guard](evidence-20261022/code-preview-final-webengine-guard.json)。兩個最終 guard 均 native_unchanged=true、persistent_data_unchanged=true。

第一次打包在編譯完成後因來源有變更而被 manifest 檢查拒收，未交付該中間包。最終重新執行 PyInstaller 與 InnoSetup 皆 exit 0；619 個來源檔在建置前後一致，[manifest](evidence-20261022/code-preview-final-build-manifest.json) 標示 published=false。

## 本機候選包

版本仍為 1.36.0，使用獨立輸出目錄，未對外發布，未執行安裝程式。

| 產物 | 大小 | SHA-256 |
|---|---:|---|
| `dist/upgrade-20261023-code-preview/MarkdownViewer/MarkdownViewer.exe` | 9,758,348 bytes | `1951d163b77633f956ca13565acb7d3e314558f377045c415fb08aa511ede0da` |
| `installer_output/upgrade-20261023-code-preview/MarkdownViewer_Setup_v1.36.0.exe` | 166,141,639 bytes | `79248d2ec5a2d084a74e4d92606bd7a62dbe12948fb1904644beb496102e06fe` |

實跑包內 EXE（offscreen／disable-gpu、隔離設定）命令：

```powershell
dist/upgrade-20261023-code-preview/MarkdownViewer/MarkdownViewer.exe --packaged-benchmark --output docs/upgrades/evidence-20261022/code-preview-packaged-child.json --repeats 1 --working-set 2 --size 1000000 --category code_heavy --overall-timeout 180 --label code-preview-final-exe
```

結果：**exit 0、passed=true、tabs_closed=true，4 個樣本**。[封裝 smoke](evidence-20261022/code-preview-packaged-smoke.json)、[guard](evidence-20261022/code-preview-packaged-guard.json)（native_unchanged=true、persistent_data_unchanged=true）。此為封裝功能核對，不是安裝／升級測試；兩個暖樣本不足以取代前面的30樣本效能驗收。

fresh reviewer `deferred_code_review` 最終唯讀驗收 **PASS**：獨立重算619份來源以及兩份產物的SHA-256與大小，全部相符；核對最終兩組測試、封裝smoke與三份guard通過，可交付本輪本機候選版。

實機安裝／升級／解除安裝、中文 IME 與多螢幕 DPI 未驗證；自訂 CSS 若改變 token 字寬／行高，延後套色仍可能改變區塊尺寸，未保證所有自訂樣式零位移。尚不能據此宣告所有升級工作或正式發布驗收完成。

後續可照做的實機驗收：在可還原的Windows測試VM安裝既有版，建立測試筆記、註記與設定；使用本輪安裝包升級並確認原測試資料保留；切換閱讀／原始碼／並排模式，測中文IME連續組字、復原與儲存；在100%／150%／200% DPI及不同螢幕間移動視窗檢查文字、工具列和游標；最後解除安裝並核對測試筆記仍存在。以上尚未實跑，不列為通過。
