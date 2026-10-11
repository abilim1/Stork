import {PriceChart} from './chart.js';
import {reportMarkup, narrative, roundFinancialText} from './reports.js';

const $ = id => document.getElementById(id);
const chart = new PriceChart($('price-chart'),$('chart-tooltip'),$('chart-empty'));
let state=null, range='5D', candles=[], chartRequest=0, selectedReport=null, initialized=false, lastEvent=null, refreshing=false, lastChartRefresh=0;
const chartControls = document.querySelectorAll('#chart-interval,#chart-style,#chart-session,[data-range]');
chartControls.forEach(control => control.disabled = true);
$('chart-data-button').disabled = true;
function preference(key, fallback) {try {return localStorage.getItem(key) || fallback;} catch {return fallback;}}
function savePreference(key, value) {try {localStorage.setItem(key, value);} catch { /* Browser storage may be disabled. */ }}
let symbol = preference('stork-stock', 'US.MU');
if (!['US.MU','US.SNDK'].includes(symbol)) symbol='US.MU';
let stockEpoch=0, reportMode=preference('stork-report-mode','detailed'), reportRenderKey='';
const sourceExpansion = new Map();
const sourceFilters = new Map();
const sectionHighlightTimers = new WeakMap();
const researchBusy=()=>Boolean(state?.research.busy || ['running','cancelling'].includes(state?.research.state));
let cancellingRunId=null;
let reportHistoryKey = '';
const ticker=()=>symbol.slice(3);
const stockTabs=document.querySelectorAll('[data-stock]');
function updateStockTabs() {
  stockTabs.forEach(button=>{
    const selected=button.dataset.stock===symbol;
    button.classList.toggle('active',selected);
    button.setAttribute('aria-pressed',String(selected));
  });
}
stockTabs.forEach(button=>button.addEventListener('click',()=>switchStock(button.dataset.stock)));
updateStockTabs();
async function switchStock(next) {
  if(next===symbol||!['US.MU','US.SNDK'].includes(next))return;
  symbol=next;stockEpoch++;chartRequest++;initialized=false;selectedReport=null;state=null;reportRenderKey='';lastChartRefresh=0;
  savePreference('stork-stock',symbol);
  updateStockTabs();
  $('modal').close();chart.setData([],true);candles=[];
  $('chart-data-button').disabled=true;
  $('settings-feedback').textContent='';$('weight-total').textContent='Total 100%';$('weight-total').classList.toggle('down',false);
  $('archive-news').innerHTML='';$('positions').innerHTML='';$('report-body').innerHTML='';$('report-list').innerHTML='';
  $('current-price').textContent='—';$('price-change').textContent='Loading selected stock…';
  $('connection-banner').hidden=false;$('connection-banner').textContent='Loading '+ticker()+'…';
  document.querySelectorAll('.view').forEach(el=>el.hidden=true);
  chartControls.forEach(control=>control.disabled=true);
  await refresh();
}
const labels={company:'Company-specific catalysts',semiconductors:'Memory & semiconductors',price_volume:'Price & volume',macro:'Market & macro',rotation:'Sector rotation',oil_geopolitics:'Oil & geopolitics',crypto_sentiment:'Crypto & sentiment'};
const sessionLabel={premarket:'Premarket',regular:'Regular hours',afterhours:'After-hours',overnight:'Overnight',historical:'Historical'};
const money=v=>v==null?'—':new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',minimumFractionDigits:2,maximumFractionDigits:2}).format(v);
const pct=v=>v==null?'—':`${v>=0?'+':''}${v.toFixed(2)}%`;
const dateTime=(ts,options={})=>ts?new Intl.DateTimeFormat('en-US',{timeZone:state?.settings.timezone||'America/New_York',month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',hour12:false,...options}).format(new Date(ts*1000)):'Unknown';
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const safeUrl=url=>{try{const u=new URL(url);return ['https:','http:'].includes(u.protocol)?u.href:'#';}catch{return '#';}};
function toast(message){$('toast').textContent=message;$('toast').hidden=false;clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('toast').hidden=true,4500);}
function signed(node,value){node.textContent=pct(value);node.classList.toggle('up',value!=null&&value>=0);node.classList.toggle('down',value!=null&&value<0);}

async function api(path,body,stock=symbol){
  path+=(path.includes('?')?'&':'?')+new URLSearchParams({symbol:stock});
  const headers={};const token=sessionStorage.getItem('stork-token');if(token)headers.Authorization=`Bearer ${token}`;
  if(body!==undefined){headers['Content-Type']='application/json';headers['X-Stork-Request']='1';}
  const response=await fetch(path,{method:body===undefined?'GET':'POST',headers,body:body===undefined?undefined:JSON.stringify(body)});
  const result=await response.json();
  if(response.status===401){showLogin();throw new Error('Enter the server access token');}
  if(!response.ok)throw new Error(result.error||'The request failed');return result;
}
function modal(title,html){$('modal-title').textContent=title;$('modal-content').innerHTML=html;if(!$('modal').open)$('modal').showModal();}
$('close-modal').addEventListener('click',()=>$('modal').close());
$('modal').addEventListener('click',e=>{if(e.target===$('modal')){const r=$('modal').getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)$('modal').close();}});
function showLogin(){if($('access-form')){if(!$('modal').open)$('modal').showModal();return;}modal('Connect to your Stork server','<form id="access-form"><label>Server access token<input type="password" name="token" required autocomplete="off"></label><p class="subtle-note">Use the token configured by the server owner. The connection should use HTTPS when accessed remotely.</p><button class="button primary">Connect</button></form>');$('access-form').onsubmit=e=>{e.preventDefault();sessionStorage.setItem('stork-token',new FormData(e.target).get('token'));$('modal').close();refresh();};}

function navigate(){if(!state){document.querySelectorAll('.view').forEach(el=>el.hidden=true);return;}const name=['overview','research','journal','settings'].includes(location.hash.slice(1))?location.hash.slice(1):'overview';document.querySelectorAll('.view').forEach(el=>el.hidden=el.id!==`${name}-view`);document.querySelectorAll('[data-view]').forEach(el=>el.classList.toggle('active',el.dataset.view===name));$('page-name').textContent={overview:'Overview',research:'Research',journal:'Position journal',settings:'Preferences'}[name];if(name==='overview')requestAnimationFrame(()=>chart.draw());if(name==='research')renderReports();if(name==='journal')renderPositions();}
window.addEventListener('hashchange',navigate);navigate();
window.addEventListener('stork-theme-change',()=>chart.draw());

async function refresh(){
  if(refreshing)return;refreshing=true;const epoch=stockEpoch;
  try{
    const received=await api('/api/state');if(epoch!==stockEpoch)return;state=received;
    $('connection-banner').hidden=true;
    $('research-heading').textContent=state.stock.name+' research';$('journal-heading').textContent=state.stock.name+' positions';
    $('settings-heading').textContent=state.stock.name+' preferences';
    $('stock-name').textContent=state.stock.name+' overview';$('stock-ticker').textContent=state.stock.ticker;
    $('workspace-stock').textContent=state.stock.company+' · NASDAQ';$('price-legend').textContent=state.stock.ticker+' price';
    $('price-chart').setAttribute('aria-label','Interactive '+state.stock.ticker+' price and volume chart');
    document.title='Stork — '+state.stock.name;
    if(!initialized){fillSettings();$('chart-interval').value=state.settings.chart_interval;$('chart-style').value=state.settings.chart_style;chart.style=state.settings.chart_style;initialized=true;chartControls.forEach(c=>c.disabled=false);navigate();await loadChart(true);}
    if(epoch!==stockEpoch)return;
    renderMarket();renderReports();renderPositions();renderEvents();
    if(Date.now()-lastChartRefresh>15000)await loadChart(false);
  }catch(error){if(epoch===stockEpoch){$('connection-banner').hidden=false;$('connection-banner').textContent=error.message;}}
  finally{refreshing=false;if(epoch!==stockEpoch)refresh();}
}
function renderMarket(){const m=state.market,feed=m.feed,quote=feed.quotes[symbol];$('current-price').textContent=money(m.price);const change=$('price-change');change.textContent=m.change_pct==null?'No comparable regular close':`${pct(m.change_pct)} · ${['afterhours','overnight'].includes(m.session)?'vs latest regular close':'vs previous regular close'}`;change.classList.toggle('up',m.change_pct>=0&&m.change_pct!=null);change.classList.toggle('down',m.change_pct<0);signed($('open-change'),m.from_open_pct);$('open-reference').textContent=m.regular_open?`Regular open ${money(m.regular_open)}`:'No regular session for this price date';const week=m.weekly.at(-1);signed($('week-change'),week?.return_pct);$('week-reference').textContent=week?`${week.week} → ${week.end}${week.partial?' · short / unfinished week':''}`:'No comparable weekly history';const running=researchBusy();const researchingName={'US.MU':'Micron','US.SNDK':'SanDisk'}[state.research.symbol]||'stock';$('research-label').textContent=running?`Researching ${researchingName}…`:state.research.state==='error'?'Needs attention':state.reports.some(r=>r.status==='complete')?'A new perspective':'Ready when you are';$('research-detail').textContent=running?`Researching ${state.research.symbol?.slice(3)||ticker()} · shared research worker`:state.research.state==='error'?'Open Research for details':state.settings.analysis_enabled?'Automatic research enabled':'Run on demand or set a schedule';document.querySelectorAll('.run-research').forEach(b=>{b.disabled=running;b.textContent=running?`Researching ${researchingName}…`:'Analyze now';});$('feed-badge').className='status-pill'+(m.live?' live':'');$('feed-badge').innerHTML=`<span class="small-dot"></span>${m.live?'Live · ':''}${esc(sessionLabel[m.session])}${!m.live?' · last observed':''}`;$('connect-button').textContent=feed.state==='connected'?'Connected ✓':feed.state==='connecting'?'Connecting…':'Connect OpenD';$('connect-button').disabled=feed.state==='connected'||feed.state==='connecting';$('feed-time').textContent=m.asof?`${dateTime(m.asof)} · ${state.settings.timezone==='Asia/Singapore'?'SGT':'ET'}`:'No live feed connected';$('data-note').textContent=m.live?'Source: connected Moomoo feed':`Last observed ${dateTime(m.asof)} · ${m.source}`;
  $('context-feed-status').textContent={connected:'Connected',connecting:'Connecting…',error:'Connection error'}[feed.state]||'Disconnected';
  const descriptions={'US.QQQ':'Nasdaq 100','US.SMH':'Semiconductors','US.SPY':'S&P 500'};
  $('benchmarks').innerHTML=Object.entries(descriptions).map(([symbol,name])=>{const q=feed.quotes[symbol];const age=q?.time==null?Infinity:Date.now()/1000-q.time;const fresh=feed.state==='connected'&&age>=-5&&age<120;return `<div class="benchmark"><span class="benchmark-icon">${symbol.slice(3,4)}</span><div><strong>${symbol.slice(3)}</strong><small>${name}</small></div><div class="benchmark-price">${money(q?.price)}<small class="${q?.change_pct>=0?'up':'down'}">${pct(q?.change_pct)}</small><small>${q?esc(sessionLabel[q.session])+(fresh?'':' · older quote'):'Not connected'}</small></div></div>`;}).join('');
  const weeks=m.weekly.slice(-6),max=Math.max(...weeks.map(w=>Math.abs(w.return_pct||0)),1);$('weekly-chart').innerHTML=weeks.map(w=>`<div class="week-column" title="${esc(w.week+' to '+w.end)}"><strong class="${w.return_pct>=0?'up':'down'}">${pct(w.return_pct)}${w.partial?'*':''}</strong><div class="week-track"><div class="week-bar ${w.return_pct<0?'negative':''}" style="height:${Math.max(3,Math.abs(w.return_pct||0)/max*46)}px"></div></div>${esc(w.week.slice(5))}</div>`).join('');
  const friday=m.friday; $('friday-record').textContent=friday?.sample_size?`Friday record: ${friday.negative_count} of ${friday.sample_size} observed Fridays closed lower than Thursday. Historical sample, not a forecast.`:''; $('friday-record').title=friday?`${friday.start} to ${friday.end}. ${friday.definition}`:'';
  const latest=state.reports.find(r=>r.status==='complete');$('report-count').textContent=state.reports.filter(r=>r.status==='complete').length;
  $('insight-preview').innerHTML=latest?`<p class="empty-description"><strong>${esc(roundFinancialText(latest.body.headline).replaceAll('**',''))}</strong><br>${esc(roundFinancialText(latest.body.summary.slice(0,230)).replaceAll('**',''))}${latest.body.summary.length>230?'…':''}</p>`:'<p class="empty-description">Bring price action and the day’s news together. Your reports keep intraday momentum and the longer-term outlook in view.</p>';
  if(chart.timezone!==state.settings.timezone){chart.timezone=state.settings.timezone;chart.draw();} updateClock();
}

async function loadChart(reset=true) {
  const request = ++chartRequest;
  $('chart-data-button').disabled = true;
  if (reset) {
    candles = [];
    chart.setData([], true);
    $('chart-empty').textContent = 'Loading candles…';
  }
  try {
    const query = new URLSearchParams({interval:$('chart-interval').value, session:$('chart-session').value, range});
    const data = await api('/api/candles?' + query);
    if (request !== chartRequest) return;
    candles = data.candles;
    chart.setData(candles, reset);
    $('chart-warning').hidden = !data.warning;
    $('chart-warning').textContent = data.warning;
    $('chart-subtitle').textContent = `${ticker()} · ${candles.length.toLocaleString()} candles · adjusted history`;
    $('chart-asof').textContent = `End-labelled candles · ${state?.settings.timezone==='Asia/Singapore'?'Singapore':'New York'} time`;
    lastChartRefresh = Date.now();
  } catch (error) {
    if (request === chartRequest) {toast(error.message); if (reset) $('chart-empty').textContent = 'Unable to load candles. Try again.';}
  } finally {
    if (request === chartRequest) $('chart-data-button').disabled = !state || !visibleChartRows().length;
  }
}
document.querySelectorAll('[data-range]').forEach(button=>button.addEventListener('click',()=>{range=button.dataset.range;document.querySelectorAll('[data-range]').forEach(b=>b.classList.toggle('active',b===button));loadChart();}));
$('chart-interval').addEventListener('change',()=>{loadChart();saveChartPreference('chart_interval',$('chart-interval').value);});
$('chart-session').addEventListener('change',()=>loadChart());
$('chart-style').addEventListener('change',()=>{chart.style=$('chart-style').value;chart.draw();saveChartPreference('chart_style',chart.style);});
async function saveChartPreference(key,value){try{await api('/api/settings',{[key]:value});}catch(e){toast(e.message);}}
$('reset-chart').addEventListener('click',()=>chart.reset());
function visibleChartRows() {
  // Drawing can be skipped while Overview is hidden; do not use its cached visible array.
  return chart.rows.slice(chart.start,chart.start+chart.count);
}
$('chart-data-button').addEventListener('click',()=>{
  if(!state || $('chart-data-button').disabled)return;
  const visible=visibleChartRows();
  if(!visible.length)return;
  // Use the current zoom/pan window, never unrelated candles from the full dataset.
  const rows=visible.slice(-100).reverse();
  modal('Visible candle data',`<p class="subtle-note">${rows.length} of ${visible.length.toLocaleString()} visible candles, newest first${visible.length>100?' (limited to the latest 100 in view)':''}. End-labelled timestamps; ${esc(state.settings.timezone)}.</p><table><thead><tr><th>Time</th><th>Open</th><th>High</th><th>Low</th><th>Close</th><th>Volume</th></tr></thead><tbody>${rows.map(b=>`<tr><td>${esc(dateTime(b.time))}</td><td>${b.open.toFixed(2)}</td><td>${b.high.toFixed(2)}</td><td>${b.low.toFixed(2)}</td><td>${b.close.toFixed(2)}</td><td>${Math.round(b.volume).toLocaleString()}</td></tr>`).join('')}</tbody></table>`);
});
$('connect-button').addEventListener('click',async()=>{try{await api('/api/feed/connect',{});toast('Connecting to OpenD');refresh();}catch(e){toast(e.message);}});
$('feed-details-button').addEventListener('click',()=>{if(!state)return toast('Market data is still loading');const f=state.market.feed;modal('Market data connection',`<p><strong>${esc(f.state)}</strong> · ${esc(f.host)}:${f.port}</p><p>${esc(f.message)}</p><p>Exchange state: ${esc(f.market_state)}</p><p>Latest feed contact: ${f.age_seconds==null?'none':Math.round(f.age_seconds)+' seconds ago'}</p><p>Active subscriptions: ${esc(f.subscriptions.join(', ')||'none')}</p><p>${esc(f.errors.join('\n'))}</p><p class="subtle-note">Freshness reflects provider timestamps. Quotes and individual trades can have different coverage. Saved history remains visible when the feed is disconnected.</p>`);});

document.querySelectorAll('.run-research').forEach(b=>b.addEventListener('click',async()=>{try{const epoch=stockEpoch;const result=await api('/api/research/run',{});if(epoch!==stockEpoch)return;selectedReport=result.id;toast('Research started. You can keep using the dashboard.');refresh();}catch(e){toast(e.message);}}));
function selectReport(id) {
  selectedReport=Number(id);
  renderReports();
  // Explicit navigation moves the reader; background polling never takes focus.
  const heading=$('report-title');
  if(heading){heading.scrollIntoView({block:'start'});heading.focus({preventScroll:true});}
}
function renderResearchProgress() {
  const r=state.research, busy=researchBusy(), cancelling=r.state==='cancelling';
  const name={'US.MU':'Micron','US.SNDK':'SanDisk'}[r.symbol]||'stock';
  $('research-start').hidden=busy;
  $('research-progress').hidden=!busy;
  $('cancel-research').hidden=!busy;
  $('cancel-research').disabled=cancelling || cancellingRunId===r.run_id || !r.run_id;
  $('cancel-research').textContent=cancelling?'Cancelling…':'Cancel research';
  $('cancel-research').setAttribute('aria-label',`Cancel ${name} research`);
  $('research-progress-toggle').textContent=`${cancelling?'Cancelling':'Researching'} ${name}…`;
  $('research-progress-heading').textContent=`${name} · ${r.kind==='explanation'?'Beginner explanation':'New research'}`;
  $('research-progress-stage').textContent=`${r.stage||'Working'} — ${r.message||'Waiting for an activity update.'}`;
  const now=state.server_time||Date.now()/1000;
  const duration=seconds=>`${Math.floor(seconds/60)}m ${Math.floor(seconds%60)}s`;
  const elapsed=Math.max(0,now-(r.last_started||now));
  const quiet=r.last_activity?Math.max(0,now-r.last_activity):null;
  const rows=[['Started',r.last_started?dateTime(r.last_started)+' '+(state.settings.timezone==='Asia/Singapore'?'SGT':'ET'):'Not recorded'],['Elapsed',duration(elapsed)],['Time limit',`${Math.round((r.timeout_seconds||600)/60)} minutes`],['Last reported activity',quiet===null?'Waiting for activity':duration(quiet)+' ago'],['Completed source actions',r.search_count||0]];
  if(r.latest_action)rows.push(['Latest action',r.latest_action]);
  if(r.latest_query)rows.push(['Latest search',r.latest_query]);
  if(r.latest_url)rows.push(['Latest source',r.latest_url]);
  $('research-progress-details').innerHTML=rows.map(([label,value])=>`<dt>${esc(label)}</dt><dd>${esc(value)}</dd>`).join('');
  $('research-progress-note').textContent=cancelling?'Waiting for the process to stop. Saved reports will be kept.':quiet!==null&&quiet>60?'No activity update for over a minute. The process may still be working; this alone does not prove it is stuck. You can cancel this run.':'Activity reflects reported events, not a completion percentage. Search and writing may overlap. Cancel stops this run; automatic research remains enabled if configured.';
  if(!busy)setProgressOpen(false);
}
function setProgressOpen(open) {
  $('research-progress-panel').hidden=!open;
  $('research-progress-toggle').setAttribute('aria-expanded',String(open));
}
$('research-progress').addEventListener('mouseenter',()=>setProgressOpen(true));
$('research-progress').addEventListener('mouseleave',()=>{if(!$('research-progress').contains(document.activeElement))setProgressOpen(false);});
$('research-progress-toggle').addEventListener('focus',()=>setProgressOpen(true));
$('research-progress-toggle').addEventListener('click',()=>setProgressOpen(true));
$('research-progress').addEventListener('focusout',e=>{if(!$('research-progress').contains(e.relatedTarget))setProgressOpen(false);});
$('research-progress').addEventListener('keydown',e=>{if(e.key==='Escape')setProgressOpen(false);});
document.addEventListener('click',e=>{if(!$('research-progress').contains(e.target))setProgressOpen(false);});
$('cancel-research').addEventListener('click',async()=>{
  const run=state?.research;
  if(!run?.run_id || !researchBusy() || cancellingRunId)return;
  cancellingRunId=run.run_id;
  const button=$('cancel-research');button.disabled=true;button.textContent='Cancelling…';
  try{
    const result=await api('/api/research/cancel',{run_id:run.run_id},run.symbol);
    toast(result.message);await refresh();
  }catch(error){toast(error.message);}
  finally{cancellingRunId=null;if(state)renderResearchProgress();}
});

function renderReportHistory(reports, report) {
  const key=JSON.stringify([symbol,selectedReport,state.settings.timezone,reports.map(r=>[r.id,r.status,r.body.headline])]);
  if(key===reportHistoryKey)return; // Polling must not replace a focused history control.
  reportHistoryKey=key;
  const label=r=>r.body.headline?.replaceAll('**','')||({running:'Research in progress',failed:'Report could not complete',cancelled:'Research cancelled'}[r.status])||'Saved report';
  $('report-list').innerHTML=reports.length?`<label class="mobile-report-picker">Report history<select id="report-picker">${reports.map(r=>`<option value="${r.id}" ${r.id===report?.id?'selected':''}>${esc(dateTime(r.created))} · ${esc(label(r))} · ${esc(r.status)}</option>`).join('')}</select></label><div class="desktop-report-history"><h2>Report history</h2>${reports.map(r=>`<button class="report-button ${r.id===selectedReport?'active':''}" aria-pressed="${r.id===selectedReport}" data-report="${r.id}"><small>${esc(dateTime(r.created))} · ${esc(r.status)}</small>${esc(label(r))}</button>`).join('')}</div>`:'<div class="empty-panel">Your research history will appear here.</div>';
  document.querySelectorAll('[data-report]').forEach(b=>b.onclick=()=>selectReport(b.dataset.report));
  if($('report-picker'))$('report-picker').onchange=e=>selectReport(e.target.value);
}

function bindSourceFilters(key) {
  const query=$('source-search'), filter=$('source-filter');
  const saved=sourceFilters.get(key)||{query:'',usage:'all'};
  query.value=saved.query;filter.value=saved.usage;
  const update=()=>{
    const term=query.value.trim().toLocaleLowerCase();let count=0;
    const items=document.querySelectorAll('#report-body .news-item[data-source-key]');
    items.forEach(item=>{
      item.hidden=!(filter.value==='all'||item.dataset.usage===filter.value)||!item.textContent.toLocaleLowerCase().includes(term);
      if(!item.hidden)count++;
    });
    $('source-results').textContent=count?`${count} of ${items.length} sources`:'No matching sources. Change the search or filter.';
    sourceFilters.set(key,{query:query.value,usage:filter.value});
  };
  query.oninput=update;filter.onchange=update;update();
}

function renderReports(){if(!state)return;const reports=state.reports;const existingBeginner=$('generate-beginner');if(existingBeginner)existingBeginner.disabled=researchBusy();const report=reports.find(r=>r.id===selectedReport)||reports[0];if(report)selectedReport=report.id;
  renderResearchProgress();
  const status=$('research-status'),running=researchBusy(),failed=state.research.state==='error';
  status.className='banner '+(failed?'':'neutral');
  const viewing=report?`${ticker()} · Viewing report started ${dateTime(report.created)} ${state.settings.timezone==='Asia/Singapore'?'SGT':'ET'} · ${report.status}${report.id===reports[0]?.id?' · Latest available':' · Earlier report'}`:'No saved reports yet';
  status.textContent=viewing+(running||failed?'\n'+(state.research.symbol?state.research.symbol.slice(3)+' · ':'')+state.research.message:'');
  const latest=$('latest-report');latest.hidden=!report||report.id===reports[0]?.id;
  latest.onclick=()=>selectReport(reports[0].id);
  renderReportHistory(reports,report);
  if(!report){reportRenderKey='';$('report-body').innerHTML='<div class="empty-panel"><h2 id="report-title" tabindex="-1">Put today in perspective.</h2><p>Start a report to bring the selected stock’s data together with current news, ranked drivers, and separate intraday and holding outlooks.</p><p>Sources and observation times stay attached to every saved report.</p></div>';return;}
  if(report.status!=='complete'){reportRenderKey='';$('report-body').innerHTML=`<div class="empty-panel"><h2 id="report-title" tabindex="-1">${report.status==='running'?'Researching the current picture…':report.status==='cancelled'?'Research cancelled':'This report needs attention'}</h2><p>${esc(report.status==='cancelled'?'This run was stopped. Select a completed report from history or start a new analysis.':report.error||'Checking current sources and preparing your analysis. The completed report will appear here automatically.')}</p></div>`;return;}
  const renderKey=JSON.stringify([symbol, report.id, report.body, reportMode, state.settings.timezone]);
  if(renderKey===reportRenderKey)return;
  reportRenderKey=renderKey;
  $('report-body').innerHTML=reportMarkup(report,reportMode,dateTime(report.created),state.settings.timezone);
  const expansionKey=symbol+':'+report.id;
  const expanded=sourceExpansion.get(expansionKey)||new Set();
  document.querySelectorAll('#report-body [data-source-key]').forEach(el=>{
    el.open=expanded.has(el.dataset.sourceKey);
    el.addEventListener('toggle',()=>{if(el.open)expanded.add(el.dataset.sourceKey);else expanded.delete(el.dataset.sourceKey);sourceExpansion.set(expansionKey,expanded);});
  });
  document.querySelectorAll('[data-mode]').forEach(button=>button.onclick=()=>{reportMode=button.dataset.mode;savePreference('stork-report-mode',reportMode);renderReports();});
  bindSourceFilters(expansionKey);
  document.querySelectorAll('[data-report-section]').forEach(button=>button.onclick=()=>{
    const section=$('report-'+button.dataset.reportSection);
    if(section.tagName==='DETAILS')section.open=true;
    section.scrollIntoView({block:'start'});
    section.focus({preventScroll:true});
    clearTimeout(sectionHighlightTimers.get(section));
    section.classList.remove('section-highlight');
    // Restart the animation even when the same destination is selected again.
    void section.offsetWidth;
    section.classList.add('section-highlight');
    sectionHighlightTimers.set(section,setTimeout(()=>{
      section.classList.remove('section-highlight');
      sectionHighlightTimers.delete(section);
    },1500));
  });
  document.querySelectorAll('[data-expand-sources]').forEach(button=>button.onclick=()=>document.querySelectorAll('#report-body .news-item[data-source-key]').forEach(el=>{if(button.dataset.expandSources==='no'||!el.hidden)el.open=button.dataset.expandSources==='yes';}));
  const beginnerButton=$('generate-beginner');
  if(beginnerButton)beginnerButton.disabled=researchBusy();
  if(beginnerButton)beginnerButton.onclick=async()=>{beginnerButton.disabled=true;try{await api('/api/research/beginner',{id:report.id});toast('Preparing explanation from saved evidence');refresh();}catch(e){toast(e.message);}finally{beginnerButton.disabled=false;}};
}
$('load-archive').addEventListener('click',async()=>{try{const epoch=stockEpoch;const data=await api('/api/news');if(epoch!==stockEpoch)return;$('archive-news').innerHTML=data.items.slice(0,50).map(n=>`<div class="news-item"><a href="${esc(safeUrl(n.url))}" target="_blank" rel="noopener noreferrer">${esc(n.title)} ↗</a><small>${esc(n.source)} · ${esc(n.date)} · archived, date precision only</small></div>`).join('')||'<p class="empty-panel">No saved news exports.</p>';}catch(e){toast(e.message);}});

function fillSettings(){const s=state.settings;$('schedule-fields').innerHTML=['premarket','regular','afterhours','overnight'].map(session=>`<label>${sessionLabel[session]}<select name="${session}_interval">${[5,15,30,60,120,240].map(n=>`<option value="${n}" ${s[session+'_interval']===n?'selected':''}>Every ${n} minutes</option>`).join('')}</select></label>`).join('');$('weight-fields').innerHTML=Object.entries(labels).map(([key,label])=>`<label>${label}<input type="number" name="weight_${key}" min="0" max="100" step="1" value="${s.weights[key]}" aria-label="${label} weight"></label>`).join('');for(const [key,value]of Object.entries(s)){const input=$('settings-form').elements[key];if(!input)continue;if(input.type==='checkbox')input.checked=value;else input.value=value??'';}$('weight-fields').oninput=()=>{const total=Object.keys(labels).reduce((sum,key)=>sum+Number($('settings-form').elements['weight_'+key].value),0);$('weight-total').textContent=`Total ${total}%`;$('weight-total').classList.toggle('down',total!==100);};}
$('settings-form').addEventListener('submit',async e=>{e.preventDefault();const form=new FormData(e.target);const patch={};['analysis_enabled','overnight_enabled','notify_reports'].forEach(k=>patch[k]=form.has(k));['premarket_interval','regular_interval','afterhours_interval','overnight_interval'].forEach(k=>patch[k]=Number(form.get(k)));['alert_above','alert_below'].forEach(k=>patch[k]=form.get(k)?Number(form.get(k)):null);patch.timezone=form.get('timezone');patch.weights=Object.fromEntries(Object.keys(labels).map(k=>[k,Number(form.get('weight_'+k))]));try{await api('/api/settings',patch);$('settings-feedback').textContent='Preferences saved';await refresh();chart.draw();}catch(error){$('settings-feedback').textContent=error.message;toast(error.message);}});
$('check-auth').addEventListener('click',async()=>{const button=$('check-auth');button.disabled=true;$('auth-result').textContent='Checking…';try{const result=await api('/api/auth-status');$('auth-result').textContent=result.message;}catch(e){$('auth-result').textContent=e.message;}finally{button.disabled=false;}});
$('enable-notifications').addEventListener('click',async()=>{if(!('Notification'in window))return toast('This browser does not support notifications');const permission=await Notification.requestPermission();toast(permission==='granted'?'Browser notifications enabled':'Browser notifications were not enabled');});
function renderEvents(){const events=state.events;if(!initialized)return;const newest=events[0]?.id||0;if(lastEvent!==null){for(const event of events.filter(e=>e.id>lastEvent)){if('Notification'in window&&Notification.permission==='granted'&&(event.kind==='price'||(event.kind==='research'&&state.settings.notify_reports)))new Notification(event.title,{body:event.body,tag:`stork-${event.id}`});}}lastEvent=newest;$('alert-count').hidden=!events.length;$('alert-count').textContent=Math.min(events.length,99);}
$('alerts-button').addEventListener('click',()=>modal('Your alerts',state?.events.length?state.events.map(e=>`<div class="news-item"><strong>${esc(e.title)}</strong><p>${esc(e.body)}</p><small>${esc(dateTime(e.created))}</small></div>`).join(''):'<div class="empty-panel">No alerts yet. Set price thresholds in Preferences.</div>'));

function renderPositions(){if(!state)return;const expanded=new Set([...document.querySelectorAll('#positions details[open]')].map(el=>el.dataset.position));const m=state.market;$('positions').innerHTML=state.positions.length?state.positions.map(p=>{const value=p.state==='closed'?p.exit:m.price;const profit=value==null?null:(value-p.entry)*p.quantity;return `<article class="card position-card"><div class="position-top"><h2>${ticker()} <span class="muted">· ${p.quantity} shares</span></h2><span class="status-pill">${p.state==='closed'?'Closed':p.intent==='intraday'?'Intraday trade':'Holding position'}</span></div><div class="position-numbers"><div><small>Entry</small>${money(p.entry)}</div><div><small>${p.state==='closed'?'Exit':'Last observed'}</small>${money(value)}</div><div class="${profit==null?'muted':profit>=0?'up':'down'}"><small>${p.state==='closed'?'Realized':'Unrealized'} P/L</small>${money(profit)}</div></div><p>${esc(p.reason)}</p>${p.state==='open'?`<p class="subtle-note">Valued at ${esc(dateTime(m.asof))}. Fees and taxes excluded.</p><div class="position-actions"><button class="text-button" data-intent="${p.id}">Review intention →</button><button class="text-button" data-close="${p.id}">Record closing trade →</button></div>`:''}<details data-position="${p.id}" ${expanded.has(String(p.id))?'open':''}><summary>Original plan & changes (${p.history.length})</summary><ul>${p.history.map(h=>`<li>${esc(dateTime(h.time))} · ${esc(h.intent||h.action)} · ${esc(h.reason||money(h.exit))}</li>`).join('')}</ul></details></article>`;}).join(''):'<div class="card empty-panel"><div class="empty-symbol">▤</div><h2>Every position starts with a reason.</h2><p>Record your entry and intended holding period.<br>When your plan changes, keep the reason alongside it.</p></div>';document.querySelectorAll('[data-intent]').forEach(b=>b.onclick=()=>editPosition(Number(b.dataset.intent),false));document.querySelectorAll('[data-close]').forEach(b=>b.onclick=()=>editPosition(Number(b.dataset.close),true));}
$('add-position').addEventListener('click',()=>{const positionSymbol=symbol;modal('Record a '+ticker()+' position','<form id="position-form"><div class="form-grid"><label>Number of shares<input name="quantity" type="number" min="0.000001" step="any" required></label><label>Entry price ($)<input name="entry" type="number" min="0.01" step="0.01" required></label></div><label>Original intention<select name="intent"><option value="intraday">Intraday trade</option><option value="holding">Holding shares</option></select></label><label>Why did you enter?<textarea name="reason" maxlength="1000" required placeholder="Your reason, intended timeframe, and what would make you reconsider"></textarea></label><p class="form-error" id="position-error"></p><button class="button primary">Save position</button></form>');$('position-form').onsubmit=async e=>{e.preventDefault();const data=Object.fromEntries(new FormData(e.target));try{await api('/api/positions',data,positionSymbol);$('modal').close();toast('Position recorded');refresh();}catch(error){$('position-error').textContent=error.message;}};});
function editPosition(id,close){const positionSymbol=symbol;const p=state.positions.find(p=>p.id===id);modal(close?'Record a closing trade':'Review your intention',`<form id="position-form">${close?'<label>Exit price ($)<input type="number" min="0.01" step="0.01" name="exit" required></label><p class="subtle-note">This records a completed trade in your journal. It does not send an order.</p>':`<label>New intention<select name="intent"><option value="intraday" ${p.intent==='intraday'?'selected':''}>Intraday trade</option><option value="holding" ${p.intent==='holding'?'selected':''}>Holding shares</option></select></label><label>What changed?<textarea name="reason" required maxlength="1000" placeholder="Record the evidence behind your revised plan"></textarea></label><p class="subtle-note">Your original intention remains in the journal.</p>`}<p class="form-error" id="position-error"></p><button class="button primary">${close?'Record close':'Save review'}</button></form>`);$('position-form').onsubmit=async e=>{e.preventDefault();const data=Object.fromEntries(new FormData(e.target));if(close)data.action='close';try{await api(`/api/positions/${id}`,data,positionSymbol);$('modal').close();toast('Journal updated');refresh();}catch(error){$('position-error').textContent=error.message;}};}
function updateClock(){$('clock').textContent=new Intl.DateTimeFormat('en-US',{timeZone:state?.settings.timezone||'America/New_York',weekday:'short',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date())+' '+(state?.settings.timezone==='Asia/Singapore'?'SGT':'ET');}
updateClock();setInterval(updateClock,10000);refresh();setInterval(refresh,4000);
