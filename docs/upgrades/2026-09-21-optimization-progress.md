# 2026-09-21 六批次優化進度

需求來源：2026-09-20 Markdown Viewer 優化開發需求。
基準 commit：7fa7d2c；開始時 git status --short 為空。

依序執行 A → B → C → D → E（E2 先於 E1）→ F。
每項分別測試、更新 CHANGELOG、commit；未通過的驗收明列，不視為完成。
WYSIWYG 區段不重構，維持 QTextDocument 為唯一真值。

## A1（進行中）

加入來源相對 Markdown 連結解析，保留原有 wikilink 行為與 raw_targets 相容介面。
測試：`py -3 -X utf8 -m pytest tests/test_links.py tests/test_graph_model.py tests/test_graph_view.py -q` → 29 passed in 0.52s。
1000 檔（每檔一條 Markdown、一條 wiki）、10 次 build + graph：1000 節點、2000 邊，p50 140.37ms、max 144.87ms（不含磁碟讀取）。
獨立覆核發現角括號圖片及複雜 fence 假邊，已加入回歸案例並修正，等待再驗。
與需求做法差異：wikilink 沿用 mask_markdown_code；Markdown 交由 CommonMark parser 排除程式碼，因既有 masker 無法保留 tilde／不同長度 fence 語意。
本機 DocumentLibraryStore 為空；歷史記載 E:\\Puritygo 不存在，尚未驗證使用者文件庫。

## 待辦

A2–A5、B1–B6、C1–C8、D1–D5、E1–E11、F1–F6。
D6 為仍開放的候選清單，依需求文件保留追蹤。
選配共享標籤弱邊暫不納入。

A1 fresh agent 複驗通過：25 passed in 0.33s，額外 9 種語法情境通過，混合文件庫 4 節點／3 邊。

## A2

重名 label 逐層補父資料夾；tooltip 以最深層包含該檔的文件庫根目錄計算相對路徑。
測試：`py -3 -X utf8 -m pytest tests/test_links.py tests/test_graph_model.py tests/test_graph_view.py -q` → 31 passed in 0.54s。
Fresh agent 獨立驗收：graph model/view 16 passed in 1.31s，額外檢查副檔名碰撞與巢狀文件庫通過。

更正 A1 查找：必須設定正式 QCoreApplication 組織與名稱才會讀到實際 AppData。設定指向 D:\Puritygo，確實存在。
唯讀實測 D:\Puritygo：84 檔、84 節點、29 條 markdown 邊，索引讀取加建圖 1.447s；真實節點標籤重複數 0。
README 範例：air_quality/README、app_flutter/README、docs/README。尚待 UI 與完整回歸驗證。
