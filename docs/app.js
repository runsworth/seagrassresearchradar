const state = { data:null, digest:null, filtered:[], shown:20, map:null };
const $ = (id)=>document.getElementById(id);
const esc = (s='') => String(s).replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
const dayDiff = (iso) => { if(!iso) return 99999; const d=new Date(iso+'T00:00:00Z'); const n=new Date(); return Math.floor((Date.UTC(n.getUTCFullYear(),n.getUTCMonth(),n.getUTCDate())-d.getTime())/86400000); };
const fmtDate=(iso)=>{if(!iso)return 'Date not supplied'; const d=new Date(iso+'T00:00:00Z'); return d.toLocaleDateString(undefined,{day:'numeric',month:'short',year:'numeric'});};
const firstAuthor=(a=[])=>!a.length?'Unknown author':a.length===1?a[0]:`${a[0]} et al.`;

async function load(){
  try{
    const [p,d]=await Promise.all([fetch('data/papers.json?'+Date.now()).then(r=>r.json()),fetch('data/digest.json?'+Date.now()).then(r=>r.json())]);
    state.data=p; state.digest=d; init();
  }catch(e){ $('paperList').innerHTML=`<div class="empty-state"><strong>Could not load radar data.</strong><br>${esc(e.message)}</div>`; }
}

function init(){
  const p=state.data.papers||[];
  const gen=new Date(state.data.generated_at);
  $('updatePill').textContent=`Last scan ${gen.toLocaleDateString(undefined,{day:'numeric',month:'short'})} · ${gen.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})}`;
  $('footerUpdate').textContent=`Database refreshed ${gen.toLocaleString()}`;
  $('statToday').textContent=p.filter(x=>dayDiff(x.first_seen)===0).length;
  $('statWeek').textContent=p.filter(x=>dayDiff(x.first_seen)<=7).length;
  const recent=p.filter(x=>dayDiff(x.first_seen)<=30 || dayDiff(x.published_date)<=30);
  $('statOA').textContent=recent.length?`${Math.round(100*recent.filter(x=>x.open_access).length/recent.length)}%`:'—';
  $('statPublishers').textContent=new Set(p.map(x=>x.publisher_group).filter(Boolean)).size;
  fillFilters(p); bind(); applyFilters(); renderPublishers(); renderDigest();
}

function fillFilters(p){
  const fill=(id,vals)=>{const el=$(id); [...vals].sort((a,b)=>a.localeCompare(b)).forEach(v=>{const o=document.createElement('option');o.value=v;o.textContent=v;el.appendChild(o)});};
  fill('themeFilter',new Set(p.flatMap(x=>x.themes||[])));
  fill('speciesFilter',new Set(p.flatMap(x=>x.species||[])));
  fill('publisherFilter',new Set(p.map(x=>x.publisher_group).filter(Boolean)));
}

function bind(){
  document.querySelectorAll('.tab').forEach(b=>b.addEventListener('click',()=>showTab(b.dataset.tab)));
  document.querySelectorAll('[data-jump]').forEach(b=>b.addEventListener('click',()=>showTab(b.dataset.jump,true)));
  ['searchInput','timeFilter','themeFilter','speciesFilter','publisherFilter','oaFilter'].forEach(id=>$(id).addEventListener(id==='searchInput'?'input':'change',()=>{state.shown=20;applyFilters();}));
  $('showMore').addEventListener('click',()=>{state.shown+=20;renderPapers();});
}
function showTab(tab,scroll=false){
  document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('active',x.dataset.tab===tab));
  document.querySelectorAll('.panel').forEach(x=>x.classList.toggle('active',x.id===tab));
  if(tab==='map') setTimeout(renderMap,30);
  if(scroll) document.querySelector('.tabs').scrollIntoView({behavior:'smooth',block:'start'});
}

function inTime(p,val){
  if(val==='all')return true;
  if(val==='today')return dayDiff(p.first_seen)===0;
  const n=Number(val); return dayDiff(p.first_seen)<=n || dayDiff(p.published_date)<=n;
}
function applyFilters(){
  const q=$('searchInput').value.trim().toLowerCase(), tm=$('timeFilter').value, th=$('themeFilter').value, sp=$('speciesFilter').value, pu=$('publisherFilter').value, oa=$('oaFilter').checked;
  state.filtered=(state.data.papers||[]).filter(p=>{
    const hay=[p.title,p.abstract,(p.authors||[]).join(' '),p.journal,p.publisher,p.publisher_group,(p.species||[]).join(' '),(p.themes||[]).join(' ')].join(' ').toLowerCase();
    return (!q||hay.includes(q)) && inTime(p,tm) && (th==='all'||(p.themes||[]).includes(th)) && (sp==='all'||(p.species||[]).includes(sp)) && (pu==='all'||p.publisher_group===pu) && (!oa||p.open_access);
  }).sort((a,b)=>(b.first_seen||'').localeCompare(a.first_seen||'') || (b.published_date||'').localeCompare(a.published_date||'') || (b.relevance_score||0)-(a.relevance_score||0));
  renderPapers();
}

function renderPapers(){
  $('resultCount').textContent=`${state.filtered.length.toLocaleString()} matching paper${state.filtered.length===1?'':'s'}`;
  const rows=state.filtered.slice(0,state.shown);
  $('showMore').hidden=state.shown>=state.filtered.length;
  if(!rows.length){$('paperList').innerHTML='<div class="empty-state"><strong>No papers match these filters.</strong><br>Try a wider date range or clear one of the filters.</div>';return;}
  $('paperList').innerHTML=rows.map(p=>{
    const today=dayDiff(p.first_seen)===0;
    const badges=[today?'<span class="badge today">NEW TODAY</span>':'',p.open_access?'<span class="badge oa">OPEN ACCESS</span>':'',`<span class="badge publisher">${esc(p.publisher_group||'Publisher unknown')}</span>`,...(p.sources||[]).map(s=>`<span class="badge">${esc(s)}</span>`)].join('');
    const ai=p.ai_summary?`<div class="ai-box"><strong>Radar summary</strong><p>${esc(p.ai_summary)}</p>${p.why_it_matters?`<p><b>Why it matters:</b> ${esc(p.why_it_matters)}</p>`:''}</div>`:'';
    return `<article class="paper-card"><div class="paper-top"><div class="paper-main"><div class="paper-kicker">${badges}</div><h3><a href="${esc(p.url||'#')}" target="_blank" rel="noopener">${esc(p.title)}</a></h3><p class="citation-line">${esc(p.journal||'Journal not supplied')} · published ${fmtDate(p.published_date)}</p><p class="authors">${esc((p.authors||[]).slice(0,8).join(', '))}${(p.authors||[]).length>8?' et al.':''}</p>${p.abstract?`<p class="paper-abstract">${esc(p.abstract)}</p>`:''}${ai}<div class="theme-row">${(p.themes||[]).slice(0,5).map(t=>`<span class="theme-chip">${esc(t)}</span>`).join('')}${(p.species||[]).slice(0,3).map(t=>`<span class="theme-chip"><i>${esc(t)}</i></span>`).join('')}</div></div><div class="paper-meta"><small>First seen</small><strong>${fmtDate(p.first_seen).replace(/ \d{4}$/,'')}</strong><small>${p.doi?`DOI ${esc(p.doi)}`:'No DOI in metadata'}</small></div></div></article>`
  }).join('');
}

function renderPublishers(){
  const cov=state.data.publisher_coverage||[];
  $('publisherGrid').innerHTML=cov.map(x=>`<article class="publisher-card"><div class="row"><div><h3><span class="status-dot ${x.papers_in_database?'':'empty'}"></span>${esc(x.publisher)}</h3><p>${x.latest_published?'Latest matching paper '+fmtDate(x.latest_published):'No matching paper in retained window'}</p></div><strong>${x.papers_in_database}</strong></div>${x.journals_seen?.length?`<p>${esc(x.journals_seen.slice(0,3).join(' · '))}</p>`:''}</article>`).join('');
  const st=state.data.source_status||{};
  $('sourceHealth').innerHTML=Object.entries(st).map(([k,v])=>`<article class="source-card ${v.ok?'ok':'fail'}"><strong>${esc(k)}</strong><span>${v.ok?`${v.records||0} relevant records this scan`:v.skipped?`Skipped: ${esc(v.error||'configuration needed')}`:`Scan problem: ${esc(v.error||'unknown error')}`}</span>${v.seconds!=null?`<span>${v.seconds}s</span>`:''}</article>`).join('');
}

function renderDigest(){
  const d=state.digest||{};
  $('digestLead').innerHTML=`<strong>${d.new_papers||0}</strong><p>papers first discovered by the Radar in the last seven days.</p>`;
  const entries=Object.entries(d.theme_counts||{}); const max=Math.max(1,...entries.map(x=>x[1]));
  $('themeBars').innerHTML=entries.slice(0,10).map(([k,v])=>`<div class="bar"><span>${esc(k)}</span><div class="bar-track"><div class="bar-fill" style="width:${Math.round(v/max*100)}%"></div></div><b>${v}</b></div>`).join('')||'<p>No new records in the seven-day window yet.</p>';
  $('digestPapers').innerHTML=(d.top_papers||[]).slice(0,8).map(p=>`<div class="digest-paper"><a href="${esc(p.url||'#')}" target="_blank" rel="noopener">${esc(p.title)}</a><small>${esc(firstAuthor(p.authors))} · ${esc(p.journal||'Journal unknown')}</small></div>`).join('')||'<p>No weekly papers yet.</p>';
}

function renderMap(){
  if(!window.L)return;
  if(!state.map){state.map=L.map('researchMap',{scrollWheelZoom:false}).setView([15,20],2);L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:8,attribution:'&copy; OpenStreetMap contributors'}).addTo(state.map);}
  if(state.mapLayer) state.mapLayer.clearLayers();
  state.mapLayer=L.layerGroup().addTo(state.map);
  const agg=new Map();
  (state.data.papers||[]).forEach(p=>(p.location_inference||[]).forEach(loc=>{const x=agg.get(loc.country)||{...loc,count:0,papers:[]};x.count++;if(x.papers.length<6)x.papers.push(p);agg.set(loc.country,x)}));
  [...agg.values()].forEach(x=>{const marker=L.circleMarker([x.lat,x.lon],{radius:Math.min(20,6+Math.sqrt(x.count)*3),weight:1,fillOpacity:.72});marker.bindPopup(`<strong>${esc(x.country)}</strong><br>${x.count} paper${x.count===1?'':'s'} inferred<br><small>${x.papers.slice(0,3).map(p=>esc(p.title)).join('<br><br>')}</small>`);marker.addTo(state.mapLayer);});
  const top=[...agg.values()].sort((a,b)=>b.count-a.count).slice(0,8);
  $('mapSummary').innerHTML=top.map(x=>`<div class="map-country"><strong>${esc(x.country)}</strong><span>${x.count} inferred paper${x.count===1?'':'s'}</span></div>`).join('')||'<div class="empty-state">No explicit study locations have yet been inferred from the retained records.</div>';
  setTimeout(()=>state.map.invalidateSize(),60);
}
load();
