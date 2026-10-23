## AI 協作
本專案使用 AI 協作系統。開工先讀 `AI協作/AI_協作規範.md` 與 `AI協作/AI_協作進度.md`
（收件匣有 @Claude 的先處理）。你是唯一指揮官：實作類任務優先用 ai-collab MCP
的 dispatch_task 派給 Codex、測試／文件類派給 Gemini，自己負責拆解、監看、驗收；
每個任務完成後用 read_result 驗收並在進度紀錄留一節。
門檻：預估只改 1 個檔案且不需跑測試的小事自己做，不必派工。
Tangram 中控台（跨機器進度／交接／記憶／會議）照 `AI協作/AI_協作規範.md` 的「Tangram 中控台協定」；Phase 1 建好前或本機沒有 `.agent-node` 時不適用。
