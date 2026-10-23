# 1.36.0 更新候選：大型程式碼預覽

2026-10-23已正式發布[v1.36.0](https://github.com/wulove1029/markdown_viewer/releases/tag/v1.36.0)，commit e597865。latest API及更新器實查確認1.35.0可偵測新版；fresh獨立核對tag與線上安裝包digest通過。安裝、升級及解除安裝由使用者驗收。

## 本次改動

大型程式碼預覽的高亮資料共用重複行，減少交給瀏覽器解析的資料量；獨特內容保留原本陣列形式，避免索引表反而變大。保留完整文字、換行及 Unicode，延後恢復的是語法高亮標籤。

搜尋、選取、註記及匯出沿用既有完整性保護。HTML 匯出仍使用未延後高亮的獨立來源快照。

## 驗證環境與方式

原 D 槽環境出現原生 DLL 匯入與大型檔案讀取延遲，因此改用 C 槽暫存虛擬環境，依 requirements.lock 安裝相同版本；Python 3.13.5、PySide6 6.11.2。此為開發依賴環境，沒有安裝產品或變更檔案關聯。

前後對照均使用同一環境、同一工作樹及探針，基準只將 `_encode_blocks` 換回原本 JSON 陣列編碼。兩份約 1 MB code_heavy 文件，每版 30 次暖切換，依序執行，不與測試或建置並行。這是 offscreen DOM／雙 frame 代理指標，不是實體螢幕首繪，完整載入表示完整文字，不表示所有畫面外高亮都已恢復。

執行：`python -X utf8 tmp/validate_shared_lines.py`，每項診斷均由 `tools/verify_diagnostic_isolation.py` 比對設定與持久資料。

## 效能結果

| 指標（P95） | 原陣列 | 共用行表 |
|---|---:|---:|
| 同步切換 | 48.74 ms | 43.35 ms |
| 首屏可讀 | 1429.63 ms | 973.30 ms |
| 完整文字載入 | 1740.29 ms | 1072.99 ms |
| 最大 UI 間隔 | 71.77 ms | 43.76 ms |

[基準](evidence-20261022/shared-lines-baseline.json)與[新版](evidence-20261022/shared-lines-candidate.json)各32樣本（2冷、30暖），passed=true、tabs_closed=true；兩份guard均exit0、設定與持久資料不變。這是依序A/B，並非交錯多輪統計實驗。**500ms首屏目標仍未達成。**

基準JSON的label沿用探針預設candidate；兩組以檔名及執行入口區分，基準由 `tmp/shared_lines_baseline.py` 覆寫編碼函式，新版直接執行原探針。未修改原始量測結果。

同一生成器的約1 MB文件轉換後，延後高亮HTML由12,339,927 bytes縮至1,494,171 bytes，見[大小量測](evidence-20261022/shared-lines-size.json)。結果取決於重複行比例，不能推廣為所有文件的固定加速倍數。

相關14項測試通過；fresh reviewer另核對1,004組純文字往返案例通過，未發現必修問題。

## 回歸驗證

- `python -X utf8 -m pytest tests/ -vv --tb=short -p tests.qt_lifetime_probe`：**2175 passed, 150 skipped, 2 warnings in 116.18s；exit 0**。見[完整日誌](evidence-20261022/shared-lines-final-pytest.log)。兩個warning是保留外來暫存資料測試的預期警告。
- `python -m ruff check .`：`All checks passed!`。
- `python -m mypy`：`Success: no issues found in 10 source files`。
- CI及release YAML均可解析，已將真Chromium高亮測試納入後續流程。
- `python -X utf8 tmp/validate_shared_lines.py --tests-only` 的 WebEngine 階段：**119 passed in 101.45s；exit 0**，見[日誌](evidence-20261022/shared-lines-final-webengine.log)。設定 `RUN_WEBENGINE_TESTS=1`、offscreen、disable-gpu，含高亮、搜尋、註記、編輯寫回及PDF/HTML匯出。

跳過的項目不能視為通過；實際中文輸入法、多螢幕、安裝升級流程尚未驗證，原native AV的唯一根因仍未確定。

## 候選安裝包

[下載本機候選安裝檔](../../installer_output/upgrade-20261023-shared-lines/MarkdownViewer_Setup_v1.36.0.exe)，版本1.36.0，166,166,538 bytes。

SHA-256：`e6ee9519338094d27a10486fb2b80f592e73f844fb37fb485ce39e128b5f5549`。

`python -X utf8 tmp/build_shared_lines.py`：PyInstaller及Inno Setup皆exit0；[manifest](evidence-20261022/shared-lines-final-build-manifest.json)保存來源雜湊與兩產物雜湊，建置前後來源一致。EXE位於C槽暫存封裝目錄（完整路徑見manifest，須保留整個資料夾），使用者驗收請使用上方D槽安裝包。舊候選未覆寫。

`tools/verify_diagnostic_isolation.py --output docs/upgrades/evidence-20261022/shared-lines-packaged-guard.json -- python -X utf8 tmp/smoke_shared_package.py`：**exit0、passed=true、tabs_closed=true、4樣本**，見[封裝smoke](evidence-20261022/shared-lines-packaged-smoke.json)及[guard](evidence-20261022/shared-lines-packaged-guard.json)。設定與持久資料不變；只有2暖樣本，僅作功能驗證，不當作正式性能統計。

Fresh reviewer獨立重算619個來源及2個產物的SHA-256與大小，全部吻合，並讀回測試日誌、封裝smoke與guard，候選包範圍驗收PASS。後續依使用者指示完成commit、push及正式發布；尚未執行安裝程式。

## 使用者安裝驗收清單

1. 先保存開啟中的文件，記錄目前版本及 `.md` 預設開啟程式。
2. 覆蓋安裝候選版，確認可以啟動、筆記與設定保留、預設開啟程式符合預期。
3. 開啟繁中與含空白路徑文件，測試中文輸入、儲存後重開、搜尋、註記及匯出。
4. 若有不同縮放比例或雙螢幕，確認字體、工具列與編輯游標顯示正常。
5. 需要驗證解除安裝時，先備份資料；解除安裝後確認筆記保留，檔案關聯沒有誤刪其他程式的設定。

回報通過或失敗的步驟與錯誤內容即可。尚未實測的項目維持「未驗證」。
