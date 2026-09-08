# 搬移實作獨立覆核（2026-09-08）

覆核者未修改 `app/document_relocation.py` 或 `app/file_ops.py`，只新增獨立探針與測試。

## 已完成

- 已讀取兩個產品模組及實作者的 parser／交易測試。
- 探針：`docs/upgrades/evidence-2026-09-08/relocation-independent-probe.py`，結果為同名 JSON。
- 新增獨立驗收：`tests/test_relocation_review.py`。
- 首輪命令：`py -3 -X utf8 -m pytest tests/test_relocation_review.py -q -p no:cacheprovider --basetemp tmp/upgrade-relocation-review-r1` → **2 failed, 3 passed in 0.40s，exit 1**。

已通過的隔離故障／實體 IO 情境：

1. sidecar 發布失敗且原路徑被外部程式重建：不覆寫外部新檔，原始 bytes 保存在錯誤明示的備份資料夾。
2. sidecar 發布失敗且已發布的目的主檔被外部修改：原來源回復，外部目的版本保留，錯誤包含目的路徑。
3. 實際從 E: 測試資料夾移至 C: 的 TemporaryDirectory：主檔與 sidecar 搬移成功，Markdown 相對附件改為對應來源資源的 file URI，原附件不搬走；原始換行保留。不是慢速 USB／網路效能測試。

## 已交實作者修正

1. 全文件共用 inline parser 會將不同段落中未配對的反引號錯配為 code span，因而漏改其間真實 Markdown 連結。探針先以 CommonMark 實際渲染確認中間連結存在。
2. 發布完成後立即取得 signature 失敗時，目標已存在，但交易尚未標記 published；回復來源後留下目標，錯誤未明示殘留路徑。需在發布成功時即追蹤，並對無法安全清除的目的檔提供明確位置。

狀態：上述兩項修正後待重新驗收。
