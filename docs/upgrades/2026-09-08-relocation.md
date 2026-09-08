# 文件搬移安全升級（2026-09-08）

進度：已完成純函式 `rebase_markdown_links(text, old_path, new_path)`、原編碼 bytes 包裝、檔案交易與主視窗整合。58項搬移／file_ops／独立覆核測試通過；完整回歸1,457 passed、75 skipped。

範圍：單文件改名／跨資料夾搬移，包括主檔、註記 sidecar、已有備份；資料夾整體改名維持原语意。相對資源重算成同一資源的新路徑，不移走共用附件。

主線整合契約：搬前先 snapshot 所有 live buffer，呼叫純函式預檢；搬後以同函式更新記憶體，保留髒狀態、更新來源簽章與復原草稿。檔案磁碟 API 保持 `{old_path: new_path}`。

已處理目的地既有主檔／孤立sidecar／待復原草稿衝突；Office搬移前等待最新snapshot與ack。保存編碼、換行、未儲存狀態及外部修改衝突；交易發布失敗時嘗試還原，還原受阻則明列保留路徑。

獨立覆核抓到並修正：跨段落反引號誤判、發布後stat失敗漏列目的地、數學公式與frontmatter誤改。重跑證據見 `evidence-2026-09-08/relocation-independent-probe.json`。無相對資源的E→C搬移已實跑；有相對資源的跨磁碟搬移明確拒絕並保留來源。

限制：wiki-links、CSS URL、srcset、base與其他無法可靠定位的語法採保守拒絕；尚無程序崩潰後交易自動回放或manifest。共用附件保留原位置。
