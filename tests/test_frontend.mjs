import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import test from 'node:test';
import {PriceChart} from '../web/chart.js';
import {reportMarkup, narrative, roundFinancialText, sourceLedger, presentationText, reportTime} from '../web/reports.js';

const appSource = await readFile(new URL('../web/app.js', import.meta.url), 'utf8');
const themeSource = await readFile(new URL('../web/theme.js', import.meta.url), 'utf8');

function element() {
  const listeners = {};
  return {
    dataset:{}, style:{}, hidden:false, open:false, listeners,
    classList:{toggle() {}},
    addEventListener(name, callback) {listeners[name] = callback;},
    setAttribute() {},
    focus() {},
    scrollIntoView() {},
    showModal() {this.open = true;},
    close() {this.open = false;},
  };
}

function appHarness() {
  const elements = new Map();
  const notifications = [];
  const document = {
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, element());
      return elements.get(id);
    },
    querySelectorAll() {return [];},
    addEventListener() {},
  };
  const context = vm.createContext({
    document, location:{hash:''}, requestAnimationFrame() {},
    window:{addEventListener() {}},
    PriceChart:class {rows=[];start=0;count=0;draw() {} setData(rows) {this.rows=rows;this.start=0;this.count=rows.length;}},
    Notification:class {static permission='granted'; constructor(title) {notifications.push(title);}},
    Intl, Date, URL, URLSearchParams, setTimeout, clearTimeout, reportMarkup, narrative, roundFinancialText,
  });
  context.window.Notification = context.Notification;
  // Suppress startup I/O; exercise the shipped handlers with deterministic browser inputs.
  const source = appSource.replace("import {PriceChart} from './chart.js';", '')
    .replace("import {reportMarkup, narrative, roundFinancialText} from './reports.js';", '')
    .replace('updateClock();setInterval(updateClock,10000);refresh();setInterval(refresh,4000);', '');
  vm.runInContext(source, context);
  return {context, elements, notifications};
}

test('the first new alert after an empty initial history is delivered exactly once', () => {
  const {context, notifications} = appHarness();
  vm.runInContext("initialized=true;state={events:[],settings:{notify_reports:true}};renderEvents();", context);
  vm.runInContext("state.events=[{id:1,kind:'price',title:'Crossed threshold',body:''}];renderEvents();renderEvents();", context);
  assert.deepEqual(notifications, ['Crossed threshold']);
});

test('a rejected access token reopens the existing closed sign-in dialog', () => {
  const {context, elements} = appHarness();
  elements.set('access-form', element());
  vm.runInContext('showLogin()', context);
  assert.equal(elements.get('modal').open, true);
});

test('chart data is safe before loading and shows only the current chart window', () => {
  const {context,elements}=appHarness();
  const button=elements.get('chart-data-button');
  assert.equal(button.disabled,true);
  assert.doesNotThrow(()=>button.listeners.click());
  context.windowBars=[{time:1791459000,open:101,high:102,low:100,close:101.5,volume:42}];
  vm.runInContext("state={settings:{timezone:'Asia/Singapore'}};candles=[{open:999}];chart.rows=[{open:999},...windowBars,{open:998}];chart.start=1;chart.count=1;chart.visible=[{open:997}];",context);
  button.disabled=false;
  button.listeners.click();
  const html=elements.get('modal-content').innerHTML;
  assert.match(html,/1 of 1 visible candles/);
  assert.match(html,/<td>101.50<\/td>/);
  assert.ok(!html.includes('999'));
  assert.ok(!html.includes('997'));
  vm.runInContext('chart.rows=[]',context);
  elements.get('modal').open=false;
  button.listeners.click();
  assert.equal(elements.get('modal').open,false);
});

test('failed or empty chart loads keep the data control disabled', async () => {
  const {context,elements}=appHarness();
  vm.runInContext("state={settings:{timezone:'Asia/Singapore'}};api=async()=>{throw new Error('No candles')};toast=()=>{};",context);
  await vm.runInContext('loadChart(true)',context);
  assert.equal(elements.get('chart-data-button').disabled,true);
  vm.runInContext('api=async()=>({candles:[],warning:null})',context);
  await vm.runInContext('loadChart(true)',context);
  assert.equal(elements.get('chart-data-button').disabled,true);
  context.windowBars=[{time:1791459000,open:1,high:2,low:1,close:2,volume:3}];
  vm.runInContext('api=async()=>({candles:windowBars,warning:null})',context);
  await vm.runInContext('loadChart(true)',context);
  assert.equal(elements.get('chart-data-button').disabled,false);
});

test('missing position value is neutral while observed gains and losses retain their colours', () => {
  const {context,elements}=appHarness();
  vm.runInContext("state={settings:{timezone:'Asia/Singapore'},market:{price:null,asof:null},positions:[{id:1,state:'open',quantity:2,entry:100,intent:'holding',reason:'Test',history:[]}]};renderPositions();",context);
  assert.match(elements.get('positions').innerHTML,/<div class="muted"><small>Unrealized P\/L<\/small>—/);
  vm.runInContext('state.market.price=101;renderPositions()',context);
  assert.match(elements.get('positions').innerHTML,/<div class="up"><small>Unrealized P\/L<\/small>\$2.00/);
  vm.runInContext('state.market.price=99;renderPositions()',context);
  assert.match(elements.get('positions').innerHTML,/<div class="down"><small>Unrealized P\/L<\/small>-\$2.00/);
});

test('research buttons identify the active stock and connection status never claims a live feed', () => {
  const {context,elements}=appHarness();
  const buttons=[element(),element()];
  context.document.querySelectorAll=selector=>selector==='.run-research'?buttons:[];
  vm.runInContext("state={settings:{timezone:'Asia/Singapore',analysis_enabled:true},research:{state:'running',symbol:'US.SNDK'},reports:[],market:{price:null,change_pct:null,from_open_pct:null,weekly:[],session:'premarket',live:false,feed:{state:'disconnected',quotes:{}}}};renderMarket();",context);
  assert.equal(elements.get('context-feed-status').textContent,'Disconnected');
  for(const button of buttons){assert.equal(button.textContent,'Researching SanDisk…');assert.equal(button.disabled,true);}
  vm.runInContext("state.research.symbol='US.MU';state.market.feed.state='connected';renderMarket();",context);
  assert.equal(elements.get('context-feed-status').textContent,'Connected');
  assert.equal(buttons[0].textContent,'Researching Micron…');
  vm.runInContext("state.research.state='idle';renderMarket();",context);
  assert.equal(buttons[0].textContent,'Analyze now');
  assert.equal(buttons[0].disabled,false);
});

function chartHarness() {
  const colors = new Set();
  const ctx = new Proxy({}, {
    get(object, key) {
      if (key === 'createLinearGradient') return () => ({addColorStop() {}});
      return object[key] ?? (() => {});
    },
    set(object, key, value) {if (key === 'fillStyle') colors.add(value); object[key]=value; return true;},
  });
  globalThis.window = {devicePixelRatio:1};
  globalThis.document = {documentElement:{}};
  globalThis.ResizeObserver = class {observe() {}};
  globalThis.getComputedStyle = () => ({getPropertyValue(name) {return {'--green':'#62d5b6','--chart-session':'#182631'}[name] || '';}});
  const canvas = {...element(),getContext:()=>ctx,getBoundingClientRect:()=>({width:900,height:320})};
  return {chart:new PriceChart(canvas, element(), element()),colors};
}

function bars(count) {
  return Array.from({length:count}, (_, i) => ({time:1791400000+i*60,low:99,high:102,open:100,close:101,volume:10,date:'2026-10-07',session:'premarket'}));
}

test('a fitted chart includes new candles without silently dropping the beginning', () => {
  const {chart} = chartHarness();
  chart.setData(bars(10), true);
  chart.setData(bars(12));
  assert.equal(chart.start, 0);
  assert.equal(chart.count, 12);
});

test('a panned chart remains visible when refreshed history becomes shorter', () => {
  const {chart} = chartHarness();
  chart.setData(bars(100), true);
  chart.start=70; chart.count=10;
  chart.setData(bars(20));
  assert.equal(chart.start, 10);
  assert.equal(chart.visible.length, 10);
});

test('large chart histories do not hit the JavaScript argument limit', () => {
  const {chart, colors} = chartHarness();
  chart.setData(bars(150000), true);
  assert.equal(chart.visible.length, 150000);
  assert.ok(colors.has('#182631'));
  assert.ok(colors.has('#62d5b6'));
});

test('theme defaults to dark and remains usable when browser storage is denied', () => {
  const button=element(), documentElement={dataset:{}}, listeners={};
  const context=vm.createContext({
    document:{documentElement,getElementById:()=>button,addEventListener:(name,fn)=>listeners[name]=fn},
    window:{dispatchEvent() {},addEventListener() {}},Event:class {},
    localStorage:{getItem() {throw new Error('denied');},setItem() {throw new Error('denied');}},
  });
  vm.runInContext(themeSource,context);
  assert.equal(documentElement.dataset.theme,'dark');
  listeners.DOMContentLoaded();
  button.listeners.click();
  assert.equal(documentElement.dataset.theme,'light');
});


test('financial display rounds without altering timestamps, URLs or counts', () => {
  assert.equal(roundFinancialText('$1,234.567 +1.238% 42 shares'), '$1,234.57 +1.24% 42 shares');
  assert.equal(roundFinancialText('2026-10-08T10:12:00.123Z https://example.com/1.234'), '2026-10-08T10:12:00.123Z https://example.com/1.234');
  assert.equal(roundFinancialText('−0.001%'), '−<0.01%');
  assert.equal(roundFinancialText('$600 million and 75%'), '$600.00 million and 75.00%');
  assert.equal(roundFinancialText('Change -1.2408740147244801. Price $123.456.'), 'Change −1.24. Price $123.46.');
  assert.equal(roundFinancialText('09:45:01.860 UTC, 5.421 seconds'), '09:45:01.860 UTC, 5.42 seconds');
});

test('report emphasis escapes untrusted markup', () => {
  const html=narrative('**Watch this**\n- Return +1.236%\n<img src=x onerror=alert(1)>');
  assert.match(html, /<strong>Watch this<\/strong>/);
  assert.match(html, /<li>Return \+1.24%<\/li>/);
  assert.ok(!html.includes('<img'));
  assert.equal(narrative('change_pct -1.2345.'), '<p>change (%) −1.23.</p>');
});

test('source ledger includes all saved sources plus omitted factor links', () => {
  const sources=Array.from({length:40},(_,i)=>({url:'https://example.com/'+i,title:String(i)}));
  const ledger=sourceLedger({sources,news:[{url:'https://example.com/1'}],factors:[{source_url:'https://example.com/factor',name:'Driver',evidence:'Test'}]});
  assert.equal(ledger.length,41);
  assert.equal(ledger.at(-1).access,'not_recorded');
});

test('legacy report exposes beginner generation and labels incomplete source history', () => {
  const body={headline:'Test',summary:'Summary',intraday:'Test',holding:'Test',changes:'Test',limitations:'Test',factors:[],news:[],scenarios:[]};
  const html=reportMarkup({body},'beginner','Today');
  assert.match(html,/Create beginner explanation/);
  assert.match(html,/Legacy report/);
});

test('report display groups counts and converts only unambiguous timestamps', () => {
  const input='324819 shares, previous 220548. $1066.25 on 2026-10-08T11:30:03.627085+00:00; 2026-10-08; 11:31 UTC; https://example.com/324819';
  const text=presentationText(input,'Asia/Singapore');
  assert.match(text,/324,819 shares, previous 220,548\./);
  assert.match(text,/\$1,066.25/);
  assert.match(text,/07:30 pm SGT/);
  assert.match(text,/2026-10-08; 11:31 UTC; https:\/\/example.com\/324819/);
  assert.equal(reportTime(null),'Unknown');
  assert.equal(reportTime('not a date'),'Unknown');
  assert.match(reportTime('2026-10-08T11:30:00Z','America/New_York'),/07:30 am ET/);
});

function readableReport() {
  return {id:1,created:1791459003,body:{headline:'Test',summary:'**Uncertain cause.**',intraday:'Today',holding:'Weeks',changes:'Changed',limitations:'Missing current oil quote.\n**Technical details**\nProvider timestamp is an update.',factors:[],news:[],scenarios:[],snapshot:{now_utc:'2026-10-08T11:30:03Z',market:{price:1066.25,reference:1088,change_pct:-2,session:'premarket',live:true,asof:1791459000,source:'Test feed',feed:{quotes:{'US.SMH':{change_pct:-1.58,time:1791458990,session:'premarket'}}}}}}};
}

test('saved snapshot summary preserves values and rejects stale or mismatched comparisons', () => {
  const report=readableReport(), before=JSON.stringify(report);
  const html=reportMarkup(report,'detailed','Today','Asia/Singapore');
  assert.match(html,/\$1,066.25/);
  assert.match(html,/0.42 pp behind/);
  assert.match(html,/Saved observations, not live prices/);
  assert.equal(JSON.stringify(report),before);
  report.body.snapshot.market.feed.quotes['US.SMH'].session='regular';
  assert.match(reportMarkup(report,'detailed','Today'),/Not comparable/);
  report.body.snapshot.market.feed.quotes['US.SMH'].session='premarket';
  report.body.snapshot.market.feed.quotes['US.SMH'].time-=600;
  assert.match(reportMarkup(report,'detailed','Today'),/Not comparable/);
});

test('tiny benchmark differences and already numbered drivers are not misleading', () => {
  const report=readableReport();
  report.body.snapshot.market.feed.quotes['US.SMH'].change_pct=-1.999;
  report.body.factors=[{name:'1. Company news — priority 25',direction:'mixed',evidence:'Uncertain',source_url:''}];
  const html=reportMarkup(report,'detailed','Today');
  assert.match(html,/Less than 0.01 pp behind/);
  assert.match(html,/1\.<\/span> Company news — research attention weight 25/);
});

test('material caveats remain visible and all source roles remain browsable', () => {
  const report=readableReport();
  report.body.sources=['used','background','excluded'].map((usage,i)=>({title:'<script>bad</script>',url:'javascript:alert(1)',source:'Publisher',usage,access:'headline_only',summary:'<img>',relevance:'Context',published:'Unknown'}));
  report.body.sources.forEach((source,i)=>source.url+='?'+i);
  const html=reportMarkup(report,'beginner','Today');
  assert.ok(html.indexOf('Missing current oil quote.')<html.indexOf('<details class="technical-notes"'));
  assert.match(html,/Used \(1\)/);assert.match(html,/Background \(1\)/);assert.match(html,/Excluded \(1\)/);
  assert.equal((html.match(/data-usage=/g)||[]).length,3);
  assert.ok(!html.includes('<script>'));assert.ok(!html.includes('href="javascript:'));
  assert.match(html,/Headline only/);
});

test('older-report status and latest action distinguish the viewed report without poll focus churn', () => {
  const {context,elements}=appHarness();
  const report=readableReport();
  context.fixture=report;
  vm.runInContext("state={settings:{timezone:'Asia/Singapore'},research:{state:'idle',message:'Latest report saved'},reports:[{...fixture,id:2},fixture]};selectedReport=1;renderReports();",context);
  assert.match(elements.get('research-status').textContent,/Earlier report/);
  assert.equal(elements.get('latest-report').hidden,false);
  vm.runInContext("state.research={state:'running',symbol:'US.SNDK',message:'Researching'};renderReports();",context);
  assert.match(elements.get('research-status').textContent,/Earlier report\nSNDK · Researching/);
  // An unchanged poll must leave the existing history DOM (and keyboard focus) intact.
  elements.get('report-list').innerHTML='preserved';
  vm.runInContext('renderReports()',context);
  assert.equal(elements.get('report-list').innerHTML,'preserved');
  elements.get('latest-report').onclick();
  assert.match(elements.get('research-status').textContent,/Latest available/);
  assert.equal(elements.get('latest-report').hidden,true);
});

test('technical heading variants collapse only their paragraph and preserve later caveats', () => {
  for(const heading of ['**Technical details**\n','**Technical details:** ','**Technical details**: ','**Technical details:**\n\n']){
    const report=readableReport();
    report.body.limitations='Missing live prices.\n\n'+heading+'Provider timestamp mechanics.\n\nSmall sample; not a forecast.';
    const before=JSON.stringify(report);
    const html=reportMarkup(report,'detailed','Today');
    const notes=html.match(/<details class="technical-notes"[^>]*>[\s\S]*?<\/details>/)?.[0];
    assert.ok(notes);
    assert.match(notes,/Provider timestamp mechanics/);
    assert.ok(!notes.includes('Missing live prices'));
    assert.ok(!notes.includes('Small sample'));
    assert.ok(html.indexOf('Small sample')>html.indexOf(notes)+notes.length);
    assert.equal(JSON.stringify(report),before);
  }
  const report=readableReport();
  report.body.limitations='**Technical details:** Mechanics.\n**Important limitation:** Missing benchmark.';
  const html=reportMarkup(report,'detailed','Today');
  assert.ok(html.indexOf('Missing benchmark')>html.indexOf('</details>'));
});

test('search activity displays complete batches, legacy queries and unavailable actions safely', () => {
  const report=readableReport();
  report.body.search_audit=[
    {query:'First ...',action:{queries:['First full query','Second <query>'],query:'First full query'}},
    {query:'Short label',action:{query:'Third full query'}},
    {query:'Page label',action:{url:'https://example.com/article'}},
    {query:'Legacy query'},
    {action:{type:'other'}},
  ];
  const before=JSON.stringify(report);
  const html=reportMarkup(report,'detailed','Today');
  assert.match(html,/Recorded search activity \(6\)/);
  assert.match(html,/Second &lt;query&gt;/);
  assert.match(html,/Third full query/);
  assert.match(html,/https:\/\/example.com\/article/);
  assert.match(html,/Legacy query/);
  assert.match(html,/Search\/page action; details not exposed/);
  assert.equal((html.match(/First full query/g)||[]).length,1);
  assert.ok(!html.includes('First ...'));
  assert.equal(JSON.stringify(report),before);
});

test('explicit report selection moves to its heading while polling preserves focus and scroll', () => {
  const {context,elements}=appHarness();
  context.fixture={...readableReport(),status:'complete'};
  vm.runInContext("state={settings:{timezone:'Asia/Singapore'},research:{state:'idle'},reports:[{...fixture,id:2},fixture]};selectedReport=1;renderReports();",context);
  const calls=[];
  elements.set('report-title',{scrollIntoView(options){calls.push(['scroll',options.block]);},focus(options){calls.push(['focus',options.preventScroll]);}});
  elements.get('latest-report').onclick();
  assert.equal(vm.runInContext('selectedReport',context),2);
  assert.deepEqual(calls,[['scroll','start'],['focus',true]]);
  calls.length=0;
  vm.runInContext('renderReports();renderReports();',context);
  assert.deepEqual(calls,[]);
  elements.get('report-picker').onchange({target:{value:'1'}});
  assert.equal(vm.runInContext('selectedReport',context),1);
  assert.deepEqual(calls,[['scroll','start'],['focus',true]]);
  calls.length=0;
  vm.runInContext("state.reports[0].status='running';selectReport(2);",context);
  assert.match(elements.get('report-body').innerHTML,/<h2 id="report-title" tabindex="-1">Researching/);
  assert.deepEqual(calls,[['scroll','start'],['focus',true]]);
});

test('progress shows actual active stock, quiet time and escaped source activity', () => {
  const {context,elements}=appHarness();
  vm.runInContext("state={server_time:200,settings:{timezone:'Asia/Singapore'},research:{state:'running',busy:true,run_id:'run-1',symbol:'US.SNDK',last_started:100,last_activity:120,stage:'Researching sources',message:'Reviewing evidence',search_count:3,latest_query:'<script>bad</script>',latest_url:'https://example.com',kind:'research'}};renderResearchProgress();",context);
  assert.equal(elements.get('research-progress-toggle').textContent,'Researching SanDisk…');
  assert.match(elements.get('research-progress-details').innerHTML,/1m 40s/);
  assert.match(elements.get('research-progress-details').innerHTML,/&lt;script&gt;/);
  assert.match(elements.get('research-progress-note').textContent,/does not prove it is stuck/);
  assert.equal(elements.get('cancel-research').hidden,false);
  elements.get('research-progress-toggle').listeners.focus();
  assert.equal(elements.get('research-progress-panel').hidden,false);
  elements.get('research-progress').listeners.keydown({key:'Escape'});
  assert.equal(elements.get('research-progress-panel').hidden,true);
  vm.runInContext("state.research.state='cancelled';state.research.busy=false;renderResearchProgress();",context);
  assert.equal(elements.get('research-start').hidden,false);
  assert.equal(elements.get('cancel-research').hidden,true);
});

test('cancel targets the active run even when another stock is selected and stays disabled while stopping', async () => {
  const {context,elements}=appHarness();
  vm.runInContext("symbol='US.MU';state={settings:{timezone:'Asia/Singapore'},research:{state:'running',busy:true,run_id:'sndk-run',symbol:'US.SNDK'}};renderResearchProgress();",context);
  const calls=[];
  context.cancelCalls=calls;
  vm.runInContext("api=async(path,body,stock)=>{cancelCalls.push([path,body.run_id,stock]);state.research.state='cancelling';return {message:'Cancellation requested'};};refresh=async()=>{};toast=()=>{};",context);
  await elements.get('cancel-research').listeners.click();
  assert.equal(JSON.stringify(calls),JSON.stringify([['/api/research/cancel','sndk-run','US.SNDK']]));
  assert.equal(elements.get('cancel-research').disabled,true);
  assert.equal(elements.get('cancel-research').textContent,'Cancelling…');
  vm.runInContext("state.research.state='error';state.research.busy=false;renderResearchProgress();",context);
  assert.equal(elements.get('research-start').hidden,false);
});
