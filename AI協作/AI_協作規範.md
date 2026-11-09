# AI 協作規範（Claude ↔ Codex ↔ Gemini 共用規則，2026-11-09 版：派工器 + Watchdog）

> 這是**跨專案共用**的規則檔，與各專案自己的討論版 `AI_協作進度.md` 分開。
> 改這份＝改所有專案的規則；改討論版＝只動那個專案的內容。
> 套到新專案時，把本檔連同 `AI協作` 資料夾一起複製即可；要統一更新規則，
> 只覆蓋各專案的這一份 `AI_協作規範.md`，不會碰到各專案的進度紀錄。
> 檢視器（http://127.0.0.1:8765）左側「規範」分頁即讀本檔。

---

## ⚠️ 角色與權限（所有參與者務必遵守）

**專案主導＝Claude Code。** 使用者的需求一律先進 Claude；由 Claude 統籌
**指揮調度**另外兩個 AI——拆解任務、決定每件事派給 Codex 還是 Gemini、
選定模型／推理檔位、收攏各方產出、安排獨立驗收（驗證不自驗）、對使用者回報。
Codex 與 Gemini 是**受 Claude 調度的執行方**：接派工 → 做事 → 留痕 → 交棒回
Claude，不自行主導整體方向、不自行決定要不要動下一棒。

| 參與者 | 角色 | 權限 |
|---|---|---|
| **Claude Code** | 主導／指揮調度 | 讀 + 寫程式 + 執行 + **派工 Codex/Gemini + 驗收** |
| **Codex (GPT)** | 執行方（受 Claude 調度） | 讀 + 寫程式 + 執行 |
| **Gemini** | 執行方（受 Claude 調度） | 讀 + 寫程式 + 執行（2026-07-06 起與 Claude/Codex 同權） |

### Claude 的指揮調度職責

1. **接需求**：使用者把需求講給 Claude；Claude 先掃相關檔案、確認問題屬實
   （帶 `檔名:行號`），再決定拆法。
2. **派工**：一次派一件事，選對執行方與檔位——分析／提案類與難題傾向
   Gemini 深檔或 Codex 高檔，機械性實作走預設檔。**一律透過派工器**
   `dispatch.py run`（中央工具目錄下，路徑記在 `~/.ai_collab_tool_dir`；或 ai-collab 工具
   `dispatch_task`）派出，並依各專案「⚙ 協作設定」帶 `--model`／`--effort`／
   `--service-tier`。派工器會自動：套用標準 prompt 範本（先讀規範與速覽、只做一件事、
   不做對外動作、完成後寫進度紀錄）、建立 `AI協作/tasks/<task_id>/`、更新
   `status.json`、在進度紀錄寫一節〔派工〕。**不要再手動執行 `codex exec`／`agy -p`
   （只在派工器不可用時當備援，見「🎯 指揮口令」），也不要手寫 `status.json`。**
   同一專案**一次只能有一個進行中的派工**（`tasks/.lock`），第二個會被拒絕。
3. **監看**：派出後看專案板頂部的「🐕 真實狀態」面板（Watchdog 由 transcript 推論，
   與 `status.json` 自報橫幅並列）或 `dispatch.py check <id>` / `list`。任務卡住、
   離線、失敗時，巡檢器會在收件匣寫一則 `@Claude` 提醒（見下方「🚨 異常處理」）；
   重派或取消由 Claude 判斷，**不會自動重派**。
4. **收攏＋驗收**：任務結束後派工器自動回收，結果在 `tasks/<id>/result.md`
   （`dispatch.py result <id>` 或 ai-collab 工具 `read_result`），進度紀錄自動多一節〔完成〕或
   〔失敗〕。Claude 核對「怎樣算做好」；交付級產出**另派一個獨立方或 fresh subagent
   驗收**（實作者不驗自己的活）。
5. **回報**：對使用者只回「結論＋檔案:行號＋風險＋需帶去其他機器的清單」，
   不貼原始碼／log 全文。
6. **接棒節奏**：依各專案「⚙ 協作設定」的「自動接棒」開關——❌ 每棒停下等使用者
   確認；✅ 可自動連跑整條鏈，只在最終回報。對外動作永遠需使用者確認。

> **只有 Claude 派工**。Codex／Gemini 不得呼叫派工器或 ai-collab 派工工具、不得互相派工；
> 被派出的 AI **只改工作樹**，不 commit、不 push。

> 使用者仍可越過 Claude、直接照下方「🎯 指揮口令」貼給 Codex/Gemini；但**預設由
> Claude 主導**，其他兩方被點名時以 Claude 的派工為準。使用者本人不受此分工限制。

### 三個 AI 一體適用的通用規則

1. 改動必須留痕：完成後在該專案 `AI_協作進度.md` 的「## 進度紀錄」寫一節，
   引用帶 `檔名:行號`，附驗證證據。派工器自動寫的〔派工〕〔完成〕〔失敗〕節
   **不能取代**執行方自己的那一節。
2. 不得聲稱未驗證的改動已完成（「改完了、應該可以」不算完成）。驗收由實作者
   以外的一方執行。
3. 對外發布類動作（git push、對外服務、刪除資料）永遠需要使用者確認。被派出的
   AI 只改工作樹，不 commit、不 push。
4. `status.json` 與 `AI協作/tasks/` 由派工器維護，**任何 AI 都不要手動編輯**。
5. **只做局部編輯**：改 `AI_協作進度.md` 只能插入或修改指定行，禁止整檔重寫、禁止刪除或還原
   他人的紀錄與收件匣勾選。派工器會在任務結束時比對派工前快照（`tasks/<id>/doc_before.md`），
   發現既有紀錄消失一律判〔失敗〕並通知 Claude。

---

## ⚙ 協作設定的規則（表格在各專案的 `AI_協作進度.md`）

> 每個專案的參與開關、模型檔位、自動接棒開關記在該專案討論版的「⚙ 協作設定」
> 表（檢視器設定面板讀寫）。這裡只放**跨專案共用的規則文字**。

- **參與=❌ 的 AI 不派工、不點名、不加收件匣項**。
- 模型/推理是派工的**強制檔位**（Codex 對應 `-m` 與 `-c model_reasoning_effort=`；
  Gemini 對應 agy `--model`）——派工一律照該專案設定表帶 flag，**不得自行升降檔**。
- **升降檔規則**：認為任務需要更強/更弱檔位時，派工者（Claude）必須**先向使用者
  提出並取得同意**才可改檔；經同意的升降檔仍須在該節紀錄的作者行註記實際使用模型
  （例：`作者：Gemini（模型 Gemini 4 Argon (High)）`）。未經使用者同意的升降檔視為違規。
- **速度**＝Codex 的 `service_tier`：`default`=標準；`priority`=快速（1.5x、吃更多
  額度，急件才開），派工帶 `-c service_tier="priority"`。Gemini 無此旋鈕
  （速度由模型檔位決定，Flash 快、Pro 慢而深）。
- **自動接棒**：❌＝確認模式——每一棒完成後（提案/實作/驗收），派工者必須停下來
  回報並**等使用者確認**才派下一棒；✅＝自動模式——可依「→ 下一棒」自動連跑整條鏈
  （提案→實作→驗收），只在最終結果回報。不論開關狀態，**對外發布類動作
  （git push、對外服務、刪除資料）永遠需要使用者確認**。

---

## 🟢 進行中狀態（status.json，討論版頂部橫幅的資料源；**機器維護，勿手改**）

> 檢視器頂部「進行中的 AI 任務」橫幅每 3 秒輪詢此檔；檔案不存在或 `tasks`
> 為空就顯示「⚪ 目前沒有進行中的 AI 任務」。這是本地執行期檔案（已被
> `.gitignore` 排除），不隨專案同步。

- **位置**：與該專案 `AI_協作進度.md` **同一層資料夾**的 `status.json`。
- **誰寫**：**派工器**（`ai_collab_dispatch.py`）。每次 `run`／回收／取消都會從
  `tasks/<id>/task.json` 重新產生整個檔案；需要手動重建時跑
  `dispatch.py status-json --cwd <專案>`。**AI 不要再手寫或手清這個檔**——手寫的項目
  會在下一次回收時被蓋掉，而且不會有對應的 task 記錄，板上會標成「宣告中但無活動」。
- **真實狀態並列**：`status.json` 是「自報」狀態；板上旁邊的「🐕 真實狀態」是 Watchdog
  從 transcript 推論的狀態，兩者不一致時以真實狀態為準去查（見「🚨 異常處理」）。
- **格式**（僅供讀取參考；頂層 `{"tasks":[...]}`，`tasks` 為 list）：

```json
{"tasks": [
  {"ai": "Codex", "task": "實作 Array Level Control characteristic",
   "started": "2026-07-07 01:00", "model": "gpt-5.6-sol / high"}
]}
```

- 欄位：`ai`＝執行方名稱；`task`＝一句話任務；`started`＝24 小時制當地時間；
  `model`＝實際使用的模型／檔位（派工器可能再附 task id 等欄位）。
- 同一專案一次只有一個派工任務（`tasks/.lock`）；接棒＝前一個任務回收後再 `run` 下一個。
- 任務檔案：`AI協作/tasks/<task_id>/{task.json, prompt.md, stdout.log, stderr.log, result.md}`，
  查問題先看 `stderr.log`，結果看 `result.md`。

---

## 🎯 給使用者的指揮口令（複製貼上即可）

### 首選：派工器 `dispatch.py run`（或板上「📋 派工 → 🚀 直接派工」）

派工器會自動把「先讀規範與速覽／一次一件事／不做對外動作／完成後寫進度紀錄」
包進 prompt，所以 `--task` 只要寫**目標＋動機**；驗收條件、相關檔案、禁區用專屬 flag：

```powershell
# 叫 Gemini 分析（提案）
py -3 -X utf8 "E:\AI協作\dispatch.py" run --ai gemini --cwd "＜專案路徑＞" `
  --title "分析 X" --task "分析＜問題＞，指明建議由 Claude 還是 Codex 操刀、改哪個檔案哪個函式" `
  --files "src\a.py,src\b.py" --model "Gemini 4 Argon (High)"

# 叫 Codex 實作
py -3 -X utf8 "E:\AI協作\dispatch.py" run --ai codex --cwd "＜專案路徑＞" `
  --title "實作 Y" --task-file "＜寫好的任務說明.md＞" `
  --acceptance "pytest 全綠;不動公開 API" --do-not-touch "config.json" --effort high

# 追蹤／收尾
py -3 -X utf8 "E:\AI協作\dispatch.py" list   --cwd "＜專案路徑＞"        # --all 含已結束
py -3 -X utf8 "E:\AI協作\dispatch.py" check  ＜task_id＞ --cwd "＜專案路徑＞"
py -3 -X utf8 "E:\AI協作\dispatch.py" result ＜task_id＞ --cwd "＜專案路徑＞"
py -3 -X utf8 "E:\AI協作\dispatch.py" cancel ＜task_id＞ --cwd "＜專案路徑＞"
```

- `--dry-run` 只印將執行的指令與 prompt，不啟動；`--json` 給程式讀；
  `--background` 放背景與約束；`--service-tier priority` 只給 Codex 急件；
  `--raw-prompt` 把 `--task-file` 原樣當 prompt（重派既有 `tasks/<id>/prompt.md` 時用）。
- `E:\AI協作` 換成你這台機器的中央工具目錄（`~/.ai_collab_tool_dir` 記的那個）。
- **工具形式**（Claude Code／Codex 已註冊 `ai-collab` 工具伺服器時）：`dispatch_task(ai, cwd, task,
  title?, model?, effort?, files?, acceptance?, do_not_touch?, background?, service_tier?,
  dry_run?, raw_prompt?)`、`check_task(task_id, cwd)`、`list_tasks(cwd, include_done?)`、
  `read_result(task_id, cwd)`、`cancel_task(task_id, cwd)`。註冊方式見 `使用說明.md`。

### 備援：原生 CLI（派工器不可用時才用；要自己補 status.json 以外的所有留痕）

**叫 Gemini 分析**（Gemini 走 Antigravity，headless 用
`agy --dangerously-skip-permissions -p "口令"` 直寫討論版。
此旗標是現行本機派工暫留，之後派工器加入安全設定時移除。
舊 gemini CLI 已被 Google 停用勿再試）：
> 請讀 `＜專案路徑＞\AI協作\AI_協作規範.md`（角色與權限）與
> `＜專案路徑＞\AI協作\AI_協作進度.md`（本專案討論版）。任務：分析
> ＜問題＋相關檔案路徑＞。把結論寫成一節進度紀錄（作者 Gemini、狀態〔提案〕，
> 格式照規範的「紀錄格式」），寫進 `AI_協作進度.md`「## 進度紀錄」最上方，
> 並把收件匣對應項改 [x]。指明建議由 Claude 還是 Codex 操刀、改哪個檔案哪個函式。

**叫 Codex 實作**（貼給 Codex，或由 Claude 用 `codex exec --sandbox workspace-write` 派）：
> 先讀 `＜專案路徑＞\AI協作\AI_協作規範.md` 與 `AI_協作進度.md` 的「專案速覽」與
> 「收件匣 @Codex」。任務：＜一件事＋目標檔案＞。完成後實跑驗證，把改動摘要＋
> 驗證證據寫成一節進度紀錄（作者 Codex、狀態〔已實作＋已驗證〕或〔已實作＋待驗證〕）。

**叫 Claude 實作/驗收/派工**：直接講需求即可（Claude 是主導，會按本規範派工）；
要驗收就說「照 playbook 驗收」。

**通用原則**：一次派一件事；給檔案路徑不要貼全文；說清楚「動機」與「怎樣算做好」。
原生 CLI 派出的任務不會進 `tasks/`，板上「真實狀態」會標「有活動但未登記」，屬正常。

### 🚨 異常處理（Watchdog 提醒與重派）

- 板內巡檢器（`ai_collab_orchestrator.py`，每 3 秒一輪；手動 `--once` 可單跑）會回收
  已結束的任務，並對 **STUCK**（啟動逾 60 秒仍無 transcript 活動，或 Watchdog 判定
  疑似卡住）／**OFFLINE**／**DEAD**（行程已結束但回收不到結果）／**FAILED**（exit code
  非 0 或無可用結果）的任務，在該專案收件匣寫**一則**提醒（每個任務最多一次）：
  ```
  - [ ] @Claude：任務 <task_id>（<AI>）<原因>，請確認後重派：py -3 -X utf8 "...\dispatch.py" run --ai ... --cwd ... --task-file "...\tasks\<id>\prompt.md" --raw-prompt ...（Watchdog/MM-DD HH:MM）
  ```
- **不會自動重派**。Claude 看到提醒後：先看 `tasks/<id>/stderr.log` 與 `result.md`
  判斷原因（prompt 問題？環境問題？真的只是慢？），決定直接貼那行重派、改 prompt
  再派、或 `cancel` 後改派另一方；處理完把該收件匣項改 `[x]` 並在進度紀錄留一節。
- 「真實狀態」面板三種標籤的意思：**宣告中但無活動**＝`status.json` 有項但 Watchdog
  看不到對應 AI 在動（多半已死或手寫的殘項）；**有活動但未登記**＝有 AI 在這個專案
  工作但不是派工器派的（原生 CLI 或使用者自己開的 session）；**已結束但未清除**＝
  任務記錄已 DONE/FAILED/CANCELLED 但 `status.json` 還列著，跑 `reap` 或
  `status-json` 重建即可。
- 超過 30 分鐘的任務橫幅會轉黃（見文末「⏱️ 任務超時警報」），那是「請看一眼」，
  不是「一定壞了」——分析類長任務常超過。

### 🎚 模型選擇（派工時依任務難度選檔位）

| 任務類型 | Codex（`dispatch.py run --ai codex`） | Gemini（`dispatch.py run --ai gemini`） |
|---|---|---|
| 輕任務：同步、抄寫、格式、小修 | `--effort medium` | 預設 `Gemini 3.8 Flash (Medium)` |
| 一般實作、除錯 | **預設即可**（gpt-5.6-sol / high） | `--model "Gemini 3.8 Flash (High)"` |
| 深度分析、架構、難題 | `--effort xhigh`（gpt-5.6 可再上 `max`/`ultra`） | `--model "Gemini 4 Argon (High)"` |

- 派工器的 `--model`／`--effort`／`--service-tier` 會轉成各 CLI 的原生 flag（Codex：
  `-m`、`-c model_reasoning_effort=`、`-c service_tier=`；Gemini：agy `--model`）。
- Codex 全域預設在 `~/.codex/config.toml`；effort 可選 `low|medium|high|xhigh|max|ultra`（max/ultra 僅 gpt-5.6 系列支援；可用模型：gpt-5.6-sol / terra / luna、gpt-5.5、gpt-5.4、gpt-5.4-mini、gpt-5.3-codex-spark）。
- agy 可用模型看 `agy models`。
- 原則：**分析/提案類寧可高一檔**（錯的提案會浪費後面兩棒）；
  機械性任務降檔省時間與額度。

---

## ✍️ 紀錄格式（AI 交流風格約定）

每節格式（寫在各專案 `AI_協作進度.md` 的「## 進度紀錄」最上方）：

```
### YYYY-MM-DD HH:MM — 標題〔狀態標籤〕
**作者**：Claude / Codex / Gemini
**類型**：分析｜提案｜實作｜驗證｜反駁｜同意｜同步
（內文：根因/方案/改動，引用一律帶 檔名:行號）
**→ 下一棒**：@誰 做什麼（沒有就寫「無，待使用者裁決」）
```

- 狀態標籤：〔提案〕〔已實作＋待驗證〕〔已實作＋已驗證〕〔已駁回〕〔阻塞〕〔已同步〕
- 時間用 24 小時制當地時間；同一天多節依時間排序（新在上），
  方便還原「誰先說、誰回應誰」的對話順序。
- **鼓勵交鋒**：不同意前一節結論就開一節「類型：反駁」，列證據；同意就「同意」＋補充。
  反駁對事不對人，有證據就推翻，被推翻不需辯護。
- **接棒明確**：每節結尾的「→ 下一棒」讓下一個被叫起的 AI 不用猜要做什麼。
- 新紀錄在上、舊在下；過時共識由後續紀錄明確標記「推翻 YYYY-MM-DD 某節」。

---

## ⚔️ A/B 雙案對決與交叉審查規範

當面臨重大架構重構、技術選型或高風險難題時，應採取 **A/B 雙案對決與交叉審查**：
1. **分別派工**：Claude 派工 Gemini (Variant A / Pro High 檔) 與 Codex (Variant B / xhigh 檔) 分別提出獨立設計提案〔提案〕。
2. **產出對照矩陣**：主導者 (Claude) 收攏兩方產出，寫入一節「類型：分析＋對比」，包含：
   - 方案 A (Gemini) vs 方案 B (Codex) 的核心差異。
   - 複雜度、維護成本與潛在 Side-effects。
   - 風險等級與 Trade-off 推薦。
3. **裁決**：由使用者或主導者選擇最終採納之方案。

---

## 📦 進度紀錄歸檔與歷史維護

- 主討論版 `AI_協作進度.md` 僅保持最新的 **10 節進度紀錄**，以確保 Token 上下文與讀寫效能。
- 超過 10 節的舊紀錄，點擊檢視器標頭的「📦 歸檔」按鈕（或 `POST /api/archive-logs`），將自動移至 `AI_協作進度_archive.md` 保存。

---

## ⏱️ 任務超時警報

- 派工器寫入 `status.json` 後，檢視器頂部橫幅每 3 秒自動計算已執行分鐘數。
- 當單一任務執行時間超過 **30 分鐘**，橫幅自動切換為黃色警示背景 (`⚠️ 提醒：有任務已執行逾 30 分鐘`)，提醒使用者檢查 Console 或 Terminal 是否卡死或在等待手動輸入。
