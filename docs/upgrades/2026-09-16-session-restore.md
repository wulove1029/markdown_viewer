# 關閉後恢復文件分頁

日期：2026-09-16。程式修正、驗證及本機測試版打包完成。

## 問題與定位

使用者同時開啟多份 PDF，意外關閉整個軟體後，再啟動時上次文件分頁消失。

已確認程式碼有兩個缺口：

- `restore_startup` 收到文件路徑時跳過 `restore_last_session`，只開指定文件；接著正常關閉會把原本 `open_tabs` 覆蓋成新清單。
- 分頁清單只在 `close_event` 寫入，無正常關閉事件時不會保存近期的開啟／關閉／排序與目前分頁。

另外驗證還原流程中的有效路徑過濾、明確空白工作階段、PDF 頁碼是否能正確保留，避免修好分頁卻切錯文件或回到第一頁。

## 驗收條件

1. 正常關閉後，空白啟動還原分頁順序及目前文件；從檔案總管開新文件也保留舊分頁，新文件取得焦點，已存在的文件不重複新增。
2. 開啟、切換、關閉、排序分頁會自動儲存；正常關閉立即 flush，未儲存編輯的確認流程維持原行為。
3. 還原中不覆蓋尚未完成的舊清單，分離視窗不覆蓋主視窗工作階段。
4. 手動關閉全部分頁後，不會因舊 `last_file` 又冒出已關閉的文件；缺失文件不造成其餘分頁切錯。
5. 多份 PDF 的閱讀頁碼切換及重啟後可恢復，不修改來源檔。
6. 相關測試、隔離的實際 Qt 啟動／重啟 smoke 與 fresh agent 驗收通過。

## 進度

- 修正前的重現測試：7 failed、2 passed，明確重現從文件啟動遺失分頁、沒有關閉事件就未儲存、過濾缺失路徑後活躍文件位移、空清單錯誤回復 last_file，以及 PDF 第一頁未還原。
- 已實作啟動合併舊分頁且僅載入目標文件、250 ms 合併寫入工作階段並 sync、主視窗關閉即時寫入、還原／分離視窗不寫入，以及以路徑識別活躍文件。
- PDF 還原時先取得目標頁碼並抑制載入中捲軸訊號覆蓋紀錄，第一頁也明確跳轉。
- 新增 13 項工作階段整合測試，測試清理另新增 1 項；完整回歸 1,696 passed、77 skipped（65.66 秒，exit 0）。讀回確認回歸執行前後本機分頁設定相同。

## 實際重啟驗證

`py -3 -X utf8 tools/verify_session_restart.py --output docs/upgrades/evidence-2026-09-16-session`：exit 0。

使用真實 MainWindow、QPdfDocument、Qt 事件迴圈，連續五個獨立程序確認：保存、正常重開、文件參數啟動、略過 closeEvent 強制結束、強制結束後重開。分頁順序、目前文件、PDF 頁碼均符合預期，生成的四份來源 PDF SHA-256 維持不變。截圖已目視核對還原後的分頁與 PDF 第 3 頁。

### 測試隔離修正與副作用紀錄

首輪跨程序 smoke 的隔離方式有誤：Windows 上傳入 org/app 的 QSettings 建構仍使用 NativeFormat，不受 setDefaultFormat(IniFormat) 影響，因此誤讀寫本機應用程式設定並留下合成文件的分頁紀錄。先前一次取消關閉測試也留下未清除的視窗計時器。已向使用者揭露；未修改使用者 PDF 文件，未找到可精確還原原分頁清單的先前備份，因此沒有猜測重建。

首輪 smoke 也設定 `update_check_enabled=False`，並在 close event 寫入測試視窗尺寸；沒有原值備份。已另詢問使用者自動檢查更新的偏好，等候超過 60 秒尚無回覆後，先說明並恢復程式預設的 `True`（原值無法確認，不宣稱還原原偏好）。寫入及讀回確認成功，分頁設定鍵維持不變。仍執行中的安裝版正常關閉時，可以從既有視窗狀態重新保存目前分頁及視窗尺寸。

工具已改成在載入任何 app 模組前以明確 INI 檔案的 QSettings 子類隔離，停用 fallback，首程序確認設定檔全空；完成後讀回確認本機分頁紀錄未變。原本安裝版的使用者程序仍在執行，未由測試關閉或操作。兩個 MainWindow 測試 fixture 補上無條件清理 QObject 與計時器，避免取消關閉造成事件逃逸。

## 完整回歸與交付界線

從專案根目錄執行：

```powershell
py -3 -X utf8 docs/upgrades/evidence-2026-09-16-session/run-regression.py
```

此 runner 在 pytest 載入 app 之前，將未指定 INI 的 QSettings 建構導向暫存檔；各既有測試的顯式 INI fixture 維持原本隔離。結果見 `evidence-2026-09-16-session/full-regression.txt`，獨立審查見 `2026-09-16-session-restore-review.md`。

- 自動保存合併間隔為 250 ms；正常關閉立即儲存，強制終止仍可能遺失最後不足此間隔的分頁操作。
- 此次不重建已遺失的舊分頁清單，不改寫原文件；未儲存的編輯仍沿用既有草稿復原機制。
- 前輪本機交付尚未升版或發布；使用者確認可用後，另授權升版、提交與發布，後續見 `2026-09-16-release-1.33.1.md`。
- 使用本機測試版前，先正常關閉目前仍開著的安裝版，使它重新保存目前分頁，再啟動修正版；避免單一執行個體機制將新啟動導回舊版程序。

## 本機測試版

依 DEVELOPMENT.md 使用原有 spec 打包，另放獨立目錄：

```powershell
py -3 -X utf8 -m PyInstaller --noconfirm --distpath dist/session-restore --workpath build/session-restore markdown_viewer.spec
```

打包 exit 0，約 140 秒。入口：`dist/session-restore/MarkdownViewer/MarkdownViewer.exe`；需與同目錄的 `_internal` 一起保留。檢查 PE 檔頭與封裝內的修正模組通過，SHA-256：`7dbc4d6b52f71997209bf20e1b3ec1901a9c1655f19126c4e8ea4528f561a9da`。證據：`evidence-2026-09-16-session/package-verification.json`。

五程序 GUI smoke 驗證的是此版原始碼；沒有啟動打包後的 GUI，以免啟動被路由至使用者仍執行中的安裝版。使用者可正常關閉舊版後，開測試版並開啟數份 PDF、切換頁數、排序分頁，再關閉重開確認；也可關閉後從檔案總管用此測試版開新文件，確認舊分頁保留。
