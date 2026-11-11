/* AI Agent Watchdog —— 列表（dash.html）、辦公室（office.html）、星圖（starmap.html）與舊像素辦公室
   （office-pixel.html）共用的前端。
   每個頁面都先 <script src="/static/watchdog.js"></script>（絕對路徑：/office/pixel 也要載得到），
   再接自己的頁面專屬程式碼。
   這裡用 function 宣告的（unhideAll…）才會掛到 window，inline onclick 才叫得到；
   用 let/const 宣告的（esc、refresh…）在同一個全域詞彙環境裡，兩個頁面都看得到。

   唯讀模式（?readonly=1，值必須剛好是 "1"）：頁面不顯示會寫入伺服器的控制項。
   這是介面層的唯讀，不是安全邊界：HTTP API 本身完全沒變，任何人直接打 API 照樣能寫。
   唯讀時頁面不畫任何會寫入伺服器的控制項，下面的寫入函式也一律直接回傳失敗、不呼叫 fetch。

   精簡模式（?compact=1，判定同 readonly）：給窄的嵌入面板用。只開放星圖：列表、辦公室、像素辦公室
   一載入就換到 /starmap（compactLeave），星圖不畫檢視切換、再窄也不退回辦公室；連不上的電腦整台不顯示、
   remote_errors 的文字一律不顯示（見 viewSnapshot）。沒帶時畫面完全照舊。 */

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
      +(isReadonly()?'':`<a href="#" onclick="unhideAll();return false">全部叫回</a>`):'')
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
  if(isReadonly())return false;          // 唯讀：不碰網路
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
  if(isReadonly())return false;
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

/* ---------------- 唯讀：?readonly=1 ---------------- */
function readonlyFrom(search){
  try{
    // 參數重複時只要任何一個值是 "1" 就算唯讀：在網址後面補上 readonly=1 一定生效。
    return new URLSearchParams(search||'').getAll('readonly').indexOf('1')>=0;
  }catch(e){return false;}
}
function isReadonly(){
  try{return readonlyFrom(location.search);}catch(e){return false;}
}

/* ---------------- 精簡：?compact=1 ----------------
   判定比照 readonly：任何一個 compact 的值剛好是 "1" 就成立。只影響畫面：不寫 localStorage、不送伺服器。 */
function compactFrom(search){
  try{
    return new URLSearchParams(search||'').getAll('compact').indexOf('1')>=0;
  }catch(e){return false;}
}
function isCompact(){
  try{return compactFrom(location.search);}catch(e){return false;}
}
/* compact 只開放星圖：列表、辦公室、像素辦公室在 init 的第一件事呼叫。compact 時 location.replace 到
   /starmap（不留歷史紀錄），query 與 hash 原樣保留（scheme、readonly、compact 與其他參數，順序不變），
   不看伺服器存的檢視偏好。回傳 true＝正在換頁，頁面就此結束（不抓資料、不畫、不寫任何東西）。
   伺服器對這幾個網址本來就先回 302（aiwatch.py 的 compact_redirect），這裡是同一條規則的前端版。 */
const COMPACT_PATH='/starmap';
let compactMoved=false;
function compactLeave(){
  if(compactMoved)return true;
  if(!isCompact())return false;
  compactMoved=true;
  let tail='';
  try{tail=String(location.search||'')+String(location.hash||'');}catch(e){tail='';}
  location.replace(COMPACT_PATH+tail);
  return true;
}

/* ---------------- 檢視切換：全站只有這一份清單 ----------------
   順序就是切換列的順序。每個連結都保留目前的 ?scheme=、?readonly=1 與 ?compact=1。
   minWidth＝該檢視的最小舒適寬度（px，省略＝不限）；太窄時 effectiveView 退回 FALLBACK_VIEW。 */
const FALLBACK_VIEW='office';
const VIEWS=[
  {id:'starmap',label:'星圖',icon:'✨',path:'/starmap',minWidth:370},
  {id:'office',label:'辦公室',icon:'🏢',path:'/office'},
  {id:'list',label:'列表',icon:'📋',path:'/'},
];
function effectiveView(width,requested){
  const v=VIEWS.find(x=>x.id===requested);
  if(!v)return FALLBACK_VIEW;
  const w=(width===null||width===undefined)?NaN:Number(width);
  if(v.minWidth&&w>=0&&w<v.minWidth)return FALLBACK_VIEW;   // 寬度未知（null/undefined/NaN）時照要求
  return v.id;
}
function viewHref(path,search){
  const q=search===undefined?location.search:search;
  const parts=[];
  const s=schemeFrom(q);
  if(s)parts.push('scheme='+s);
  if(readonlyFrom(q))parts.push('readonly=1');
  if(compactFrom(q))parts.push('compact=1');
  return parts.length?path+'?'+parts.join('&'):path;
}
function viewLinks(current,search){
  return VIEWS.map(v=>({id:v.id,label:v.label,icon:v.icon,
    href:viewHref(v.path,search),current:v.id===current}));
}
/* 因太窄而退回辦公室、寬度還沒寬到能換回去時，那個被退回的檢視（例如星圖）暫時點不了：
   點了不會有任何事（頁面本來就會因為太窄再退回來）。回傳它的 id，不是這種情況回 null。 */
const NARROW_HOLD_TITLE='面板太窄，暫時顯示辦公室';
function narrowHold(current,width,search){
  const w=Number(width);
  if(!(w>0)||current!==FALLBACK_VIEW)return null;
  const from=VIEWS.find(x=>x.id===narrowFrom(search));
  return from&&w<=from.minWidth+RESTORE_MARGIN?from.id:null;
}
/* 用 DOM 建，不拼 HTML。目前所在的檢視仍是連結（像素辦公室也標「辦公室」，點了回新版）。
   compact：只有星圖，整個切換列從頁面拿掉（空的 nav 也不留）。 */
function renderViewSwitch(el,current){
  if(!el)return;
  if(isCompact()){if(el.parentNode)el.parentNode.removeChild(el);return;}
  el.textContent='';
  const held=()=>narrowHold(current,typeof innerWidth==='number'?innerWidth:NaN,location.search);
  const links=[];
  for(const v of viewLinks(current)){
    const a=document.createElement('a');
    a.setAttribute('href',v.href);
    a.className='view-link'+(v.current?' current':'');
    if(v.current)a.setAttribute('aria-current','page');
    a.textContent=v.icon+' '+v.label;
    /* 點下去先把選擇記到伺服器（下次開面板就是這一頁），不等回應，失敗也不擋導航。 */
    a.addEventListener('click',ev=>{
      if(held()===v.id){if(ev&&ev.preventDefault)ev.preventDefault();return;}
      try{postOfficeView(v.id);}catch(e){/* 導航照常 */}
    });
    el.appendChild(a);
    links.push([v.id,a]);
  }
  const sync=()=>{
    const h=held();
    for(const [id,a] of links){
      if(id===h){a.setAttribute('aria-disabled','true');a.setAttribute('title',NARROW_HOLD_TITLE);}
      else if(a.getAttribute('aria-disabled')){a.removeAttribute('aria-disabled');a.removeAttribute('title');}
    }
  };
  sync();
  if(typeof addEventListener==='function')addEventListener('resize',sync);
}

/* ---------------- 太窄時改顯示辦公室（帶遲滯，不來回跳） ----------------
   以 innerWidth（面板的 CSS 寬度）判斷：
   - 有 minWidth 的檢視（星圖）寬度低於 minWidth：location.replace 到 FALLBACK_VIEW，
     保留 ?scheme=、?readonly=1、?compact=1，並帶 narrow=<原檢視> 記下「這是因太窄而退回」。
   - FALLBACK_VIEW 只有在帶著這個記號、而且寬度超過 minWidth＋RESTORE_MARGIN 時才換回去。
   - 使用者自己打開（或從切換列點進來）的頁面沒有記號：永遠不被改導。
   寬度 0 或未知（面板被藏起來時常是 0）一律不動。compact（只開放星圖）一律不動：再窄也留在星圖。 */
const NARROW_PARAM='narrow';
const RESTORE_MARGIN=30;
function narrowFrom(search){
  try{
    const v=new URLSearchParams(search===undefined?location.search:search).get(NARROW_PARAM);
    const hit=VIEWS.find(x=>x.id===v);
    return hit&&hit.minWidth?hit.id:null;
  }catch(e){return null;}
}
function narrowHref(path,from,search){
  const src=search===undefined?location.search:search;
  const s=schemeFrom(src);
  const q=[];
  if(s)q.push('scheme='+s);
  if(readonlyFrom(src))q.push('readonly=1');
  if(compactFrom(src))q.push('compact=1');
  if(from)q.push(NARROW_PARAM+'='+from);
  return q.length?path+'?'+q.join('&'):path;
}
/* 該不該換頁：回傳 {id,href} 或 null。純函式，測試直接餵寬度。 */
function guardTarget(current,width,search){
  const w=Number(width);
  if(!(w>0))return null;
  if(compactFrom(search===undefined?location.search:search))return null;
  const v=VIEWS.find(x=>x.id===current);
  if(v&&v.minWidth&&w<v.minWidth){
    const fb=VIEWS.find(x=>x.id===FALLBACK_VIEW);
    return fb?{id:fb.id,href:narrowHref(fb.path,v.id,search)}:null;
  }
  if(current===FALLBACK_VIEW){
    const from=VIEWS.find(x=>x.id===narrowFrom(search));
    if(from&&w>from.minWidth+RESTORE_MARGIN)return {id:from.id,href:viewHref(from.path,search)};
  }
  return null;
}
let guardMoved=false;
/* 頁面 init 的第一件事（星圖與辦公室）：載入時判斷一次，之後每次視窗大小改變再判斷。
   回傳 true＝正在換頁，init 就此結束（不量寬度、不抓資料、一幀都不畫）。 */
function guardView(current){
  const check=()=>{
    if(guardMoved)return true;
    const t=guardTarget(current,typeof innerWidth==='number'?innerWidth:NaN,location.search);
    if(t){guardMoved=true;location.replace(t.href);}
    return guardMoved;
  };
  if(check())return true;
  if(typeof addEventListener==='function')addEventListener('resize',check);
  return false;
}

/* ---------------- 辦公室設定 API（/api/office 系列）----------------
   畫風與「我知道了」存在伺服器（office.json），嵌入的面板與一般瀏覽器看到同一份。
   一律回 {ok, status, data}，不丟例外；10 秒逾時當成網路錯誤（status 0）。 */
const OFFICE_STYLES=['win','card','compute','flat','sprite'];
const API_TIMEOUT_MS=10000;
function isOfficeStyle(v){return OFFICE_STYLES.indexOf(v)>=0;}
async function apiCall(path,init){
  const method=String((init&&init.method)||'GET').toUpperCase();
  if(method!=='GET'&&isReadonly())return {ok:false,status:0,data:null};   // 唯讀：只准 GET
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
  if(isReadonly())return Promise.resolve({ok:false,status:0,data:null});
  return apiCall(path,{method:'POST',headers:{'Content-Type':'application/json'},
                       body:JSON.stringify(payload)});
}
function fetchOffice(){return apiCall('/api/office');}
function postOfficeStyle(style){return postApi('/api/office/style',{style:String(style)});}
/* 檢視偏好（starmap／office／list）。keepalive：緊接著就要換頁，請求不能跟著頁面一起被取消。 */
function postOfficeView(view){
  if(isReadonly())return Promise.resolve({ok:false,status:0,data:null});   // 唯讀：不記住選擇
  return apiCall('/api/office/view',{method:'POST',headers:{'Content-Type':'application/json'},
                                     body:JSON.stringify({view:String(view)}),keepalive:true});
}
/* 綁的是「這個 session 的這一次警示」；伺服器比對 id＋state（mtime 照送，目前不比）。 */
function postOfficeAck(id,state,mtime){
  return postApi('/api/office/ack',{id:String(id),state:String(state),mtime:Number(mtime)});
}
function postOfficeUnack(id){return postApi('/api/office/unack',{id:String(id)});}

/* ================================================================ 三個檢視共用：資料規則
   以下是純函式（資料進、資料出），列表、辦公室、星圖三個頁面共用同一份：
   哪些 session 算「需要你」、哪些已確認、每台電腦一間房、名稱怎麼排。 */
const ALERT_STATES=['WAITING','STUCK'];
function agentKind(agent){
  const base=String(agent===null||agent===undefined?'':agent).split('@')[0].trim().toLowerCase();
  if(base.indexOf('claude')===0)return 'claude';
  if(base.indexOf('codex')===0)return 'codex';
  if(base.indexOf('antigravity')===0)return 'antigravity';
  return 'other';
}
function baseAgent(row){
  const b=row&&(row.base_agent||String(row.agent||'').split('@')[0]);
  return b?String(b):'?';
}
function helperCount(v){
  const n=Math.floor(Number(v));
  return isFinite(n)&&n>0?Math.min(99,n):0;
}
/* 圖像狀態。房間離線或該列 stale：什麼都不是即時的 → off。已確認的警示畫成 idle。
   不認得的 state 一律當閒置（狀態是開放字串，不可以當掉）。 */
function artState(row,acked,roomOffline){
  if(roomOffline||(row&&row.stale))return 'off';
  switch(row&&row.state){
    case 'WORKING':case 'TOOL':case 'BACKGROUND':return 'work';
    case 'WAITING':return acked?'idle':'wait';
    case 'STUCK':return acked?'idle':'stuck';
    case 'OFFLINE':return 'off';
    default:return 'idle';
  }
}
/* 「我知道了」只比 state：acks[logical_id].state 等於目前 state 才算（伺服器負責清掉失效的）。 */
function isAcked(row,acks){
  if(!row||!acks||typeof acks.get!=='function')return false;
  if(ALERT_STATES.indexOf(row.state)<0)return false;
  const a=acks.get(String(row.logical_id||''));
  return !!(a&&a.state===row.state);
}
function ackMapFrom(raw){
  const out=new Map();
  if(!raw||typeof raw!=='object'||Array.isArray(raw))return out;
  for(const id of Object.keys(raw)){
    const v=raw[id];
    if(v&&typeof v==='object'&&typeof v.state==='string')
      out.set(id,{state:v.state,mtime:Number(v.mtime),at:Number(v.at)});
  }
  return out;
}
function ackSig(m){
  return JSON.stringify([...m.entries()].map(([k,v])=>[k,v.state]).sort());
}
/* remote_errors 每行是 "<遠端名>: <原因>"。先比對已知的遠端名（名字裡有冒號也不會切錯），
   其他退回第一個 ": " 之前；poller 自己的錯誤不是某台電腦。 */
function remoteErrorMap(errors,knownNames){
  const out=new Map();
  if(!Array.isArray(errors))return out;
  const names=(knownNames||[]).slice().sort((a,b)=>b.length-a.length);
  for(const e of errors){
    if(typeof e!=='string')continue;
    let name=null;
    for(const n of names)if(e.indexOf(n+': ')===0){name=n;break;}
    if(name===null){
      const i=e.indexOf(': ');
      if(i<=0)continue;
      name=e.slice(0,i);
      if(name==='remote poller 異常')continue;
    }
    if(!out.has(name))out.set(name,[]);
    out.get(name).push(e.slice(name.length+2));
  }
  return out;
}
function rowRemote(s){return s&&typeof s==='object'&&typeof s.remote==='string'&&s.remote?s.remote:null;}
/* 連不上的遠端（＝buildRooms 的 room.offline）：在 remote_errors 裡、且沒有列或每列都 stale。
   有錯誤訊息還不算（連線成功時也會有「時鐘偏移」警告）；真連不上時該遠端每列必為 stale。 */
function offlineRemotes(d){
  const rows=(Array.isArray(d&&d.sessions)?d.sessions:[]).filter(s=>s&&typeof s==='object');
  const names=[];
  for(const s of rows){const r=rowRemote(s);if(r&&names.indexOf(r)<0)names.push(r);}
  const out=new Set();
  for(const name of remoteErrorMap(d&&d.remote_errors,names).keys())
    if(rows.every(s=>rowRemote(s)!==name||s.stale))out.add(name);
  return out;
}
/* 頁面實際要畫的快照。沒帶 ?compact=1：原封不動。compact：拿掉連不上的電腦的列（sessions 與
   attention），remote_errors 清空（連線錯誤與時鐘偏移之類的提醒都不顯示；原因仍在 /api/status）。
   連得上的電腦的列原樣保留（含 stale 與已離線的）。不記任何狀態：電腦一連上，下一輪資料就有它。 */
function viewSnapshot(d){
  if(!isCompact()||!d||typeof d!=='object')return d;
  const off=offlineRemotes(d);
  const keep=s=>!off.has(rowRemote(s));
  const out=Object.assign({},d,{remote_errors:[]});
  if(Array.isArray(d.sessions))out.sessions=d.sessions.filter(keep);
  if(Array.isArray(d.attention))out.attention=d.attention.filter(keep);
  return out;
}
function cmpText(a,b){
  return String(a===null||a===undefined?'':a).localeCompare(String(b===null||b===undefined?'':b));
}
/* 房內順序：專案 → agent → host → id。絕不依狀態排，狀態變了位置不動。 */
function compareRows(a,b){
  const ia=String(a.logical_id||''),ib=String(b.logical_id||'');
  return cmpText(a.project,b.project)||cmpText(a.agent,b.agent)||cmpText(a.host,b.host)||(ia<ib?-1:ia>ib?1:0);
}
/* 同一台電腦同專案多個 session：名稱加 #n（依桌序），卡片與按鈕才分得出來。 */
function sessionNames(rows){
  const total=new Map();
  for(const r of rows){const p=r.project||'—';total.set(p,(total.get(p)||0)+1);}
  const seen=new Map();
  return rows.map(r=>{
    const p=r.project||'—';
    if(total.get(p)<2)return p;
    const n=(seen.get(p)||0)+1;seen.set(p,n);
    return p+' #'+n;
  });
}
/* 一台電腦一間房：本機（machine.name）＋每個遠端（row.remote）。offlineRemotes 認定連不上的遠端是
   離線房：整間畫成 off、不算警示（compact 時 viewSnapshot 已先把它拿掉，不會有離線房）。
   順序：本機 → 連得上的遠端 → 離線的遠端，同組依名稱。 */
function buildRooms(d,acks){
  const rows=Array.isArray(d&&d.sessions)?d.sessions:[];
  const machine=d&&d.machine&&typeof d.machine==='object'?d.machine:null;
  const localName=machine&&machine.name?String(machine.name):'本機';
  const map=new Map();
  const ensure=(key,name,local)=>{
    let r=map.get(key);
    if(!r){r={key,name,local,offline:false,errors:[],rows:[]};map.set(key,r);}
    return r;
  };
  ensure('local',localName,true);
  const remoteNames=[];
  for(const s of rows){
    if(!s||typeof s!=='object')continue;
    const remote=typeof s.remote==='string'&&s.remote?s.remote:null;
    if(remote&&remoteNames.indexOf(remote)<0)remoteNames.push(remote);
    (remote?ensure('remote:'+remote,remote,false):ensure('local',localName,true)).rows.push(s);
  }
  const offline=offlineRemotes(d);
  for(const [name,msgs] of remoteErrorMap(d&&d.remote_errors,remoteNames)){
    const r=ensure('remote:'+name,name,false);
    r.errors=msgs;
    r.offline=offline.has(name);
  }
  const rooms=[...map.values()];
  for(const room of rooms){
    const sorted=room.rows.slice().sort(compareRows);
    const names=sessionNames(sorted);
    const used=new Map();
    room.desks=sorted.map((row,i)=>{
      const base=String(row.logical_id||row.file||'?');
      const n=(used.get(base)||0)+1;used.set(base,n);
      const uid=n>1?base+'#'+n:base;
      const acked=isAcked(row,acks);
      const alert=ALERT_STATES.indexOf(row.state)>=0&&!row.stale&&!room.offline;
      return {key:room.key+'::'+uid,id:String(row.logical_id||''),row,room,name:names[i],
              acked:alert&&acked,attention:alert&&!acked,
              art:artState(row,alert&&acked,room.offline),
              helpers:helperCount(row.subagents),background:helperCount(row.background_pending)};
    });
    room.waiting=room.desks.filter(x=>x.attention).length;
    room.busy=room.desks.filter(x=>x.art==='work').length;
    delete room.rows;
  }
  rooms.sort((a,b)=>(a.local?0:1)-(b.local?0:1)||(a.offline?1:0)-(b.offline?1:0)
                   ||cmpText(a.name,b.name)||(a.key<b.key?-1:a.key>b.key?1:0));
  return rooms;
}

/* ================================================================ 共用：小工具與文字 */
const NOTICE_MS=8000;
const $=id=>document.getElementById(id);
function el(tag,cls,text){
  const e=document.createElement(tag);
  if(cls)e.className=cls;
  if(text!==undefined)e.textContent=text;
  return e;
}
function setText(e,text){if(e.textContent!==text)e.textContent=text;}
function setHidden(e,h){if(h)e.setAttribute('hidden','');else if(e.hasAttribute('hidden'))e.removeAttribute('hidden');}
function setAttr(e,k,v){
  if(v===null||v===undefined){if(e.hasAttribute(k))e.removeAttribute(k);}
  else if(e.getAttribute(k)!==String(v))e.setAttribute(k,v);
}
/* 長名稱優先在 _ - . / \ 之後換行（<wbr>），真的放不下才任意斷（CSS overflow-wrap）。 */
function setBreakable(e,text){
  e.textContent='';
  let part='';
  for(const ch of String(text)){
    part+=ch;
    if('_-./\\'.indexOf(ch)>=0){e.appendChild(document.createTextNode(part));e.appendChild(document.createElement('wbr'));part='';}
  }
  if(part)e.appendChild(document.createTextNode(part));
}
function chipClass(row,acked){
  if(acked)return 'chip chip-acked';
  switch(row.state){
    case 'WORKING':return 'chip chip-working';
    case 'TOOL':return 'chip chip-tool';
    case 'BACKGROUND':return 'chip chip-background';
    case 'WAITING':return 'chip chip-waiting';
    case 'STUCK':return 'chip chip-stuck';
    case 'IDLE':return 'chip chip-idle';
    case 'OFFLINE':return 'chip chip-offline';
    default:return 'chip chip-unknown';
  }
}
function stateText(row){
  const zh=row.state_zh!==null&&row.state_zh!==undefined&&row.state_zh!==''?String(row.state_zh):'';
  return zh||(row.state?String(row.state):'未知狀態');
}
function chipText(desk){return desk.acked?'已確認・'+stateText(desk.row):stateText(desk.row);}
function who(desk){return desk.name+' · '+baseAgent(desk.row)+' · '+desk.room.name;}
/* 同一個容器裡的子節點以 key 對應：沒變就沿用（hover、焦點、canvas 都保得住），只調順序。 */
function syncKeyed(box,map,items,make,update,destroy){
  const seen=new Set();
  items.forEach((it,i)=>{
    seen.add(it.key);
    let rec=map.get(it.key);
    if(!rec){rec=make(it);map.set(it.key,rec);}
    update(rec,it);
    const at=box.children[i];
    if(at!==rec.el)box.insertBefore(rec.el,at||null);
  });
  for(const [k,rec] of map)if(!seen.has(k)){
    if(destroy)destroy(rec);
    if(rec.el.parentNode)rec.el.parentNode.removeChild(rec.el);
    map.delete(k);
  }
}

const ART_FRAME_MS=66;                   // 最快約 15 fps
/* ================================================================ 全頁唯一的時鐘
   所有會動的圖都註冊在這裡：同一時間最多一個 setTimeout 或一個 rAF，最快 ART_FRAME_MS；
   只重畫自己間隔到了的圖。頁面隱藏就停（rAF 在隱藏的面板裡會停，setInterval 只會被節流），
   回到可見再排程。沒有任何會動的圖時完全不排程。 */
const clockEntries=new Set();
let clockTimer=null,clockFrame=0,clockListening=false,clockTicks=0;
function clockNow(){return typeof performance!=='undefined'&&performance.now?performance.now():Date.now();}
function clockHidden(){return document.visibilityState==='hidden';}
function clockCancel(){
  if(clockTimer!==null){clearTimeout(clockTimer);clockTimer=null;}
  if(clockFrame){cancelAnimationFrame(clockFrame);clockFrame=0;}
}
function clockSchedule(){
  if(clockTimer!==null||clockFrame||!clockEntries.size||clockHidden())return;
  const now=clockNow();
  let due=Infinity;
  for(const e of clockEntries)due=Math.min(due,e.last+e.painter.interval);
  clockTimer=setTimeout(()=>{clockTimer=null;clockFrame=requestAnimationFrame(clockRun);},
                        Math.max(ART_FRAME_MS,due-now));
}
function clockRun(time){
  clockFrame=0;
  if(clockHidden())return;
  clockTicks++;
  for(const e of [...clockEntries]){
    if(!clockEntries.has(e)||time-e.last<e.painter.interval-8)continue;   // 8ms 抖動寬限
    e.last=time;
    try{e.painter.paint(time);}catch(err){clockEntries.delete(e);}
  }
  clockSchedule();
}
function onClockVisibility(){
  if(clockHidden())clockCancel();
  else clockSchedule();
}
function startPainting(painter){
  if(!isFinite(painter.interval)||painter.interval<=0)return ()=>{};
  const entry={painter,last:clockNow()};
  clockEntries.add(entry);
  if(!clockListening){document.addEventListener('visibilitychange',onClockVisibility);clockListening=true;}
  if(clockTimer!==null){clearTimeout(clockTimer);clockTimer=null;}   // 新來的可能更早到期
  clockSchedule();
  return ()=>{
    clockEntries.delete(entry);
    if(clockEntries.size)return;
    clockCancel();
    if(clockListening){document.removeEventListener('visibilitychange',onClockVisibility);clockListening=false;}
  };
}
function clockStats(){
  return {painters:clockEntries.size,scheduled:clockTimer!==null||clockFrame!==0,ticks:clockTicks};
}

/* ================================================================ 共用：「我知道了」與辦公室設定的同步
   /api/office 的確認紀錄（和辦公室的畫風）三個檢視共用同一份流程；各頁只提供
   「怎麼重畫」「現在有哪些桌／星」「額外要讀哪些欄位」。 */
const office={style:null,acks:new Map()};
let officeReady=false,officeLoading=false,officeQueued=false,officeSeq=0,officeAskedAt=-1e12;
let officeWaitTimer=null,statusSeen=false;
/* /api/office 卡住時最多等這麼久：之後先照「沒有確認紀錄」畫，回音來了再套用。 */
const OFFICE_WAIT_MS=1500;
let noticeTimer=null;
const officeFlow={repaint:()=>{},desks:()=>[],apply:null,need:null};
function showNotice(text){
  const n=$('notice');
  if(!n)return;
  n.textContent=text;setHidden(n,false);
  if(noticeTimer!==null)clearTimeout(noticeTimer);
  noticeTimer=setTimeout(()=>{noticeTimer=null;setHidden(n,true);},NOTICE_MS);
}
/* 「我知道了」：畫面先降級（立即回饋），伺服器沒收下就退回並說出來。
   409＝狀態已經變了：重新抓 /api/status 與 /api/office 再畫。 */
async function ackSession(id){
  if(isReadonly())return false;
  const d=officeFlow.desks().find(x=>x.id===id&&x.attention);
  if(!d)return false;
  const row=d.row;
  officeSeq++;
  const next=new Map(office.acks);
  next.set(d.id,{state:row.state,mtime:Number(row.mtime),at:Date.now()/1000});
  office.acks=next;
  officeFlow.repaint();
  const r=await postOfficeAck(d.id,row.state,Number(row.mtime));
  if(r.ok){loadOffice();return true;}
  officeSeq++;
  const back=new Map(office.acks);back.delete(d.id);office.acks=back;
  if(r.status===409){
    showNotice('這位的狀態剛剛變了，已重新整理。');
    officeFlow.repaint();refresh();loadOffice();
    return false;
  }
  showNotice('⚠ 沒能記下「我知道了」，請確認 Watchdog service 還活著。');
  officeFlow.repaint();
  return false;
}
async function unackSession(id){
  if(isReadonly())return false;
  const prev=office.acks.get(id);
  if(!prev)return false;
  officeSeq++;
  const next=new Map(office.acks);next.delete(id);office.acks=next;
  officeFlow.repaint();
  const r=await postOfficeUnack(id);
  if(r.ok){loadOffice();return true;}
  officeSeq++;
  const back=new Map(office.acks);back.set(id,prev);office.acks=back;
  showNotice('⚠ 沒能取消確認，請確認 Watchdog service 還活著。');
  officeFlow.repaint();
  return false;
}
/* GET /api/office：確認紀錄，加上頁面自己要讀的欄位（officeFlow.apply，回傳有沒有變）。
   本機有尚未回音的改動時（officeSeq 變了）不拿舊回應蓋掉。 */
function applyOffice(data){
  if(!data||typeof data!=='object')return false;
  let changed=false;
  if(officeFlow.apply&&officeFlow.apply(data))changed=true;
  const acks=ackMapFrom(data.acks);
  if(ackSig(acks)!==ackSig(office.acks)){office.acks=acks;changed=true;}
  return changed;
}
async function loadOffice(){
  if(document.visibilityState==='hidden')return;            // 隱藏時完全不發請求
  if(officeLoading){officeQueued=true;return;}
  officeLoading=true;officeAskedAt=Date.now();
  const seq=officeSeq;
  let changed=false;
  if(!officeReady&&officeWaitTimer===null){
    officeWaitTimer=setTimeout(()=>{
      officeWaitTimer=null;
      if(officeReady)return;
      officeReady=true;
      if(statusSeen)officeFlow.repaint();
    },OFFICE_WAIT_MS);
  }
  try{
    const r=await fetchOffice();
    if(r.ok&&seq===officeSeq)changed=applyOffice(r.data);
  }finally{
    officeLoading=false;
    if(officeWaitTimer!==null){clearTimeout(officeWaitTimer);officeWaitTimer=null;}
    const first=!officeReady;
    officeReady=true;
    if(changed||first)officeFlow.repaint();
    if(officeQueued){officeQueued=false;loadOffice();}
  }
}
/* 每次 /api/status 回來時呼叫：確認紀錄讀過了就重畫；太久沒問就順便問一次。 */
function officeAfterStatus(){
  statusSeen=true;
  if(officeReady)officeFlow.repaint();
  if(Date.now()-officeAskedAt>=2000)loadOffice();
}
/* 頁面 init 時呼叫一次：{repaint, desks, apply?, need?}。
   會接上「我知道了」「取消確認」「還有 N 位」三種按鈕（data-act＝ack／unack／more）、
   回到可見時重讀 /api/office，並馬上讀一次。 */
function startOfficeSync(opts){
  officeFlow.repaint=opts.repaint;
  officeFlow.desks=opts.desks;
  officeFlow.apply=opts.apply||null;
  officeFlow.need=opts.need||null;
  document.addEventListener('click',ev=>{
    const t=ev.target,b=t&&t.closest?t.closest('[data-act]'):null;
    if(!b)return;
    const act=b.getAttribute('data-act');
    if(isReadonly()&&(act==='ack'||act==='unack'))return;
    if(act==='ack')ackSession(b.getAttribute('data-id'));
    else if(act==='unack')unackSession(b.getAttribute('data-id'));
    else if(act==='more'&&officeFlow.need)officeFlow.need.toggle();
    else return;
    if(ev.preventDefault)ev.preventDefault();
  });
  document.addEventListener('visibilitychange',()=>{
    if(document.visibilityState!=='hidden')loadOffice();
  });
  loadOffice();
}

/* ================================================================ 共用：「需要你」與「已確認」
   三個頁面的 DOM id 相同：need、needTitle、needCount、needNone、needList、needMore、acked、ackedList。
   超過兩列（NEED_ROWS）收合成「還有 N 位」；每列有幾欄量 #needList 的 grid，量不到就用寬度推算。
   opts：
     rooms()  目前的 buildRooms 結果
     icon     {make()→{node,…}, update(rec,desk), destroy?(rec)}：每項最前面的小圖（見 needDotIcon）
     point    可省略 {on(key), off(key)}：滑過或聚焦一項時通知頁面（星圖用它圈出那顆星）
   回傳 {paint, toggle, stats}。 */
const NEED_ROWS=2;                       // 超過兩列收合
const NEED_CARD_MIN=250;                 // 與 .need-list 的 minmax 相同
const NEED_GAP=8;
const NEED_DOT_COLORS={wait:'#ffc24d',stuck:'#ff5a52'};
function needDotIcon(colors){
  const c=colors||NEED_DOT_COLORS;
  return {
    make(){
      const dot=el('i','need-dot');dot.setAttribute('aria-hidden','true');
      return {node:dot,dot};
    },
    update(rec,d){rec.dot.style.background=c[d.art==='stuck'?'stuck':'wait'];},
  };
}
function createNeedPanel(opts){
  const needRecs=new Map(),ackedRecs=new Map();
  let open=false,columns=1,painted=false;
  function measureColumns(){
    const list=$('needList');
    let n=0;
    try{
      if(typeof getComputedStyle==='function'){
        const t=String(getComputedStyle(list).gridTemplateColumns||'');
        if(t&&t!=='none')n=t.split(' ').filter(x=>x&&x!=='none').length;
      }
    }catch(e){n=0;}
    if(!(n>0)){
      const w=Number(list.clientWidth)||0;
      n=w>0?Math.max(1,Math.floor((w+NEED_GAP)/(NEED_CARD_MIN+NEED_GAP))):1;
    }
    return n;
  }
  function pointable(node,rec){
    if(!opts.point)return;
    const on=()=>opts.point.on(rec.key),off=()=>opts.point.off(rec.key);
    node.addEventListener('mouseenter',on);node.addEventListener('mouseleave',off);
    node.addEventListener('focusin',on);node.addEventListener('focusout',off);
  }
  function makeNeed(it){
    const li=el('li','need-item');
    const icon=opts.icon.make();
    const text=el('span','need-text');
    const name=el('strong');const meta=el('small');
    text.appendChild(name);text.appendChild(meta);
    let btn=null;
    if(!isReadonly()){                   // 唯讀：不畫按鈕
      btn=el('button','btn','我知道了');
      btn.setAttribute('type','button');btn.setAttribute('data-act','ack');
      btn.setAttribute('title','確認沒事：不再算進「需要你」；這個 session 的狀態一變就會自動恢復。');
    }
    li.appendChild(icon.node);li.appendChild(text);if(btn)li.appendChild(btn);
    const rec={el:li,icon,name,meta,btn,nameText:null,key:it.key};
    pointable(li,rec);
    return rec;
  }
  function updateNeed(rec,it){
    const d=it.desk,row=d.row;
    rec.el.className='need-item'+(row.state==='STUCK'?' stuck':'');
    rec.el.setAttribute('data-need',d.id);
    rec.el.setAttribute('data-key',d.key);
    opts.icon.update(rec.icon,d);
    if(rec.nameText!==d.name){rec.nameText=d.name;setBreakable(rec.name,d.name);}
    setText(rec.meta,d.room.name+' · '+baseAgent(row)+' · '+stateText(row)+' · '+fmtAge(row.age_s));
    if(rec.btn){
      rec.btn.setAttribute('data-id',d.id);
      rec.btn.setAttribute('aria-label','我知道了：'+who(d));
    }
  }
  function makeAckedBtn(it){
    if(isReadonly()){                    // 唯讀：只顯示已確認的名字，不能取消
      const s=el('span','acked-name');
      const rec={el:s,key:it.key};
      pointable(s,rec);
      return rec;
    }
    const b=el('button');
    b.setAttribute('type','button');b.setAttribute('data-act','unack');
    b.setAttribute('title','取消「已確認」，重新算進警示。');
    const rec={el:b,key:it.key};
    pointable(b,rec);
    return rec;
  }
  function updateAckedBtn(rec,it){
    const d=it.desk;
    rec.el.setAttribute('data-key',d.key);
    if(isReadonly()){setText(rec.el,d.name);return;}
    rec.el.setAttribute('data-id',d.id);
    rec.el.setAttribute('aria-label','取消已確認：'+who(d));
    setText(rec.el,d.name+' · 取消');
  }
  function paint(){
    painted=true;
    const need=[],acked=[];
    for(const r of opts.rooms())for(const d of r.desks){
      if(d.attention)need.push(d);
      else if(d.acked)acked.push(d);
    }
    const count=$('needCount');
    setText(count,need.length?need.length+' 位':'');
    setHidden(count,!need.length);
    setText($('needNone'),'沒有人在等你。');
    setHidden($('needNone'),need.length>0);
    setHidden($('needList'),!need.length);
    if(need.length>NEED_ROWS)columns=measureColumns();
    const limit=NEED_ROWS*Math.max(1,columns);
    const folded=open?0:Math.max(0,need.length-limit);
    const shown=need.slice(0,need.length-folded);
    syncKeyed($('needList'),needRecs,shown.map(d=>({key:d.key,desk:d})),makeNeed,updateNeed,
              rec=>{if(opts.icon.destroy)opts.icon.destroy(rec.icon);});
    const more=$('needMore');
    setHidden(more,need.length<=limit);
    setAttr(more,'aria-expanded',open?'true':'false');
    setAttr(more,'data-folded',String(folded));
    setText(more,open?'收合':'還有 '+folded+' 位');
    if(need.length<=limit&&open)open=false;
    setHidden($('acked'),!acked.length);
    syncKeyed($('ackedList'),ackedRecs,acked.map(d=>({key:d.key,desk:d})),makeAckedBtn,updateAckedBtn);
  }
  function toggle(){open=!open;paint();}
  if(typeof addEventListener==='function')addEventListener('resize',()=>{if(painted)paint();});
  return {paint,toggle,stats:()=>({open,columns,need:needRecs.size,acked:ackedRecs.size})};
}
