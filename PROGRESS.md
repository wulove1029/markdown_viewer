# 目前狀態
目前：v1.36.0已正式發布，latest API/更新器實查與fresh發布驗收PASS；commit e597865。
下一步：使用者可再次檢查更新並驗收安裝；D槽main已同步發布提交e597865，本機協作紀錄與測試資料保留。
限制：安裝流程由使用者驗收；500ms、實機IME/DPI及原native AV唯一根因限制仍保留。

# 流水帳
2026-10-23｜[CX] 使用者要求commit並提交，核對本機暫存141檔與已發布e597865完全相同｜git diff --cached e597865 --quiet exit0；遠端main SHA一致；以soft reset同步本機HEAD、保留工作檔與index｜無重複提交，產品暫存差異清空，本機協作/驗收資料未納入發布提交
2026-10-23｜[CX] 正式發布v1.36.0並成為latest｜gh release edit --draft=false --prerelease=false --latest exit0；check_for_update('1.35.0')=has_update true/latest1.36.0；fresh live API與tag/asset SHA核對PASS｜release https://github.com/wulove1029/markdown_viewer/releases/tag/v1.36.0；asset SHA e6ee9519…f5549，來源commit e597865
2026-10-23｜[CX] D槽Git寫入持續緩慢，改C槽獨立clone發布｜複製後619來源raw SHA全符合manifest，141檔版本提交推送main exit0｜C槽發布副本保留；D槽工作檔、協作制度與本機證據未刪改
2026-10-23｜[CX] 使用者指出檢查更新仍顯示1.35.0，要求實際發布｜gh latest API=1.35.0、release view v1.36.0 not found｜接續發布已通過獨立驗收的安裝包，不再停在本機候選交付
2026-10-23｜[CX] fresh候選驗收PASS｜獨立重算619來源與2產物SHA/bytes吻合，讀回2175/119測試與4樣本smoke/guard皆通過；git diff --check exit0｜本輪候選可交付使用者安裝驗收，未宣稱500ms或整體升級全部完成，未對外發布
2026-10-23｜[CX] 新候選建置與封裝開檔smoke通過｜PyInstaller/Inno exit0；EXE隔離4樣本passed/tabs_closed true、guard設定資料不變｜installer SHA e6ee9519…；等待fresh 619來源及產物雜湊核對，安裝由使用者驗收
2026-10-23｜[CX] 真WebEngine整合119項通過｜shared-lines-final-webengine.log：119 passed in 101.45s，exit0｜PyInstaller exit0，Inno壓縮中；下一步封裝smoke與fresh manifest驗收
2026-10-23｜[CX] 最新來源完整回歸通過｜python -m pytest tests/ -vv --tb=short -p tests.qt_lifetime_probe：2175 passed, 150 skipped, 2預期warnings in 116.18s，exit0｜WebEngine opt-in續跑，PyInstaller建置中
2026-10-23｜[CX] 全專案ruff check .與mypy通過；CI/release納入延後高亮真Chromium測試｜All checks passed / no issues found in 10 source files｜開始C槽建置、新installer獨立目錄，保留舊候選；1.36.0程式/installer一致，本機最新tag v1.35.0
2026-10-23｜[CX] 同環境前後各30暖樣本完成，兩guard均exit0且設定/資料不變｜首屏P95 1429.63→973.30ms，完整文字1740.29→1072.99ms，同步48.74→43.35ms｜改善成立但500ms未達；完整測試launcher讀到BOM已改utf-8-sig，續跑測試不重跑效能
2026-10-23｜[CX] 最新共用行表功能與真Chromium整合測試｜C槽python -m pytest tests/test_code_loading.py tests/test_code_loading_webengine.py -q -p tests.qt_lifetime_probe：14 passed in 24.21s；ruff三檔All checks passed｜開始同環境A/B各30暖樣本，隨後完整及opt-in回歸
2026-10-23｜[CX] 使用者接手安裝流程驗收，要求繼續準備更新版｜C槽暫存環境pip exit0；啟動code_loading與真實WebEngine測試｜不再尋找Windows VM；候選安裝檔仍保留
2026-10-23｜[CX] faulthandler定位QtWebEngineWidgets原生模組載入；D槽佇列14、約658KB/s，203MB DLL hash亦未完成｜C槽約425GB可用，D槽約57GB可用；VMware vmrun存在但runningVM=0，inventory僅Ubuntu｜改建C槽同鎖版暫存驗證環境，將同環境A/B避免磁碟差異誤算；已詢問可用Windows測試VM位置
2026-10-23｜[CX] 本次Qt相關測試與pilot停在初始化、CPU低且尚無樣本｜只終止核對命令列的自有pytest/benchmark child，不碰使用者程式｜開始15秒faulthandler匯入診斷，未把未完成pilot算為性能證據
2026-10-23｜[CX] 使用者要求繼續完成；讀取既有進度、制度與專案協作規則｜Get-Command WindowsSandbox/VBoxManage/vmrun/Get-VM無結果；既有候選與證據保留｜以同步JSON共用行表縮減高亮payload，避免引入新瀏覽器壓縮API或變動互動時序
2026-10-23｜[CX] fresh最終交付核對PASS｜獨立重算619來源及EXE/installer SHA與bytes全部相符；核對2173/117測試log、packaged4樣本與三guard皆通過｜本輪本機候選可交付，整體升級與正式發布未宣告完成；詳細報告2026-10-23-code-preview-performance.md
2026-10-23｜[CX] 最終包內EXE以新版探針跑2文件各冷/暖共4樣本｜code-preview-packaged-smoke exit0 passed/tabs_closed true；guard設定/資料不變，exe SHA與manifest相符｜installer未執行；等待fresh最終來源/產物雜湊核對
2026-10-23｜[CX] 最終PyInstaller及InnoSetup皆exit0，建置前後來源雜湊一致｜code-preview-final-build-manifest.json：EXE SHA1951d163…、installer SHA79248d2e…｜啟動封裝EXE隔離code-heavy smoke，未執行安裝程式
2026-10-23｜[CX] 匯出修正後完整重跑完成｜code-preview-final-pytest：2173passed150skipped2warnings119.08s；final-webengine：117passed105.36s無teardownerror；兩guard設定/資料不變｜PyInstaller重建已完成，installer編譯中，準備封裝smoke
2026-10-23｜[CX] 追查HTML snapshot實際走RendererView，新增defer_code=False保留匯出完整span；修正4個opt-in測試的QObject instance patch為class patch｜含新增大型HTML匯出與四案lifetime probe：55passed35.68s｜前次建置因source變動由manifest檢查拒收(exit1)，開始最終重建；保留未驗證實機限制
2026-10-23｜[CX] 全套lifetime probe重跑完成｜2173passed149skipped2warnings120.07s exit0；lazy-code-final-pytest-guard設定/資料不變｜無teardownerror；相關Chromium組、建置仍在進行，產品碼固定
2026-10-23｜[CX] fresh獨立重跑22passed8.26s，報告數值/限制read-back通過；完整初跑2個舊stub缺helper已補，fast preview21passed，Ruff通過｜保留lazy-code-full-pytest.log失敗紀錄，final-pytest重跑中，guard前後不變｜啟動相關真Chromium組及獨立目錄upgrade-20261023-code-preview本機建置，既有候選保留
2026-10-23｜[CX] 30暖切換同探針完成，full P95 1492.70ms、ready1233.83ms、sync40.45ms、gap42.56ms；關閉前process-tree RSS 1.266GB（原4.013GB，單一時點非peak）｜lazy-code-final.json passed且guard不變｜新增大型RendererView真搜尋/既有註記/行內編輯寫回測試，11passed6.83s；啟動全套lifetime probe
2026-10-23｜[CX] 修正review指出的pre首行LF、worker例外範圍與payload ID衝突；搜尋期間暫停節點替換，選取/註記/編輯節點保護｜pytest test_code_loading+webengine：6passed1.77s；PDF末段文字存在｜lazy-code-cache 4暖樣本full P95 1366ms、sync35ms、gap45ms；guard設定與資料不變，展開30暖樣本
2026-10-23｜[CX] CSS visibility確認實際auto但4暖樣本完整P95仍8.93秒；trace PrePaint1886ms、UpdateLayoutTree1428ms、Layout924ms｜隔離guard皆不變；未採用CSS試驗｜開始減少初始活動DOM，保留完整文字並驗證互動安全
2026-10-23｜[CX] 完整頁面約292514元素、288007 span、7.9MB HTML；移除陰影試驗4暖樣本完整P95 7.24秒，尚不足決定採用｜complete-browser-metrics及no-shadow guard皆設定／資料不變｜啟動僅127.0.0.1的自有Chromium trace，定位實際Layout／Script成本，不修改產品
2026-10-23｜[CX] 使用者要求持續直到改善；開始完整載入後Browser Timing與DOM數量分析｜沿用新版readyState complete探針，隔離guard，2文件1輪診斷｜以完整載入9.30秒P95為既有基準，不再沿用舊full指標
2026-10-23｜[CX] 工具修正最終獨立驗收通過；測試先清除partial再獨立驗錯文件｜真Chromium3 passed in2.91s，Ruff通過；complete-probe-early completed/passed/tabs_closed均true｜僅放行驗收工具，產品效能與正式發布未放行
2026-10-23｜[CX] 新探針30暖樣本完成｜complete-probe-early.json：ready P95 1777.14ms、full P95 9303.96ms、sync P95 57.14ms；exit0且guard設定／資料不變｜效能仍未達標；每份文件完整載入才切換，新舊時序不同，非本輪產品回歸
2026-10-23｜[CX] 將complete檢查提前至全文讀取前；中間完整測量停止以避免無效probe成本｜只終止精確命令匹配的自有benchmark，child exit15；guard設定／持久資料不變；新真Chromium測試3passed0.61s｜新版30暖樣本測量進行中，已見完整載入數秒，歷史full_ready不作完整載入證據
2026-10-23｜[CX] Browser Navigation Timing診斷發現舊full探針通過時domComplete仍0；補readyState complete與獨立double-frame token｜RUN_WEBENGINE_TESTS=1 pytest test_benchmark_file_switching：3 passed in0.60s；Ruff通過｜舊full_ready_ms不再作完整載入驗收，啟動修正探針30暖樣本
2026-10-23｜[CX] fresh唯讀驗收診斷證據通過｜確認4/30/30暖樣本與P95，三guard不變及617來源無改；依審查明確標示51/73ms是開檔後時間點非方法耗時｜未採納無效優化，正式發布限制保留
2026-10-23｜[CX] 核對本輪沒有變更候選版來源｜recent-build-manifest 617 sources重新SHA-256比對changed=[]，exit0｜維持既有候選包，不因診斷重建；待fresh文件驗收
2026-10-23｜[CX] renderer分段診斷完成：暖樣本HTML約51ms準備、73ms導航，可讀481ms；初版包裝Qt signal slot導致診斷逾時，移除該包裝後正常｜render-stages-safe.json exit0及guard不變；首版render-stages.json失敗不作產品證據｜沒有修改產品
2026-10-23｜[CX] 探針範圍試驗與畫面外程式碼排版試驗｜heading-probe30暖樣本P95 626.22ms；code-visibility-warm30暖樣本P95 767.73ms，guard不變｜兩方向未證明達標，未納入產品；初版CSS試驗每輪清cache屬無效暖比較，保留但不採用
2026-10-23｜[CX] 接續開檔瓶頸分析，臨時診斷加各renderer階段時間戳｜profile_render_stages.py，2文件2輪，隔離guard｜產品尚未變更，結果不作達標證據
2026-10-23｜[CX] 新候選版獨立驗收完成｜recent-artifact-audit.json passed=true，617來源及2產物hash/bytes一致；新產物表readback通過｜本輪修正與候選包已交付，正式發布仍未放行
2026-10-23｜[CX] 新目錄upgrade-20261023建置完成，原候選版保留｜build_recent_delivery.py exit0，sources_unchanged_during_build=true；EXE smoke passed=true exit0且guard不變｜等待獨立來源／產物hash核對；未安裝或發布
2026-10-23｜[CX] 最新來源完整回歸完成｜pytest tests/ -vv --tb=short -p tests.qt_lifetime_probe：2167 passed、143 skipped、2 warnings in130.89s，exit0｜新候選包建置中；測試與建置並行，耗時不作性能比較
2026-10-23｜[CX] 最近清單修正經獨立審查；完整視窗30暖樣本P95 649.17ms，guard兩類資料不變｜167 passed；補驗INI跨程序讀回（按既有_load處理單筆字串/空值）11 passed in1.44s，Ruff通過｜準備新目錄建置候選版，不覆寫既有證據
2026-10-23｜[CX] 完整視窗30暖樣本ready P95 953.693ms，guard設定／持久資料不變；額外4暖樣本追蹤發現recent.add約41ms｜ascii-full-window.json及open-profile.json，exit0｜合併recent路徑與時間戳為單一QSettings生命週期，執行相關回歸
2026-10-23｜[CX] 接續完整開檔驗證；讀取原benchmark參數與隔離設定｜使用相同2文件、15輪、1MB code_heavy workload｜啟動帶持久資料前後核對的測量，不把計字元件改善直接視為開檔改善
2026-10-23｜[CX] fresh agent確認最終code與文件證據一致｜新增2測試獨立實跑2 passed in0.41s；66,430組語意對照一致，長連字詞輔助記憶體約2KB｜僅放行計字元件修正，整體發布尚未放行
2026-10-23｜[CX] 獨立審查發現初版regex長連字詞配置62.8MB；改固定lookbehind並加入1M字元記憶體回歸測試｜最終editor_status含probe36 passed in2.03s，Ruff通過；7次交替中位數134.1894→34.4568ms｜約3.9倍，取代初版4.9倍數據；產品候選包待重建
2026-10-23｜[CX] 新增ASCII計字快速路徑及連字號／控制字元／Unicode路徑對照測試｜pytest editor_status/editor_data_safety/session_restore含probe：105 passed in29.54s，exit0；Ruff通過；7次交替比較135.2245→27.6475ms｜統計元件約4.9倍，非整體開檔速度；產品已變，舊建置manifest不再代表最新來源
2026-10-23｜[CX] 取消QTWEBENGINE_CHROMIUM_FLAGS後驗證5個相關測試檔，含生命週期probe｜267 passed in38.28s，exit0（session24719）｜無需以disable-gpu掩蓋已知測試還原問題；原生AV唯一根因仍未證實，發布限制保留
2026-10-23｜[CX] 修正後全套probe完成且獨立代理確認斷言與設定隔離保留｜pytest tests/ -vv --tb=short -p tests.qt_lifetime_probe：2162 passed、143 skipped、2 warnings in114.72s，exit0；manifest changed_since_build=[]｜開始無Chromium flags相關組驗證，產品未改
2026-10-23｜[CX] 全套診斷2162 passed、143 skipped但11 teardown errors，保留before log；修正10案context及1案銷毀測試改patch類別｜Ruff All checks passed｜啟動修正後全套；早期未清理即報錯的probe中斷結果不作驗收證據
2026-10-22 21:59｜[CX] 使用者授權持續實作至完成；核對計畫、全域判斷守則與git狀態｜git status：只有先前PROGRESS與docs/plans未追蹤，無產品改動｜根據計畫劃分檔案責任，開始並行實作
2026-10-22 22:06｜[CX] 工作區控制、命令搜尋、可見後端插入及閱讀/計字API整合｜window+shortcuts 163 passed；編輯工作區/glue/範本16 passed｜後續補新功能測試及真實Office驗證，搜尋萬檔完整掃描仍在優化

2026-10-22 22:21｜[CX] 加入草稿/來源摘錄/同步/交易啟動回放與分頁固定｜草稿+衝突12 passed/3 opt-in skip；真WebEngine來源同步6 passed；root先前整合172 passed｜最新共享標籤改非同步，測試需等完成；正持續整合
2026-10-22 22:21｜[CX] 接收PDF session與中斷復原實測｜PDF270 passed/6 WebEngine skip；搬移71 passed/3環境skip；100次PDF P95 20.918ms/UIgap15.002ms/RSS+1.635%｜各組證據保留tmp原始JSON，尚待整體與獨立驗收

2026-10-22 22:34｜[CX] Fresh review抓出搬移清理與中斷、草稿另存關閉、過期定位、大綱後端問題並逐項修正｜獨立搜尋/交易83passed；核心8passed；真來源同步6passed｜開始全套回歸，仍將實體桌面與EXE指標分開驗證

2026-10-22 22:45｜[CX] 核心獨立驗收及來源匯出完成；全套測試中斷，開始釐清共享Qt測試生命週期｜核心12 passed/相關42 passed/Chromium22 passed；來源匯出103 passed/WebEngine7 passed｜尚未宣告整套通過，先修首個失敗再重跑

2026-10-22 22:46｜[CX] 修正既有測試替身隨新共享索引／啟動復原的契約更新；補隔離EXE量測入口與10文件workload｜啟動／匯出／標籤49 passed；獨立閱讀與匯出24 passed｜準備1.36.0本機候選版，建立隔離鎖版環境，不對外發布
2026-10-22 22:55｜[CX] 大HTML背景準備、共用工作池與TXT改善凍結；開始鎖版打包｜WebEngine/HTML/TXT73 passed；window159 passed；Ruff全過；pip check無破損相依｜使用隔離build/dist，不覆蓋既有安裝，實體IME/DPI與長行Qt限制仍待驗或未達標
2026-10-22 23:03｜[CX] 鎖版PyInstaller候選EXE建置成功，補完大MD完整來源模式；全回歸發現PDF捲軸寬導致布局差異並交獨立重現修正｜Build complete；大MD/TXT/workspace20 passed；真閱讀匯出重驗7 passed；全套1132 pass後geometry fail｜不宣告全通過；新增圖表按需及貼上預覽後再重建，正在量測10文件100次EXE切換
2026-10-22 23:14｜[CX] 確認瀏覽器記憶體增長可由純QWebEngine重現，fileURL／clear history／停用BFCache均未解；每10次重建頁面可維持約79MiB｜獨立探針41次；核心reviewer已交實作agent整合｜整合後必須重跑100次EXE與WebChannel／匯出回歸
2026-10-22 23:21｜[CX] 補底部章節／進度與右側工具分頁，獨立review實跑4passed；全套發現Windows venv launcher先退出造成復原測試競態，修測試等實worker後20/20通過｜fresh review與實際強終止探針；非產品鎖放寬｜正在fresh驗附件貼上rollback，避免成功插入後換頁誤刪副本
2026-10-22 23:34｜[CX] 主視窗10文件100次暖切換及全關閉記憶體驗收成功：283.57→285.99MiB（+0.85%），峰值363.77，全關259.64｜tmp/upgrade-rotation-mainwindow.json；其他檢查並行故延遲不作最終KPI｜後續凍結EXE重跑；貼上fresh51passed+JS OK+Chromium6passed，普通回歸續跑
2026-10-22 23:40｜[CX] 全套普通回歸完成2111passed/141skip/2warnings；fresh發現benchmark QSettings實際仍native，撤回隔離聲稱並暫停新測量｜registry備份後僅清6keys中確定診斷生成路徑，未猜測theme/geometry/update原值；沒有原偏好快照可完整還原｜core修共用隔離工具、reader做native digest fresh；正在最終Chromium三組回歸，工具修好後重建與重測
2026-10-22 23:54:14｜[CX] 最終工具隔離fresh驗收與Office合跑穩定：22passed連3次；Ruff全過、mypy10模組、pip check通過｜唯讀guard正常/child失敗/合成資料變動3探針符合預期｜完整普通回歸與最新helper凍結版建置進行；事故限制已補implementation
2026-10-23 00:00:42｜[CX] 凍結前完整回歸2121passed/141skip；EXE隔離預檢exit0且native/data摘要不變｜原計畫逐條fresh audit補抓選取字數／面板寬度／註記失效提示等缺口，決定補實作後重新驗收｜root註記/釘選8passed；載入提示真Chromium16passed；最終產物將再次重建
2026-10-23 00:04:46｜[CX] 原計畫遺漏已補：panelwidth11案、selection99案與fresh21案、orphan/pin8案及Chromium11案、慢提示16案｜獨立逐條review通過；freeze後啟動完整pytest、renderer全組、PyInstaller+Inno與來源SHA一致性檢查｜此輪產物/最終證據集中docs/upgrades/evidence-20261022
2026-10-23 00:10:03｜[CX] 最後完整普通2157passed/142skip/2預期warning、Chromium renderer130passed；Office27與命令22另組證據已保留｜所有exit0；PyInstaller exit0，installer壓縮進行；已更新真深色窄窗釘選畫面及guard｜等壓縮與背景工作全部結束後依序量測，不並行效能試驗
2026-10-23 00:20:13｜[CX] 串行9組最終診斷全exit0且native/data guard不變；EXE100次RSS -0.775%、早晚10筆中位+0.993%、全關閉成功；萬檔首rowP95 12.48–15.52ms、batch max7.464ms、輸入P95約6/5.5ms｜100KB切換P95227ms未達150，1MB首屏662ms未達500，PDF全視窗gap60ms高於50｜不把readiness=true當KPI通過；core獨立定位rotation PID更換約+80ms，正在owned tmp prewarm對照，產品仍freeze
2026-10-23 00:33｜[CX] 凍結最後預熱與搜尋修正，準備完整回歸及重新打包｜搜尋相關49 passed/2 skipped；core預熱49 passed、整合60暖切P95 189.397ms/guard不變｜保留另組註記逾時由fresh定位，尚未達切換150ms目標
2026-10-23 00:35｜[CX] 最終搜尋／預熱獨立驗收與靜態檢查｜fresh45 passed；註記失敗單案1 passed；Ruff/mypy10/pip/Node各exit0｜保留先前註記逾時根因未明，完整renderer將涵蓋；開始完整回歸
2026-10-23 00:37｜[CX] 最終完整普通回歸完成｜locked pytest tests/ -x -q --tb=short：2160 passed,154 skipped,2 warnings in121.85s exit0｜兩警告為保留外來檔測試；開始真Chromium12檔，installer壓縮中
2026-10-23 00:39｜[CX] 最終EXE與Inno安裝包重建完成｜PyInstaller exit0；Inno Successful compile215.641s exit0；source前後一致｜manifest保留617來源hash與兩產物SHA，交fresh獨立重算；尚未實際安裝
2026-10-23 00:42｜[CX] 停止預熱候選版交付：最終Chromium native access violation｜renderer.log stack為test_unready_spare_never_delays_navigation/_evaluate；driver63538 exit1，失敗log與manifest另存prewarm-*｜不放寬測試，core與delivery定位；必要時撤回加速保留原輪換
2026-10-23 00:47｜[CX] 空白預熱單案重現0xC0000005後完整撤回｜renderer SHA與initial manifest完全相符；保留原count10輪換，另加無spare連續10輪換即時JS案例｜產品freeze，root19315重跑普通/Chromium，78918重build；最終只補受影響性能
2026-10-23 00:49｜[CX] 回退後完整普通回歸通過，靜態檢查重做通過｜2160 passed,143 skipped,2 warnings in121.86s exit0；Ruff/mypy10/pip/Node各exit0｜Chromium131案例組執行中；EXE已編譯，installer下一階段
2026-10-23 00:53｜[CX] 回退後真Chromium完整組通過，最終產物重建成功｜131 passed in259.77s exit0；build78918 exit0/Inno195.000s，來源前後hash一致｜包含無spare連續輪換新案；等待fresh hash後開始閒置性能3組
2026-10-23 00:57｜[CX] 最終3組閒置效能驗證完成｜EXE100暖切P95226.375ms/RSS十次median+2.788%；萬檔搜尋含150ms等待P95171.775–173.341ms；IPC305.256ms；3guard不變/all exit0｜記憶體與此搜尋首列達標；MD/PDF/超長行仍有限制，正完成最後文件fresh讀回
2026-10-23 00:59｜[CX] delivery reviewer 完成三份交付文件 fresh 全文／證據核對，修正四處文案並更新驗收結論｜普通2160／renderer131原始log、最後3組JSON與guard、兩manifest僅2檔異動；125個local links皆存在，EXE／installer大小吻合｜可交付本機試用候選，未發布；MD／PDF／極長行與實體驗收未達或未驗，設定事故及撤回native實驗完整保留；無產品修改
2026-10-23 01:09｜[CX] 接續正式發布準備：發現tag直接公開及檔案關聯覆寫問題｜已讀release.yml/installer/updater；WindowsSandbox入口未找到｜先修發布草稿與關聯保留，實機安裝仍未驗證
2026-10-23 01:14｜[CX] 發布補強與獨立驗收｜77相關tests及Ruff通過；fresh updater46通過；workflow固定draft/prerelease｜首次full Qt teardown AV exit3保留，控制環境重跑後段中；舊安裝日誌風險尚待VM
2026-10-23 01:15｜[CX] 發布補強完整回歸結果｜一致環境pytest -x -vv：2162 passed,143 skipped,2 warnings in121.75s exit0｜首次teardown原生錯誤仍未定位；新installer編譯中，尚未正式放行
2026-10-23 01:17｜[CX] 發布補強候選重新打包與獨立核對完成｜build85214 exit0/Inno206.922s；617source與2artifact hash符合；新IPC偵測既有程序而skip2/guard1、設定資料不變｜正式發布仍阻擋；未關閉日常程式，未安裝或發布
2026-10-23 01:20｜[CX] 定位測試清理缺陷：已刪除QTextDocument仍被monkeypatch還原｜單案probe原版1passed/1teardown error，修context後editor_status34passed、Ruff通過｜保留原功能斷言，接著全套probe查相同生命週期問題；未修改產品
