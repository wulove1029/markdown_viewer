# 消費者導覽升級獨立覆核（2026-09-08）

覆核主線的 `app/quick_open.py`、`app/file_browser.py`、`app/settings_dialog.py`，未修改這三個產品檔。新增獨立驗收 `tests/test_consumer_navigation_review.py`，測試文件庫與設定均採暫存路徑／monkeypatch。

覆核曾回報主線三個缺口：移除最後來源時 loading 狀態訊號先於狀態更新、掃描失敗沒有發送文件清單變更訊號、深色設定捲動區仍畫出 `#efefef`。主線已修正，最終驗收通過。

實際驗證範圍：

- 全文件快取不受名稱篩選／標籤篩選影響。
- 真實背景掃描完成可更新已開啟快速開啟面板，保留輸入並結束 loading。
- 掃描中移除來源、掃描失敗均可結束面板 loading。
- 同名文件顯示不同路徑，更新候選保留已選擇文件。
- 載入 Windows 中文字型後，設定對話框在 540 px 高度下五個分頁均可捲到底，確定／取消常駐可見；深色 viewport 實際截取像素維持暗色。

```powershell
py -3 -X utf8 -m pytest tests/test_global_search.py tests/test_consumer_search.py tests/test_consumer_navigation_review.py -q -p no:cacheprovider
```

最終實跑 exit code 0：`46 passed in 0.76s`；其中導覽獨立覆核 7 項。這是 Qt offscreen 幾何／事件／像素驗證，未宣稱已執行真人可用性測試。

## Window／移動整合第二輪獨立覆核

新增 `tests/test_consumer_window_review.py`，唯讀覆核產品程式。首輪實跑 `1 failed, 4 passed in 1.00s`：

- **需修正**：目的文件已不存在、但目的路徑仍有另一份待復原草稿時，移動未儲存來源文件會用來源草稿覆寫目的草稿。已回報主線在搬移前檢查目的復原版本，禁止無提示覆寫。測試以獨立 RecoveryStore 與暫存文件實際重現。
- **通過**：移動非當前未儲存文件，保留當前文件物件與文字；「稍後」草稿移動後能於新路徑再次復原；Office 非同步最後按鍵確認後才切換 TXT 並選取指定匹配行；相同查詢點不同來源行會重新要求對應預覽區塊。

主線已新增目的草稿衝突檢查；補上第 6 項驗收，使用真實 `FileBrowserView._relocate_document()` 入口確認 Office 快照與 acknowledgement 完成前不會搬檔，完成後正確更新當前路徑及未儲存連結。

```powershell
py -3 -X utf8 -m pytest tests/test_consumer_window_review.py -q -p no:cacheprovider
```

最終實跑 exit code 0：`6 passed in 1.00s`。目的孤立草稿維持原文、來源文件保持原位；其餘來源／目的草稿與 active／inactive 狀態驗收均通過。獨立覆核本輪未留下未解決的阻擋項目。
