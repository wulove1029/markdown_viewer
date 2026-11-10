/* AI Agent Watchdog —— 列表視圖（dash.html）、辦公室視圖（office.html）與舊像素辦公室
   （office-pixel.html）共用的前端。
   每個頁面都先 <script src="/static/watchdog.js"></script>（絕對路徑：/office/pixel 也要載得到），
   再接自己的頁面專屬程式碼。
   這裡用 function 宣告的（unhideAll…）才會掛到 window，inline onclick 才叫得到；
   用 let/const 宣告的（esc、refresh…）在同一個全域詞彙環境裡，兩個頁面都看得到。 */

/* 插進 HTML 的字串一律過這裡。單引號也要轉：屬性值用單引號寫時才不會被截斷。 */
const ESC_MAP = {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'};
const esc = s => (s === null || s === undefined ? '' : String(s))
  .replace(/[&<>"']/g, c => ESC_MAP[c]);

function fmtAge(sec){
  const s=Math.max(0,Math.floor(Number(sec)||0));
  if(s<60)return s+' 秒前';
  if(s<3600)return Math.floor(s/60)+' 分鐘前';
  return Math.floor(s/3600)+' 小時 '+Math.floor(s%3600/60)+' 分前';
}

/* 遠端主機斷線時後端把 session 標成 stale + state=OFFLINE：畫面要看得出不是即時資料。 */
function staleText(s){
  return '資料停在 '+((s&&s.last_activity)||'未知時間')+'（連線中斷）';
}

const OFFLINE_MESSAGE='⚠ 無法連線到 Watchdog service';
const REFRESH_MS=5000;                  // 正常輪詢間隔
const BACKOFF_MS=[10000,20000,60000];   // 連續失敗後的退避：5→10→20→60（上限 60 秒）
const VISIBLE_GAP_MS=1000;              // 回到分頁時離上次嘗試夠久，才立刻補刷一次

/* meta 列：更新於 X ・ N 個 session ・（頁面專屬片段）・ 已移除 N 位＋全部叫回，
   最後接上遠端連線警告。opts 可給 countLabel / extra / hiddenLabel / link。 */
function metaHtml(d,opts){
  const o=opts||{};
  const hidden=Number(d.hidden)||0;
  return `更新於 ${esc(d.generated_at)} ・ ${Number(d.sessions.length)} ${esc(o.countLabel||'個 session')}`
    +(o.extra||'')
    +(hidden?` ・ ${esc(o.hiddenLabel||'已移除')} ${hidden} 位 `
      +`<a href="#" onclick="unhideAll();return false">全部叫回</a>`:'')
    +(o.link||'')
    +((d.remote_errors||[]).length?
      `<div style="color:#e08a5a">⚠ 遠端連線問題: ${esc(d.remote_errors.join(' / '))}</div>`:'');
}

function setMeta(html){document.getElementById('meta').innerHTML=html;}
function setMetaText(txt){document.getElementById('meta').textContent=txt;}

/* ---------------- 兩頁共用的輪詢骨架 ----------------
   fetch /api/status → 交給各自的 render(d)。三件事：
   1) 分頁不可見就跳過該輪（背景分頁不打 API），重新可見時立刻補刷一次；
   2) 失敗指數退避 5→10→20→60 秒，成功歸零；
   3) in-flight 序列化，慢回應不會亂序覆寫畫面。 */
let refreshInFlight=false;   // 正在等 /api/status
let refreshQueued=false;     // in-flight 期間又被呼叫，收尾後補跑一次
let refreshFailures=0;       // 連續失敗次數，決定退避長度
let refreshTimer=null;       // 永遠只有一顆計時器
let refreshBase=REFRESH_MS;
let refreshLastAttempt=0;
let renderSnapshot=null;

let refresh=function(){};    // hide/unhide 之後手動刷新用；startWatchdog 之後才有作用

function backoffDelay(failures,base){
  const b=base||REFRESH_MS;
  if(!(failures>0))return b;
  return BACKOFF_MS[Math.min(failures,BACKOFF_MS.length)-1];
}
function pageVisible(){
  return !document.visibilityState||document.visibilityState==='visible';
}
function scheduleRefresh(ms){
  if(refreshTimer!==null)clearTimeout(refreshTimer);
  refreshTimer=setTimeout(refreshTick,ms);
}
function refreshTick(){
  refreshTimer=null;
  doRefresh();
}
let refreshSkipped=false;    // 隱藏期間被擋掉過：回到可見時一定補抓
async function doRefresh(){
  if(!pageVisible()){                                   // 隱藏：不發請求、不留計時器
    refreshSkipped=true;refreshQueued=false;
    if(refreshTimer!==null){clearTimeout(refreshTimer);refreshTimer=null;}
    return;
  }
  if(refreshInFlight){refreshQueued=true;return;}
  refreshInFlight=true;
  refreshLastAttempt=Date.now();
  let ok=false;
  try{
    const response=await fetch('/api/status');
    if(!response.ok)throw new Error(`HTTP ${response.status}`);
    const d=await response.json();
    if(!Array.isArray(d.sessions))throw new Error('Invalid status response');
    renderSnapshot(d);
    ok=true;
  }catch(e){/* 訊息在 finally 一起處理，才能把下次重試時間寫進去 */}
  finally{
    refreshInFlight=false;
    refreshFailures=ok?0:refreshFailures+1;
    const wait=backoffDelay(refreshFailures,refreshBase);
    if(!ok)setMetaText(`${OFFLINE_MESSAGE}（${Math.round(wait/1000)} 秒後重試）`);
    if(pageVisible())scheduleRefresh(wait);
    else refreshSkipped=true;                           // 請求飛行中被藏起來：不排下一輪
    if(refreshQueued){refreshQueued=false;doRefresh();}
  }
}
function onVisibilityChange(){
  if(!pageVisible())return;
  if(refreshTimer!==null){clearTimeout(refreshTimer);refreshTimer=null;}
  if(refreshSkipped||Date.now()-refreshLastAttempt>=VISIBLE_GAP_MS){refreshSkipped=false;doRefresh();}   // 立刻，不等下一個 tick
  else scheduleRefresh(backoffDelay(refreshFailures,refreshBase));
}
function startWatchdog(render,intervalMs){
  renderSnapshot=render;
  refreshBase=intervalMs||REFRESH_MS;
  refresh=doRefresh;
  refreshSkipped=false;
  document.addEventListener('visibilitychange',onVisibilityChange);
  doRefresh();
}

/* ---------------- 寫入類 API：一律檢查 response.ok，失敗不要靜默 ---------------- */
async function postJson(path,payload){
  try{
    const r=await fetch(path,{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify(payload)});
    return !!r.ok;
  }catch(e){return false;}
}

/* 後端認的是 logical_id（穩定），不是陣列位置也不是檔名。 */
function hideSession(id,mtime){
  return postJson('/api/hide',{id:String(id),mtime:Number(mtime)});
}

async function unhideAll(){
  if(!await postJson('/api/unhide',{})){
    setMetaText('⚠ 叫回失敗，請確認 Watchdog service 還活著');
    return false;
  }
  refresh();
  return true;
}

/* ---------------- 淺色／深色：?scheme=light|dark 蓋過系統設定 ----------------
   內嵌本頁的宿主程式換主題時會帶 ?scheme= 重新載入頁面；沒帶就跟
   prefers-color-scheme。刻意不用 theme：舊的像素辦公室已經拿 ?theme=wood|tech 選房間風格。 */
const SCHEMES=['light','dark'];
function schemeFrom(search){
  try{
    const v=new URLSearchParams(search||'').get('scheme');
    return SCHEMES.indexOf(v)>=0?v:null;
  }catch(e){return null;}
}
/* 有指定才寫 data-scheme；CSS 用 :root[data-scheme=…] 蓋過 media query。 */
function applyScheme(){
  const s=schemeFrom(location.search);
  if(s)document.documentElement.setAttribute('data-scheme',s);
  return s;
}

/* ---------------- 檢視切換：全站只有這一份清單 ----------------
   之後加「星圖」只要在這裡多一筆。每個連結都保留目前的 ?scheme=。
   minWidth＝該檢視的最小舒適寬度（px，省略＝不限）；太窄時 effectiveView 退回 FALLBACK_VIEW。 */
const FALLBACK_VIEW='office';
const VIEWS=[
  {id:'list',label:'列表',icon:'📋',path:'/'},
  {id:'office',label:'辦公室',icon:'🏢',path:'/office'},
];
function effectiveView(width,requested){
  const v=VIEWS.find(x=>x.id===requested);
  if(!v)return FALLBACK_VIEW;
  const w=(width===null||width===undefined)?NaN:Number(width);
  if(v.minWidth&&w>=0&&w<v.minWidth)return FALLBACK_VIEW;   // 寬度未知（null/undefined/NaN）時照要求
  return v.id;
}
function viewHref(path,search){
  const s=schemeFrom(search===undefined?location.search:search);
  return s?path+'?scheme='+s:path;
}
function viewLinks(current,search){
  return VIEWS.map(v=>({id:v.id,label:v.label,icon:v.icon,
    href:viewHref(v.path,search),current:v.id===current}));
}
/* 用 DOM 建，不拼 HTML。目前所在的檢視仍是連結（像素辦公室也標「辦公室」，點了回新版）。 */
function renderViewSwitch(el,current){
  if(!el)return;
  el.textContent='';
  for(const v of viewLinks(current)){
    const a=document.createElement('a');
    a.setAttribute('href',v.href);
    a.className='view-link'+(v.current?' current':'');
    if(v.current)a.setAttribute('aria-current','page');
    a.textContent=v.icon+' '+v.label;
    el.appendChild(a);
  }
}

/* ---------------- 辦公室設定 API（/api/office 系列）----------------
   畫風與「我知道了」存在伺服器（office.json），嵌入的面板與一般瀏覽器看到同一份。
   一律回 {ok, status, data}，不丟例外；10 秒逾時當成網路錯誤（status 0）。 */
const OFFICE_STYLES=['win','card','compute','flat','sprite'];
const API_TIMEOUT_MS=10000;
function isOfficeStyle(v){return OFFICE_STYLES.indexOf(v)>=0;}
async function apiCall(path,init){
  let timer=null;
  const ctrl=typeof AbortController==='function'?new AbortController():null;
  const opts=Object.assign({cache:'no-store'},init||{});
  if(ctrl)opts.signal=ctrl.signal;
  try{
    const timeout=new Promise((_,reject)=>{
      timer=setTimeout(()=>{if(ctrl)ctrl.abort();reject(new Error('timeout'));},API_TIMEOUT_MS);
    });
    const r=await Promise.race([fetch(path,opts),timeout]);
    let data=null;
    try{data=await r.json();}catch(e){data=null;}
    return {ok:!!r.ok,status:Number(r.status)||0,data:data};
  }catch(e){
    return {ok:false,status:0,data:null};
  }finally{
    if(timer!==null)clearTimeout(timer);
  }
}
function postApi(path,payload){
  return apiCall(path,{method:'POST',headers:{'Content-Type':'application/json'},
                       body:JSON.stringify(payload)});
}
function fetchOffice(){return apiCall('/api/office');}
function postOfficeStyle(style){return postApi('/api/office/style',{style:String(style)});}
/* 綁的是「這個 session 的這一次警示」；伺服器比對 id＋state（mtime 照送，目前不比）。 */
function postOfficeAck(id,state,mtime){
  return postApi('/api/office/ack',{id:String(id),state:String(state),mtime:Number(mtime)});
}
function postOfficeUnack(id){return postApi('/api/office/unack',{id:String(id)});}
