# 2026-11-08 渲染不受信任 Markdown 的安全強化（工作包 S2）

Viewer 之後會開啟外部工作區與網路下載的 `.md`。安全審計證實：
WYSIWYG 模式下惡意 `.md` 可零點擊經 `<iframe srcdoc>` 呼叫 Python 端 QWebChannel
（`saveWithContent`）並讀取本機檔案；預覽模式下 `![x](//host/share/x.png)` 會零點擊
發起 SMB 連線（可能洩漏 NTLM hash）。本文件記錄修補內容，**特別是升級 vendored
bundle 時必須重做的修改**。

## 1. 升級 Vditor bundle 時必須重做（重要）

`assets/vditor/dist/index.min.js`（Office Viewer 4.2.0 的 Vditor 分支版）被直接修改了
兩處。換新版 bundle 後請重新套用，`tests/test_security_untrusted_markdown.py` 的
`test_bundles_never_enable_loose_mermaid_or_trusted_katex` /
`test_vditor_bundle_keeps_the_patched_values` 會在漏做時失敗。

| 位置（修改後的 9c5f961 版 bundle） | 原字串 | 改成 | 原因 |
|---|---|---|---|
| 第 121 行，字元位移約 2101188（Mermaid 預設設定物件 `Awe`） | ``securityLevel:`loose` `` | ``securityLevel:`strict` `` | `loose` 讓 `click A call eval(...)`、`click B href "javascript:..."` 可在點擊時執行；glue 傳的 `render.mermaid:false` 對這個分支版沒有作用。`securityLevel` 在 Mermaid 的 secure 清單內，`%%{init}%%` 無法再改回。 |
| 第 62 行，字元位移約 1741560（KaTeX `renderToString` 呼叫） | `macros:t.math.macros,trust:!0})` | `macros:t.math.macros,trust:!1})` | `trust:true` 讓 `$\href{javascript:…}{x}$` 點擊即執行；預覽端本來就是 KaTeX 預設 `trust:false`，兩邊行為一致。 |

套用方法（以 bytes 取代，避免改到換行或編碼）：

```python
data = path.read_bytes()
for old, new in [(b"securityLevel:`loose`", b"securityLevel:`strict`"),
                 (b"macros:t.math.macros,trust:!0})", b"macros:t.math.macros,trust:!1})")]:
    assert data.count(old) == 1
    data = data.replace(old, new)
path.write_bytes(data)
```

若新版 bundle 的寫法不同（例如 `securityLevel:"loose"`、`trust:true`），以
`securityLevel` / `trust:` 搜尋，原則是：Mermaid 一律 `strict`，KaTeX 不得 `trust`
為真（或改成只允許 http/https 的 trust 函式）。

另外 `vditor_glue.js` 依賴 Lute 實例上的方法名稱（`Md2VditorDOM`、`SpinVditorDOM`、
`HTML2VditorDOM`、`VditorDOM2HTML` …，見 `LUTE_HTML_METHODS`），以及 Vditor 以
`$global.Lute = …` 設定全域、用 `Lute.New()` 建立實例。升級 Lute 後請確認
`window.__wysiwygGlue._state.vditor.vditor.lute.__markdownViewerSanitized === true`
（WebEngine 測試會檢查）。

## 2. 修改一覽

### WYSIWYG（`assets/vditor_host.html`、`assets/vditor_glue.js`、`app/wysiwyg_view.py`）

- **CSP**：host 頁加 `<meta http-equiv="Content-Security-Policy">`，由
  `wysiwyg_view._host_html()` 把 `__WYSIWYG_CSP__` 換成 `app/web_security.py` 的
  `WYSIWYG_CSP_TEMPLATE`：
  `script-src 'nonce-<每次載入隨機>' 'strict-dynamic'; object-src 'none'; frame-src 'none'; child-src 'none'; base-uri 'none'; form-action 'none'; connect-src data:`。
  - host 頁的 4 個 `<script>`（inline qwebchannel.js、`index.min.js`、`vditor_glue.js`、
    boot script）由 `add_script_nonce()` 蓋上同一個 nonce；`'strict-dynamic'` 讓
    Vditor 自己用 `createElement('script')` 載入的 Lute/i18n/Mermaid/KaTeX 正常執行。
  - **不用 `'self'`**（第二輪驗收）：在 file:// 頁面上 `'self'` 等於任何 `file:` URL，
    `<iframe srcdoc><script src=file:///…>` 會執行（已重現）。CSP 的 host-source
    也無法寫出「沒有 host 的完整 file URL」，所以無法改成逐一列出 bundle 路徑；
    nonce 是可行的精確寫法。文件裡的 inline event handler、`javascript:` URL、
    沒有 nonce 的 `<script>` 都不會執行。Vditor、Lute、Mermaid、KaTeX 皆不需要
    `'unsafe-eval'`（CodeMirror 唯一的 `Function()` 呼叫在 try/catch 內）。
  - `connect-src data:` 而非 `'none'`：Vditor 的「Mermaid 圖複製為 PNG」會
    `fetch()` 自己產生的 data: URI；`data:` 不能連網也不能讀檔。
  - 改 host 頁的 script 不用手動維護 hash；新增 `<script>` 會自動帶 nonce。
- **頂層導航鎖**（第二輪驗收高-2）：WYSIWYG 的 page 是 `_WysiwygPage`，
  `acceptNavigationRequest` 只放行 `WysiwygView._load_html()` 事先宣告的那一次
  host 頁 `setHtml`（main frame、`NavigationTypeTyped`、`data:`），其餘導航（Mermaid
  `click href`、meta refresh、`location`、表單、子 frame）一律拒絕。原本點連結就不會
  導航，所以沒有改變既有行為。glue 另外對內容區 `svg a` 的點擊 `preventDefault()`。
  - 縱深防禦：`page.urlChanged` 一旦提交非 host 的 URL，`_foreign_page=True`、
    `_ready=False`，bridge 所有 slot 忽略呼叫（`initialConfig` 回 `{}`），Python 對
    頁面的 `runJavaScript` 一律經 `_run_js()` 擋掉，快照改由 Python 端的副本回答；
    接著以 Python 端的內容重新載入 host 頁（URL 加 `?restore=N`，確保會觸發
    urlChanged 後才恢復信任）。
- **Glue 清洗**：`vditor_glue.js` 攔截 `window.Lute` 的設定，包裝每個
  `Lute.New()` 實例回傳 HTML 的方法，在 Vditor 指派 `innerHTML` 前用 inert
  `<template>` 清洗：移除 `iframe/frame/frameset/embed/object/applet/form/meta/base/script/link/portal/noscript`、
  所有 `on*` 屬性、`srcdoc`，以及 `javascript:`/`vbscript:`/`livescript:` 與非
  `data:image/` 的 `data:` URL（`href/src/action/formaction/xlink:href/data/…`、SVG
  動畫的 `to/from/values/by`）。另有 MutationObserver 只在 `.vditor-reset` 內容區
  做後援清洗；`preparePrint()` 也先清洗 `getHTML()`。
  - Raw HTML 在 Lute 輸出裡分成 `data-md-source`（Markdown 原文，存檔來源）與
    `.vditor-html-block__display`（顯示用）。清洗只影響顯示層，實測
    `getValue()` 與基準版本逐字相同，不會改寫使用者存檔內容。
- **Bridge**（`_WysiwygBridge`）：`toolbarAction` 只接受 glue 自訂按鈕會送出的 5 個
  名稱；`zoomRequested` 限制在 ±50。其餘方法皆為現有功能所用（存檔、delta、貼上
  預覽、主題），未移除；它們本來就以 generation/revision 驗證，主題值有白名單，
  資源貼上要使用者確認對話框。
- **WebEngine 設定**：`JavascriptCanOpenWindows=False`；`LocalContentCanAccessFileUrls`
  **保留 True**（實測關掉後 file:// 頁面連自己的 bundle script 和文件相對路徑圖片
  都載不到）。

### 預覽（`app/md_converter.py`、`app/renderer.py`）

- `_build_parser()` 覆寫 `md.validateLink`：先跑 markdown-it 預設檢查，再拒絕
  `^[\\/]{2}`（先 percent-decode、去掉 URL parser 會忽略的字元），連結與圖片都擋，
  被拒的語法以純文字呈現。
- `_wrap()` 在 `<meta charset>` 後加 CSP：
  `connect-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'`。
  不限制 script-src，既有 inline script（Mermaid/KaTeX loader、複製鈕）不受影響。
  大檔模式（`html_loading._prepare_html_file`）與 HTML 匯出插入的 `<base>` 都在
  `<head>` 開頭，位於 CSP meta 之前，因此 `base-uri 'none'` 不會擋到 app 自己的
  base（有測試）。
- `_DocumentPage.acceptNavigationRequest`：
  - 修好 PySide6 6.11 上 `QUrl.matches(..., UrlFormattingOption.RemoveFragment)` 的
    TypeError（改用 `_same_document()`：複製後 `setFragment(None)` 再比較），預覽內
    連結、錨點點擊恢復正常。同頁判斷只在 URL 有 fragment 時成立。
  - 點擊 `file://host/...`、裝置命名空間（`//./UNC/…`）或 QUrl 無法表示的無效
    URL，一律拒絕並提示。**導航不套用同主機例外**（第二輪驗收中-3），所以放在
    網路共用資料夾的筆記也不能點連結開旁邊的檔案；基準版 9c5f961 的預覽連結點擊
    本來就因 TypeError 全部失效，不算退化。
- `JavascriptCanOpenWindows=False`（新頁面在 rotation 時沿用設定）。

### 兩種頁面共用：`app/web_security.py`

- `RemoteFileRequestGuard`（`QWebEngineUrlRequestInterceptor`，掛在每個預覽頁與
  WYSIWYG 頁），判斷在 `blocks_file_request()`：
  - **fail closed**：空的或無效的 URL 一律擋。第二輪驗收發現 Chromium 把
    `//./UNC/host/x`、`file:////./UNC/host/x` 正規化成 `file://./UNC/host/x`，QUrl
    無法表示 host `.`，interceptor 拿到空 URL 就放行，Windows 再當成
    `\\.\UNC\host` 走 SMB（已重現 `ADMIN$` 圖片載入成功）。
  - `file:` 帶 host（含 `localhost`、`[::1]`）擋；path 經 percent-decode、反斜線轉
    斜線、收合 `.` 段之後，以 `//`、`/./`、`/?/`、`/UNC/` 開頭的也擋。
  - 目前文件所在主機的例外**只給資源載入**（image、stylesheet、font、media），
    main frame／sub frame／script／object／XHR 等都不給。
  - 實測 interceptor 看得到 `file://host/...` 子資源請求，因此 CSS `url(//host/…)`、
    WYSIWYG 裡的 `![](//host/…)` 也不會連 SMB，而且不需要改 DOM（不影響存檔內容）。
- `is_network_path_link()`（validateLink 用）做同樣的正規化，並處理 `file:` 形式。
- 模組本身不在 import 時載入 Qt WebEngine（md_converter 也在 render worker 執行）。
- 注意：guard 以 `weakref` 參照 view。若從這個 page 擁有的子物件強參照回擁有者，
  PySide 在 wrapper 回收時會 abort（0xC0000409，已重現）。

### 附件（`app/attachment_security.py`）

- 免確認清單移除 `.doc .xls .ppt .rtf .svg .xml`（巨集/OLE/RTF exploit、SVG 在瀏覽器
  file:// 執行 script、XML 由 PI 決定開啟程式），改為「先確認」。
- 封鎖清單新增 `.appx .appxbundle .chm .diagcab .hlp .library-ms .msix .msixbundle .scf .search-ms .searchconnector-ms .settingcontent-ms .website .xll`。

## 3. 驗證

- `tests/test_security_untrusted_markdown.py`：審計 60 個轉換層 payload、UNC 變形、
  bundle 靜態檢查、host CSP、bridge 驗證、guard、連結點擊與附件政策（一般 pytest）；
  WebEngine 層（`RUN_WEBENGINE_TESTS=1`）：WYSIWYG iframe srcdoc 鏈（開檔前/後兩種
  boot 路徑）不會呼叫 bridge、Mermaid strict/KaTeX 無連結但仍能渲染、glue 清洗器、
  預覽 CSP 擋下 WebSocket/fetch 且 Mermaid/KaTeX/複製鈕/task list(QWebChannel) 仍正常、
  預覽錨點與跨筆記連結點擊。
- 反向驗證：把三個 WYSIWYG 資產還原成 9c5f961 版本時，WYSIWYG 測試因
  `PWNED via iframe srcdoc` 送達 bridge 而失敗。
- 第二輪：每個繞過都有回歸測試，先在 d5fef57 上跑確認失敗（W 的 `//./UNC` 圖片
  `naturalWidth` 3840、P 的 Mermaid label 3840、meta refresh／`location`／Mermaid
  真實點擊都讓 `EVIL PAGE SAVE` 送達 bridge、srcdoc 外部 script 執行），修正後通過。
- CI：`tests/test_security_untrusted_markdown.py` 已加入 ci.yml 的 renderer
  WebEngine 分組；`app/web_security.py` 已加入 mypy 檢查清單。
