// Report presentation never changes the saved observations or calculation precision.
export const escapeText = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export function safeLink(value) {
  try { const url = new URL(value); return url.protocol === 'https:' || url.protocol === 'http:' ? url.href : '#'; }
  catch { return '#'; }
}

export function roundFinancialText(value) {
  // Leave links, ISO timestamps, dotted dates and identifiers untouched.
  const rounded = String(value ?? '').replace(/https?:\/\/\S+|\b\d{4}-\d{2}-\d{2}T\S+|\b\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?\b|\b\d{1,2}\.\d{1,2}\.\d{4}\b|(?<![\w.])[-+−]?\d[\d,]*\.\d+(?!\w|\.\d)/g, token => {
    if (/^https?:|T|:|^\d{1,2}\.\d{1,2}\.\d{4}$/.test(token)) return token;
    const n = Number(token.replaceAll(',', '').replace('−', '-'));
    if (!Number.isFinite(n)) return token;
    if (n !== 0 && Math.abs(n) < 0.005) return n < 0 ? '−<0.01' : '<0.01';
    const result = Math.abs(n).toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2, useGrouping:true});
    return (n < 0 ? '−' : token.startsWith('+') ? '+' : '') + result;
  });
  return rounded.replace(/https?:\/\/\S+|([$€£])([−+-]?\d[\d,]*)(?![\w.,])|(?<![\w.])([−+-]?\d+)%(?!\w)/g, (token, currency, amount, percent) => {
    if (token.startsWith('http')) return token;
    return currency ? currency + amount + '.00' : percent + '.00%';
  });
}

export function reportTime(value, timezone='America/New_York') {
  const date = new Date(typeof value === 'number' ? value * 1000 : value);
  if (value == null || !Number.isFinite(date.getTime())) return 'Unknown';
  return new Intl.DateTimeFormat('en-GB', {timeZone:timezone, day:'numeric', month:'short', year:'numeric', hour:'2-digit', minute:'2-digit', hour12:true}).format(date)
    + (timezone === 'Asia/Singapore' ? ' SGT' : ' ET');
}

export function presentationText(value, timezone='America/New_York') {
  // Only convert timestamps with an explicit offset. Preserve links and ambiguous dates.
  return roundFinancialText(value).replace(/https?:\/\/\S+|\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})|(?<![\w.])\d{5,}(?!\w|\.\d)/g, token => {
    if (token.startsWith('http')) return token;
    if (token.includes('T')) return reportTime(token, timezone);
    return Number.isSafeInteger(Number(token)) ? Number(token).toLocaleString('en-US') : token;
  });
}

export function narrative(value, timezone) {
  const labels = {change_pct:'change (%)',age_seconds:'feed age (seconds)',mean_return_pct:'average return (%)'};
  const inline = text => escapeText(presentationText(text.replace(/https?:\/\/\S+|\b(change_pct|age_seconds|mean_return_pct)\b/g, token => labels[token] || token), timezone)).replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
  const lines = String(value ?? '').split(/\n/).map(s => s.trim()).filter(Boolean);
  let html = '', list = false;
  for (const line of lines) {
    const bullet = /^[-*•]\s+/.test(line);
    if (bullet && !list) { html += '<ul>'; list = true; }
    if (!bullet && list) { html += '</ul>'; list = false; }
    html += bullet ? `<li>${inline(line.replace(/^[-*•]\s+/, ''))}</li>` : `<p>${inline(line)}</p>`;
  }
  return html + (list ? '</ul>' : '');
}

export function sourceLedger(report) {
  const result = new Map();
  const add = item => {
    const key = item.url || `${item.source}:${item.title}`;
    if (!result.has(key)) result.set(key, item);
  };
  (report.sources || []).forEach(add);
  (report.news || []).forEach(n => add({...n, summary:'', access:'not_recorded', usage:'used'}));
  (report.factors || []).filter(f => f.source_url).forEach(f => add({title:f.name, source:'Factor reference', url:f.source_url, published:'Unknown', summary:'', relevance:f.evidence, access:'not_recorded', usage:'used'}));
  return [...result.values()];
}

function snapshotMarkup(snapshot, timezone) {
  const m = snapshot?.market;
  if (!m) return '<p class="subtle-note">This older report has no saved market snapshot.</p>';
  const number = n => typeof n === 'number' && Number.isFinite(n);
  const dollars = n => number(n) ? '$'+n.toLocaleString('en-US', {minimumFractionDigits:2,maximumFractionDigits:2}) : 'Not recorded';
  const percent = n => number(n) ? (n>0?'+':'')+n.toFixed(2)+'%' : 'Not recorded';
  const sector = m.feed?.quotes?.['US.SMH'];
  const cutoff = Date.parse(snapshot.now_utc)/1000;
  // Never imply a simultaneous comparison when sessions or quote freshness differ.
  const comparable = m.live && sector?.session === m.session && number(m.change_pct) && number(sector?.change_pct)
    && number(m.asof) && number(sector.time) && [m.asof,sector.time].every(t=>cutoff-t>=-5 && cutoff-t<=120);
  const gap = comparable ? m.change_pct-sector.change_pct : null;
  const gapLabel=gap===null?'Not comparable':gap===0?'0.00 pp difference':`${Math.abs(gap)<0.005?'Less than 0.01':Math.abs(gap).toFixed(2)} pp ${gap<0?'behind':'ahead'}`;
  const session = {premarket:'Premarket',regular:'Regular hours',afterhours:'After-hours',overnight:'Overnight',historical:'Historical'}[m.session] || 'Session not recorded';
  return `<section class="report-snapshot" aria-label="Saved market snapshot">
    <div><span>Price at report cutoff</span><strong>${dollars(m.price)}</strong><small>${escapeText(session)} · ${escapeText(m.source || 'Source not recorded')}</small></div>
    <div><span>Session change</span><strong class="${m.change_pct<0?'down':number(m.change_pct)?'up':''}">${percent(m.change_pct)}</strong><small>vs saved reference ${dollars(m.reference)}</small></div>
    <div><span>Compared with chip stocks</span><strong>${gapLabel}</strong><small>${gap===null?'Matching, fresh quotes needed':'Percentage points vs SMH · quote times differ'}</small></div>
  </section><p class="snapshot-note">Saved observations, not live prices · Quote time ${escapeText(reportTime(m.asof,timezone))}${m.live?'':' · Feed was not live at cutoff'}</p>`;
}

export function reportMarkup(report, mode, dateLabel, timezone='America/New_York') {
  const original = report.body;
  const r = mode === 'beginner' && original.beginner ? original.beginner : original;
  const esc = escapeText;
  const prose = value => narrative(value, timezone);
  // Collapse only the labelled paragraph, never unrelated caveats that follow it.
  const limitations=String(r.limitations || '').replace(/\r\n?/g,'\n');
  const technical=/^\*\*Technical details:?\*\*:?[ \t]*(?:\n[ \t]*)*/gm;
  let limits='', cursor=0, technicalIndex=0, match;
  while((match=technical.exec(limitations))!==null){
    const start=technical.lastIndex;
    const boundary=limitations.slice(start).search(/\n[ \t]*\n|\n\*\*[^*\n]+\*\*/);
    const end=boundary<0?limitations.length:start+boundary;
    limits+=prose(limitations.slice(cursor,match.index));
    const notes=limitations.slice(start,end);
    limits+=notes.trim()?`<details class="technical-notes" data-source-key="technical-${technicalIndex++}"><summary>Technical details</summary>${prose(notes)}</details>`:prose(match[0]);
    cursor=end;technical.lastIndex=end;
  }
  limits+=prose(limitations.slice(cursor));
  const searchActivity=(original.search_audit || []).flatMap(entry=>{
    const action=entry.action || {};
    const queries=[...(Array.isArray(action.queries)?action.queries:[]),action.query,action.url]
      .filter(value=>typeof value==='string' && value.trim());
    // Structured queries are complete; entry.query can be a shortened batch label.
    return [...new Set(queries.length?queries:[entry.query || 'Search/page action; details not exposed'])];
  });
  const sources = sourceLedger(original).map(source => {
    const explanation = (r.news || []).find(news => news.url === source.url);
    return explanation ? {...source, relevance:explanation.relevance} : source;
  });
  const accessLabels = {read:'Read', headline_only:'Headline only', unavailable:'Unavailable', not_recorded:'Access not recorded'};
  return `<div class="report-controls"><div class="segmented" aria-label="Explanation mode"><button data-mode="beginner" aria-pressed="${mode==='beginner'}" class="${mode==='beginner'?'active':''}">Beginner</button><button data-mode="detailed" aria-pressed="${mode==='detailed'}" class="${mode==='detailed'?'active':''}">Detailed</button></div><span class="tiny-label">SAME EVIDENCE · YOUR READING LEVEL</span></div>
    ${mode==='beginner'&&!original.beginner?'<div class="banner neutral">This saved report has a detailed explanation only. Generate a beginner explanation from its saved evidence; this uses your subscription allowance.<button class="button secondary" id="generate-beginner">Create beginner explanation</button></div>':''}
    <h2 tabindex="-1" id="report-title">${esc(presentationText(r.headline,timezone).replaceAll('**',''))}</h2><div class="report-date">${original.generated_at?'Saved '+esc(reportTime(original.generated_at,timezone)):'Started '+esc(dateLabel)}<br>Data cutoff ${esc(reportTime(original.snapshot?.now_utc,timezone))}</div>
    ${snapshotMarkup(original.snapshot,timezone)}
    <nav class="report-sections" aria-label="Report sections">${[['takeaway','Takeaway'],['changes','What changed'],['intraday','Today'],['holding','Holding'],['drivers','Drivers'],['watch','Watch'],['limits','Limits'],['sources','Sources']].map(([id,label])=>`<button data-report-section="${id}">${label}</button>`).join('')}</nav>
    <section class="report-takeaway" id="report-takeaway" tabindex="-1"><h3>Main takeaway</h3>${prose(r.summary)}</section>
    <section class="report-section" id="report-changes" tabindex="-1"><h3>What changed since the previous report</h3>${prose(r.changes)}</section>
    <section class="report-section" id="report-intraday" tabindex="-1"><h3>${mode==='beginner'?'Today’s price action':'Intraday outlook'}</h3>${prose(r.intraday)}</section>
    <section class="report-section" id="report-holding" tabindex="-1"><h3>Holding outlook <small>Days to weeks</small></h3>${prose(r.holding)}</section>
    <section class="report-section" id="report-drivers" tabindex="-1"><h3>Relevant drivers</h3><p class="subtle-note">Ranked by relevance in this report. Research attention weights are category priorities, not probabilities; multiple drivers can share one category’s weight.</p>${r.factors.map((f,i)=>`<div class="factor-row"><strong><span class="driver-rank">${i+1}.</span> ${esc(f.name.replace(/^\d+[.)]\s+/,'').replace(/priorities/gi,'research attention weights').replace(/priority/gi,'research attention weight'))}</strong><span class="direction ${['supportive','adverse','mixed','unknown'].includes(f.direction)?f.direction:''}">${esc(f.direction)}</span><div>${prose(f.evidence)}${f.source_url?`<a href="${esc(safeLink(f.source_url))}" target="_blank" rel="noopener noreferrer">Source ↗</a>`:''}</div></div>`).join('')}</section>
    <section class="report-section" id="report-watch" tabindex="-1"><h3>Conditions to watch</h3>${r.scenarios.map(s=>`<section class="report-scenario"><strong>${esc(s.label)}</strong>${prose(s.condition)}<div class="reconsider"><strong>Reconsider if:</strong>${prose(s.invalidation)}</div></section>`).join('')}</section>
    <section class="report-section" id="report-limits" tabindex="-1"><h3>Limits & data quality</h3>${limits}</section>
    <details class="source-panel" id="report-sources" tabindex="-1" data-source-key="all"><summary>News & sources <span class="source-count">${sources.length}</span></summary>
      <p class="subtle-note">${esc(original.source_coverage || 'Legacy report: only saved news and factor links are available. Other sources from the original run cannot be recovered.')}</p>
      <div class="source-filters"><label>Search sources<input id="source-search" type="search" placeholder="Title, publisher or topic"></label><label>Evidence role<select id="source-filter"><option value="all">All sources (${sources.length})</option>${['used','background','excluded'].map(role=>`<option value="${role}">${role[0].toUpperCase()+role.slice(1)} (${sources.filter(s=>s.usage===role).length})</option>`).join('')}</select></label></div>
      <div class="source-actions"><span id="source-results" role="status"></span><button class="text-button" data-expand-sources="yes">Expand visible</button><button class="text-button" data-expand-sources="no">Collapse all</button></div>
      ${sources.map((n,i)=>`<details class="news-item" data-usage="${esc(n.usage || 'not_recorded')}" data-source-key="source-${i}"><summary>${esc(n.title)}<small>${esc(n.source)} · ${esc(presentationText(n.published || 'Publication time unknown',timezone))}</small><span class="source-badge">${esc(accessLabels[n.access] || 'Access not recorded')}</span> <span class="source-badge">${esc(n.usage || 'Role not recorded')}</span></summary>${prose(n.summary)}<strong>Why it matters</strong>${prose(n.relevance)}<a href="${esc(safeLink(n.url))}" target="_blank" rel="noopener noreferrer">Open source ↗</a></details>`).join('') || '<p>No sources were recorded.</p>'}
      ${searchActivity.length?`<details data-source-key="searches"><summary>Recorded search activity (${searchActivity.length})</summary><ul>${searchActivity.map(query=>`<li>${esc(query)}</li>`).join('')}</ul></details>`:''}
    </details>`;
}
