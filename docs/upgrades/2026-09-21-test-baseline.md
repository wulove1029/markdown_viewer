# 2026-09-21 測試與封裝基準

程式／測試基準：`4fcdf25`（全量執行期間後續改動僅註解／文件，AST 已比對相同）。Windows 11，本機 Python 3.13.5；GitHub windows-latest 為 Python 3.13.15。兩者皆安裝 `requirements.lock`：PySide6 6.11.2、pytest 9.1.1。

## 指令、數字與日期

| 日期（台北） | 指令／範圍 | 結果 |
|---|---|---|
| 2026-09-21 | 本機 `-m pytest tests/ -q`，預設旗標 | **1812 passed、82 skipped、0 failed，61.16s** |
| 2026-09-21 | 本機 `RUN_WEBENGINE_TESTS=1`，完整 `-m pytest tests/ -q` | **1887 passed、7 skipped、0 failed，436.86s** |
| 2026-09-21 | 本機 `-m pytest tests/ --collect-only -q` | **1894 collected、0 error，1.86s** |
| 2026-09-21 | 本機 `-m pytest --collect-only -q` | **1894 collected、0 error，1.59s** |
| 2026-09-21 | [push CI](https://github.com/wulove1029/markdown_viewer/actions/runs/35529213221)，`py -3 -X utf8 -m pytest tests/ --junitxml=test-results.xml` | **1812 passed、82 skipped、0 failed，92.20s** |
| 2026-09-21 | [手動 CI](https://github.com/wulove1029/markdown_viewer/actions/runs/35529213096)，七個 WebEngine 模組 | **93 passed、0 skipped、0 failed，242.70s** |
| 2026-09-21 | 同一手動 CI 的一般 tests job | **1812 passed、82 skipped、0 failed，91.19s** |

所有 GUI 測試均設 `QT_QPA_PLATFORM=offscreen`；啟用 WebEngine 時另設 `QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu`。
Ruff 全專案、mypy 納管五檔、隔離環境 `pip check` 均通過。

本機為避免改動個人全域套件，使用 TEMP 的乾淨 venv；實際完整命令為：

```powershell
# mdv-venv.py 只用 subprocess.call 將參數交給同目錄的
# mdv-release-venv/Scripts/python.exe，並傳入 -X utf8。
py -3 -X utf8 $env:TEMP\mdv-venv.py -m pytest tests/ -q

$env:RUN_WEBENGINE_TESTS='1'
$env:QTWEBENGINE_CHROMIUM_FLAGS='--disable-gpu'
py -3 -X utf8 $env:TEMP\mdv-venv.py -m pytest tests/ -q
```

安裝相同 lock 的 Python 3.13 環境可直接以 `py -3 -X utf8 -m pytest tests/ -q` 重跑；務必先確認 `py -3` 選到正確版本。CI 已使用 `PY_PYTHON3=3.13` 與版本斷言。

## 跳過與歷史差異

預設 82 skipped 中，75 個由 WebEngine 旗標啟用。其餘 7 個：5 個需要未隨專案提供的真實 Acrobat 樣本；2 個跨磁碟測試在預設 TEMP 與 pytest tmp_path 同磁碟時跳過。
本輪另外在 D: 建立隔離 fixture、目的地在 C: TEMP 執行這兩個案例，**2 passed、7 deselected，0.34s**，保留原件與相對資源拒絕行為均通過。該次自建暫存資料已清理。

舊記錄的 1773 collected、1457 passed＋75 skipped、memory 1188 並無完整一致的命令／環境／commit 證據，差異原因**未確認**，不以目前跳過數反推歷史數字。

## 原生錯誤及修正證據

首輪全量 WebEngine 在背景 parser 的 GC 期間出現 `0x80000003`；main thread 正在下一個表格預覽。修正測試視窗生命週期，強參照保留每個瀏覽器視窗，在 GUI 執行緒清理，產品同步模型不變。
修正後 table 單模組 **31 passed／144.57s**、本機全量 **1887 passed／7 skipped**、GitHub 七模組 **93 passed**。這是本輪觀察到的問題已通過驗收，非所有 Windows／GPU 組合的穩定性保證。

## 封裝

同一鎖版 venv 的 PyInstaller 真實建置 **exit 0，122.866s**；目錄 **627,589,065 bytes**，只含 en-US／zh-CN／zh-TW 三個 WebEngine locale。
封裝 exe 的 `--render-worker` 真實啟動、Markdown／code／table HTML 與來源版逐字一致，shutdown **exit 0**。
語系裁剪自身減少 44,111,855 bytes；乾淨環境另外減少 374,407,282 bytes，不能將全部減量歸給語系。
實體安裝、高 DPI、多螢幕與 SmartScreen 尚未完整人工驗收，補驗步驟見 [仍開放清單](2026-09-21-remaining-work.md)。

TEMP 原始 log：`mdv-final-default.log`、`mdv-full-webengine-lifetime.log`、`mdv-final-ci-green.log`、`mdv-final-ci-webengine.log`、`mdv-e7-locked-build.log`、`mdv-locked-package-smoke.json`。
