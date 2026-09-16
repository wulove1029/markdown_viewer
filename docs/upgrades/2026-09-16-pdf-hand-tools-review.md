# PDF 手形工具與右鍵選單：獨立驗收

日期：2026-09-16。驗收者：fresh agent `/root/verify_pdf_tools`。

**結論：本批閱讀功能通過獨立驗收，未發現阻擋問題。** 本次未修改產品檔案；審查範圍為 `app/pdf_view.py`、`app/window.py`、`app/shortcuts.py` 及相關測試。

## 驗收條件逐項核對

| 條件 | 結果與證據 |
| --- | --- |
| 手形左鍵、Space＋左鍵、中鍵可沿兩軸移動 | 通過：真實 Qt 滑鼠事件下捲軸由 `(200, 200)` 移至 `(260, 240)`；邊界限縮、放開、Esc、切檔、隱藏、失焦及 Space autorepeat 測試通過。另以真實 `QLineEdit.setFocus()` 驗證中斷拖曳，回到 PDF 後不會繼續移動。 |
| 拖曳不誤選／誤標記，文字及 H 仍可用 | 通過：三種平移手勢皆不發出 highlight；文字與螢光筆模式互斥切換正常。另從真實選單切換手形→文字選取後，以文件文字拖選並按 H，收到含 `Independent text select` 的標記 payload。 |
| 無選取文字仍有右鍵功能，工具狀態正確 | 通過：空白處選單、工具 check state、未載入時的 disabled 狀態及鍵盤叫出選單位置皆有測試。真實 `QMenu.exec()` 下以 `QTest.mouseClick()` 選取工具與縮放成功。 |
| 快照可複製圖片，Esc 還原工具 | 通過：真正點選 popup 選項並結束 menu 事件迴圈後，快照仍保持啟用。框選得到 `131 × 111` 圖片，取樣像素 `#1a66cc` 符合測試 PDF 藍色色塊，證明不是只檢查非空剪貼簿；完成／Esc 均回復手形。極小框選及不可見範圍不覆蓋原剪貼簿。 |
| 縮放、頁面註記、搜尋、文件資訊接線 | 通過：右鍵縮放維持游標錨點，實際 menu 發出 `[2.5, 1.0]` zoom signals；主視窗既有 deferred zoom persistence 測試通過。頁面註記使用點擊頁而非最上方頁，主視窗 integration 驗證寫入第 2 頁 sidecar、搜尋顯示且重選輸入內容、status 及 pen mode 同步。metadata 與檔案資訊正常。 |
| 實跑與 fresh agent 驗收 | 通過：相關測試 **333 passed**，獨立 Qt 探測 **exit 0**；詳見下節。 |

## 實跑紀錄

環境：Windows、Python 3.13、PySide6 6.11.0，`QT_QPA_PLATFORM=offscreen`。未操作使用者真實視窗或設定。

```powershell
$env:QT_QPA_PLATFORM='offscreen'
py -3.13 -X utf8 -m pytest -q tests/test_pdf_reading_tools.py tests/test_pdf_highlights.py tests/test_pdf_embedded_annotations.py tests/test_pdf_word_selection.py tests/test_pdf_view.py tests/test_window_integration.py tests/test_shortcuts.py --basetemp=tmp/pdf-hand-review-1 -p no:cacheprovider
```

輸出：`333 passed, 1 warning in 22.41s`，exit code `0`。唯一 warning 為既有 Qt context-menu constructor overload 的 deprecation，不影響測試。完整輸出：`tmp/pdf-hand-review-tests.log`。

額外探測（獨立撰寫，未 mock menu 執行或手形／快照方法）：

```powershell
py -3.13 -X utf8 tmp/pdf_hand_independent_probe.py
```

輸出：exit code `0`，`tmp/pdf-hand-independent-probe.log`：

```json
{
  "snapshot_armed_after_real_menu": true,
  "snapshot_content": {"size": [131, 111], "sample": "#1a66cc"},
  "real_menu_snapshot_escape": true,
  "select_and_h_after_hand": "Independent text select",
  "real_widget_focus_change_cancels_pan": true,
  "real_menu_zoom_signals": [2.5, 1.0],
  "pdf_unchanged_sha256": "2d07e6da4a4ad2acf60dff4322c903b237d274bf836ce7825361782b45892723"
}
```

首次使用 Python 3.14 並指定 `.codex_verify/pdf_review_pytest` 時，該既有目錄權限使 pytest mkdir 發生 `WinError 5`，得到 `59 passed, 274 errors`。這些為 setup 失敗，未算通過；改用可存取的 `tmp` 路徑及 Python 3.13 後，所有 333 個測試均通過。沒有修改目錄 ACL 或略過失敗測試。

`git diff --check`：exit code `0`。

## 相容性與限制

- 既有內嵌註解套件測試通過；手形輕點仍開啟註解，拖曳經過註解不會誤開卡片。
- 快照擷取當下可見 viewport 的像素，包含畫面上的標註；目前不是輸出原始 PDF 向量或超出可見範圍的區域。
- PDF 原文未被修改；一般閱讀標註繼續走既有 sidecar 儲存途徑。
- 本批未加入 PDF 本文編輯、插入圖片、簽名及旋轉視圖，不能宣稱具備 Adobe 全部右鍵功能。
- offscreen 驗證涵蓋 Qt 事件與真實 popup 關閉流程；未在使用者目前執行的已封裝版本、實體滑鼠或多螢幕 DPI 組合上測試。發版／更新啟動後的人工確認：開大圖 PDF→放大→右鍵手形→兩軸拖曳；再測 Space／中鍵、快照貼至其他程式、切回文字選取及 H 標記。

驗收時產品檔案 SHA256：

```text
app/pdf_view.py  C0203CDEBBFBD9A1DA1A4C606CC837FA7A0848ED7E28A65D1EC8E584A3905CFC
app/window.py    103999ABBA0127F7ACAFD77727B436FD2A8B1FCA72B6E981BBC8C7067D17E482
app/shortcuts.py 901B8DC3A6E3D63797228B2960473E017C7C40B4CE4D566A7C842DF088824DF0
```
