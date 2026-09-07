# 模式選單可讀性修正

使用者回報：淺色介面的模式下拉選單呈現黑底、深色字，無法辨識選項。

原因：topToolbar 對所有子 QWidget 設透明背景；QComboBox 私有 popup 與 viewport 繼承該規則，透明視窗底色不受主視窗的一般 QComboBox 樣式保障。

修正：在每次套用主題時，為模式選單的 popup、view、viewport 設獨立 palette 與不透明背景。一般文字、選取文字與選取背景同步亮暗主題。不變更模式切換邏輯。

驗證紀錄：
- test_combo_popup_theme 實際展開選單，檢查亮→暗→亮的 palette 及 viewport 背景像素。
- 連同編輯模式安全回歸，3 passed，exit 0。
- MainWindow 實際展開並截圖，亮暗兩次 helper 都 exit 0；已目視確認四個選項清楚可讀。
- 圖片：docs/benchmarks/popup-fix/mode-popup-light.png、mode-popup-dark.png。
- 相關主視窗／設定測試：160 passed，17.02 秒，exit 0。
- 獨立驗收通過：另驗亮→暗→亮、鍵盤選取、Escape 關閉及父視窗樣式隔離，無阻擋問題。
- PyInstaller 記錄確認 COLLECT 與 Build complete 成功，修正版 EXE 存在（11,473,099 bytes）。未啟動封裝 UI，以避免單一實例轉交到使用者目前的程式；不將原始碼 offscreen 驗證誤列為封裝 UI 驗證。

修正版獨立輸出到 dist/reading-upgrade-popup-fix/MarkdownViewer，避免覆寫正在使用的上一批試用版。不自動關閉使用者程式。
