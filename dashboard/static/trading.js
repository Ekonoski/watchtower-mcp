/* Trading workspace. Display and local paper planning only; no broker API. */
function paperGeometry(entry, stop, target, budget) {
  const values=[entry,stop,target,budget].map(x=>Number(x));
  if(values.some(x=>!Number.isFinite(x)||x<=0)) return {error:'Enter four positive values.'};
  [entry,stop,target,budget]=values;
  if(stop>=entry) return {error:'For a long-share plan, stop must be below entry.'};
  if(target<=entry) return {error:'Target must be above entry.'};
  const risk=entry-stop,shares=Math.floor((budget+1e-10)/risk);
  if(shares<1) return {error:'Risk budget is smaller than one share of planned risk.'};
  return {risk,shares,rewardRisk:(target-entry)/risk,entry,stop,target,budget};
}
function frameTiming(frame, receivedWallMs, receivedMonoMs, callbackMonoMs) {
  if(frame.preview) return null; // Recorded event time cannot measure a live feed.
  const valid=v=>typeof v==='number'&&Number.isFinite(v);
  return {received_at_ms:receivedWallMs, version:frame.version,
    frame_age_ms:valid(frame.sent_at)?receivedWallMs-frame.sent_at*1000:null,
    latest_event_age_ms:valid(frame.market_at)?receivedWallMs-frame.market_at*1000:null,
    selected_event_age_ms:valid(frame.selected?.market_at)?receivedWallMs-frame.selected.market_at*1000:null,
    callback_ms:callbackMonoMs-receivedMonoMs};
}
if(typeof module!=='undefined') module.exports={paperGeometry,frameTiming};
if(typeof document!=='undefined') (()=>{
  'use strict';
  const $=id=>document.getElementById(id),n=v=>v!==null&&v!==undefined&&Number.isFinite(Number(v));
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const price=v=>n(v)?'$'+Number(v).toFixed(Number(v)<1?4:2):'—';
  const pct=v=>n(v)?(Number(v)>0?'+':'')+Number(v).toFixed(2)+'%':'—';
  const color=v=>!n(v)?'':v>0?'green':v<0?'red':'';
  const number=v=>n(v)?Intl.NumberFormat('en-US',{notation:'compact',maximumFractionDigits:2}).format(v):'—';
  const et=v=>n(v)?new Date(v*1000).toLocaleTimeString('en-US',{timeZone:'America/New_York',hour12:false}):'—';
  let frame=null,selection='',stream=null,lastFrame=0,lastDraw=0,feed='leaders',contextRows=[],contextError='',contextToken=0;
  let locked=null,user='unidentified',key='',tabVisible=true,connectionLost=false,storageFailed=false;
  const timingSamples=[];let timingDropped=0;
  const discoveries=[],discoveryKeys=new Set();let discoveryOmitted=0;
  const discoveryClock='browser-'+Date.now()+'-'+Math.random().toString(36).slice(2);
  function report(text){$('plan-message').textContent=text;}
  function store(){storageFailed=false;if(!key)return;try{localStorage.setItem(key,JSON.stringify(locked));}catch{storageFailed=true;}}
  function loadPlan(){locked=null;if(key)try{locked=JSON.parse(localStorage.getItem(key)||'null');}catch{} applyPlan();}
  const inputs=['plan-entry','plan-stop','plan-target','plan-budget','plan-note'];
  function applyPlan(){
    const mapping=['entry','stop','target','budget','note'];
    inputs.forEach((id,i)=>{$(id).disabled=!!locked;$(id).value=locked?locked[mapping[i]]??'':'';});
    $('plan-state').textContent=locked?'Locked · '+selection:'Draft';$('lock-plan').hidden=!!locked;
    $('new-plan').hidden=!locked;$('export-plan').disabled=!locked;
    if(locked)report('Locked '+new Date(locked.locked_at).toLocaleTimeString()+'. Reference only; no order was sent.');
    else report('Long-share planning aid. Costs and slippage can increase loss.');
    updateGeometry();
    if(storageFailed)report('Browser storage unavailable. Changes exist only in this tab; export the locked plan.');
  }
  function updateGeometry(){
    const g=paperGeometry(...inputs.slice(0,4).map(id=>$(id).value));
    $('risk-share').textContent=g.error?'—':price(g.risk);
    $('risk-ratio').textContent=g.error?'—':g.rewardRisk.toFixed(2)+' : 1';
    $('risk-size').textContent=g.error?'—':g.shares.toLocaleString();
    return g;
  }
  function select(symbol){
    if(!/^[A-Z0-9.\-]{1,16}$/.test(symbol)||symbol===selection)return;
    selection=symbol;key='wt.paper-plan.v1:'+user+':'+symbol;loadPlan();
    $('focus-symbol').textContent=symbol;$('chart-link').href='https://www.tradingview.com/chart/?symbol='+encodeURIComponent(symbol);
    $('focus-price').textContent='—';$('focus-volume').textContent='—';$('focus-five').textContent='—';$('focus-stamp').textContent='—';$('volume-stamp').textContent='Timestamp unavailable';$('focus-change').textContent='Day change unavailable';$('focus-source').textContent='Waiting for selected data';
    drawChart([]);renderContext();connect();renderRows();
  }
  function connect(){
    if(stream)stream.close();
    stream=new EventSource('/api/trading/stream'+(selection?'?symbol='+encodeURIComponent(selection):''));
    stream.addEventListener('auth',()=>{stream.close();connectionLost=true;$('feed-state').textContent='Sign in required';$('feed-note').textContent='Open Account & research to sign in, then reload.';});
    stream.onmessage=event=>{
      const receivedMono=performance.now(),receivedWall=Date.now();
      try{frame=JSON.parse(event.data);}catch{$('feed-state').textContent='Invalid response';return;}
      const measuredFrame=frame;
      lastFrame=performance.now();connectionLost=false;
      if(frame.preview||frame.notice){$('preview-banner').hidden=false;$('preview-banner').textContent=frame.preview||frame.notice;}
      renderHealth();renderRows();renderFocus();
      if(!selection){const first=frame.scanner?.[$('price-band').value]?.leaders?.rows?.[0];if(first)select(first.ticker);}
      if(!document.hidden)requestAnimationFrame(()=>{
        if(document.hidden)return;
        const sample=frameTiming(measuredFrame,receivedWall,receivedMono,performance.now());
        if(!sample){$('browser-timing').textContent='Replay: live timing not measured';return;}
        timingSamples.push(sample);
        if(timingSamples.length>1000){timingSamples.shift();timingDropped++;}
        $('browser-timing').textContent=`Frame age ${sample.frame_age_ms===null?'unknown':Math.round(sample.frame_age_ms)+'ms'} (clock-dependent) · render callback ${sample.callback_ms.toFixed(1)}ms`;
      });
    };
    stream.onerror=()=>{connectionLost=true;$('feed-state').textContent='Disconnected';$('feed-state').className='amber';$('feed-note').textContent='Reconnecting. Displayed prices retain their original timestamps.';};
  }
  function effectiveNow(){return frame?.preview&&frame.preview_now?frame.preview_now:Date.now()/1000;}
  function renderHealth(){
    if(!frame||connectionLost)return;
    const seconds=frame.received_at?Math.max(0,effectiveNow()-frame.received_at):null;
    const fresh=frame.state==='receiving'&&seconds!==null&&seconds<3;
    $('feed-state').textContent=frame.preview?'Recorded replay':fresh?'Receiving data':frame.state==='receiving'?'No recent market events':frame.state[0].toUpperCase()+frame.state.slice(1);
    const timingConcern=fresh&&n(frame.lag_p95_ms)&&frame.lag_p95_ms>2000;
    if(timingConcern&&!frame.preview)$('feed-state').textContent='Timing needs review';
    $('feed-state').className=fresh&&!frame.preview&&!timingConcern?'green':'amber';
    $('feed-note').textContent=frame.preview?'Historical source; no live execution':timingConcern?'Apparent event age exceeds 2s; includes clock offset.':frame.reason;
    $('feed-lag').textContent=n(frame.lag_p95_ms)?Math.round(frame.lag_p95_ms)+' ms':'—';
    $('lag-count').textContent=frame.lag_sample_count?number(frame.lag_sample_count)+' measured trade arrivals':'No measured events';
    $('tracked').textContent=number(frame.tracked_count||0)+' observed';
    $('status-detail').textContent=frame.reason+' · '+(frame.invalid_events||0)+' rejected events · '+(frame.filtered_conditions||0)+' condition-filtered trades · '+(frame.dropped_symbols||0)+' symbols over capacity';
    const refs=frame.scanner_reference_status;
    if(refs)$('status-detail').textContent+=` · References: ${refs.status}; ${refs.loaded_count||0} loaded, ${refs.pending_count||0} queued, ${refs.failed_count||0} with errors, ${refs.deferred_count||0} deferred by cap`;
    const catalog=frame.float_catalog_status;
    if(catalog)$('status-detail').textContent+=` · Float catalog: ${catalog.status}, ${catalog.record_count||0} dated records, ${catalog.invalid_count||0} invalid`;
    const rec=frame.recording;
    if(rec)$('status-detail').textContent+=` · Server recording: ${rec.state}; ${rec.saved||0} saved, ${rec.pending||0} pending, ${rec.dropped||0} lost${rec.error?' · '+rec.error:''}`;
    if(rec?.lists)$('status-detail').textContent+=` · List recording: ${rec.lists.state}${rec.lists.error?' · '+rec.lists.error:''}`;
    if(rec?.continuation)$('status-detail').textContent+=` · Continuations: ${rec.continuation.state}, ${rec.continuation.runner_count??0} recent runners${rec.continuation.reason?' · '+rec.continuation.reason:''}`;
  }
  function renderRows(){
    if(!frame)return;
    const query=$('symbol-search').value.trim().toUpperCase(),direction=$('move-filter').value;
    const band=$('price-band').value, group=frame.scanner?.[band], view=group?.[feed];
    const range=band==='preferred'?[3,8]:band==='broader'?[3,20]:[0,Infinity];
    let rows=feed==='alerts'?[...(frame.alerts||[])]:[...(view?.rows||[])];
    const field=feed==='leaders'?'chg_pct':feed==='alerts'?'move_pct':'move_1m';
    rows=rows.filter(r=>r.price>=range[0]&&r.price<=range[1]&&(!query||r.ticker.includes(query))&&
      (direction==='all'||(direction==='up'?r[field]>0:r[field]<0)));
    // Capture first display in the selected view only, with its filters.
    // Do not mislabel server events or off-screen lists as visible discoveries.
    const observedRows=rows.map(r=>({...r})),observedFrame=frame,observedFeed=feed;
    if(!document.hidden&&!frame.preview)requestAnimationFrame(()=>{
      if(document.hidden||feed!==observedFeed||$('price-band').value!==band||connectionLost)return;
      if(observedFrame.state!=='receiving')return;
      const when=new Date().toISOString();
      for(const r of observedRows){
        if(observedFeed==='continuation'&&!r.continuation?.price_is_live)continue;
        const family=observedFeed==='alerts'?
          (r.direction==='up'&&r.label==='5% / 5m'?'Squeeze Alert - Up 5% in 5min':
           r.direction==='up'&&r.label==='10% / 10m'?'Squeeze Alert - Up 10% in 10min':'Watchtower '+r.label+' '+r.direction):
          observedFeed==='leaders'?'Top Gainers':observedFeed==='volume'?'Top Volume 5 Minutes':'Watchtower experimental '+observedFeed;
        const key=[observedFrame.session_date,family,r.ticker,band,query,direction].join('|');
        if(discoveryKeys.has(key))continue;
        // Bound both arrays and key storage; disclose observations refused at cap.
        if(discoveries.length>=10000){discoveryOmitted++;continue;}
        discoveryKeys.add(key);
        discoveries.push({session:observedFrame.session_date,ticker:r.ticker,family,
          observed_at:when,clock_id:discoveryClock,live:true,evidence:'browser_render_callback',
          source_ref:'Watchtower frame '+observedFrame.version,price:r.price,market_at:r.market_at,
          reference_context:r.reference_context||{float_reference:r.float_reference,rvol_5m:r.rvol_5m,reference_errors:r.reference_errors,inputs_observed_at:r.inputs_observed_at},
          filters:{band,query,direction},rule_equivalence:'unverified',view:observedFeed});
      }
    });
    $('move-empty').hidden=rows.length>0;
    $('move-empty').querySelector('h3').textContent=query?'No match in this view':'No qualifying names';
    $('move-empty').querySelector('p').textContent='Select another view or price range. Enter a ticker to inspect it directly. Unknown history is not a zero reading.';
    $('mover-body').innerHTML=rows.map(r=>{
      const age=n(r.market_at)?effectiveNow()-r.market_at:null,stale=age===null||age>15;
      const held=feed==='focus'?`<small>${stale?'Stale price · ':''}${r.attention_state==='holding'?'Pullback / cooling':'Qualified'} · ${Math.max(0,r.hold_remaining_seconds-(effectiveNow()-frame.sent_at)).toFixed(0)}s hold</small>`:'';
      const continuation=feed==='continuation'?`<small>${esc(r.continuation?.state)} · ran ${esc(r.continuation?.runner_session)}</small><small>Catalyst / splits: check source</small>`:'';
      const oldPrice=feed==='continuation'&&!r.continuation?.price_is_live?'<small>Historical / stale price</small>':'';
      return `<tr class="${r.ticker===selection?'selected':''}"><td><button data-symbol="${esc(r.ticker)}">${esc(r.ticker)}</button><small>${feed==='alerts'?esc(r.label):n(r.chg_pct)?pct(r.chg_pct)+' day':'Day change unavailable'}</small>${held}${continuation}</td><td>${price(r.price)}${oldPrice}</td><td class="${color(r[field])}">${feed==='volume'?number(r.volume_5m?.volume):pct(r[field])}</td><td class="${stale?'amber':''}">${age===null?'No live print':age.toFixed(1)+'s'}${stale?'<small>Stale</small>':''}</td></tr>`;
    }).join('');
    $('mover-body').querySelectorAll('[data-symbol]').forEach(b=>b.onclick=()=>select(b.dataset.symbol));
    $('movers').querySelector('th:nth-child(3)').textContent=feed==='leaders'?'DAY %':feed==='volume'?'5M VOL':feed==='alerts'?'MOVE':'1 MIN';
    const rule=feed==='volume'?'Completed observed five-minute aggregate window; not RVOL.':feed==='leaders'?'Positive day gain, ranked highest first; prices ≤15s old.':feed==='fresh'?'At least +3% in an observed minute; prices ≤15s old.':feed==='focus'?'Retain for 5 minutes after last +10% day or +3%/minute qualification; newest arrivals first.':'5 / 10 minute threshold history; these are past alerts.';
    $('coverage-note').textContent=`${rows.length} shown · ${view?.omitted??frame.alerts_omitted??0} omitted by cap. ${rule} ${group?.warming??0} warming one-minute histories; ${group?.missing_day_reference??0} missing prior closes. Thresholds are research defaults, not entry signals.`;
    if(feed==='continuation')$('coverage-note').textContent=`${rows.length} shown · ${view?.omitted??0} omitted. Last 5 cached market sessions: prior +10% runner and ≥$1m approximate turnover. Momentum: +3%/1m. Volume: ≥10k/1m, ≥25k/5m and ≥2× local 5m average pace; not historical RVOL. History ${view?.history_status?.state||'loading'}; ${view?.history_status?.sessions?.join(', ')||view?.history_status?.reason||'waiting'}. News and splits unverified; research observations, not entries.`;
  }
  function renderFocus(){
    renderContext();
    const r=frame?.selected?.ticker===selection?frame.selected:null;
    if(!r){$('high-volume-context').textContent='Known high and five-minute volume unavailable';$('focus-source').textContent=selection?'No event received':'No selection';drawChart([]);return;}
    $('high-volume-context').textContent='Known high '+price(r.known_high)+' · full HOD coverage unverified · 5m volume '+number(r.volume_5m?.volume)+' · '+(r.volume_5m?.as_of?et(r.volume_5m.as_of)+' ET':r.volume_5m?.status||'unavailable');
    $('focus-price').textContent=price(r.price);$('focus-change').textContent=n(r.chg_pct)?pct(r.chg_pct)+' vs prior close':'Day change unavailable';$('focus-change').className=color(r.chg_pct);
    $('focus-volume').textContent=number(r.volume);$('volume-stamp').textContent=r.volume_market_at?et(r.volume_market_at)+' ET · '+Math.max(0,effectiveNow()-r.volume_market_at).toFixed(1)+'s old':'Timestamp unavailable';$('focus-five').textContent=pct(r.move_5m);$('focus-five').className=color(r.move_5m);
    $('focus-stamp').textContent=et(r.market_at);$('focus-source').textContent=Math.max(0,effectiveNow()-r.market_at).toFixed(1)+'s since event';
    if(performance.now()-lastDraw>300){drawChart(frame.history||[]);lastDraw=performance.now();}
  }
  function drawChart(history){
    const el=$('price-chart'),rect=el.getBoundingClientRect(),ratio=devicePixelRatio||1;
    el.width=rect.width*ratio;el.height=rect.height*ratio;const c=el.getContext('2d');c.scale(ratio,ratio);
    const w=rect.width,h=rect.height;c.clearRect(0,0,w,h);
    $('chart-empty').hidden=history.length>0;
    $('chart-status').textContent=history.length?'Observed '+Math.floor(history.at(-1)[0]-history[0][0])+' sec':'Waiting for data';
    if(!history.length)return;
    const bins=new Map();for(const x of history){const t=Math.floor(x[0]/60)*60,b=bins.get(t);if(b){b.high=Math.max(b.high,x[2]);b.low=Math.min(b.low,x[3]);b.close=x[1];}else bins.set(t,{t,open:x[4],high:x[2],low:x[3],close:x[1]});}
    const bars=[...bins.values()],hi=Math.max(...bars.map(x=>x.high)),lo=Math.min(...bars.map(x=>x.low)),pad=Math.max((hi-lo)*.14,hi*.001),min=lo-pad,max=hi+pad;
    const chartW=w-63,chartH=h-28,y=v=>8+(max-v)/(max-min)*(chartH-12);
    c.font='10px Consolas';c.textAlign='left';for(let i=0;i<5;i++){const value=min+(max-min)*i/4,yy=y(value);c.strokeStyle='#253240';c.lineWidth=.5;c.beginPath();c.moveTo(0,yy);c.lineTo(chartW,yy);c.stroke();c.fillStyle='#8b9db2';c.fillText(value.toFixed(2),chartW+8,yy+3);}
    const start=bars[0].t,end=Math.max(start+600,bars.at(-1).t+60),step=chartW/((end-start)/60);
    bars.forEach(b=>{const x=(b.t-start)/(end-start)*chartW+step/2;c.strokeStyle=c.fillStyle=b.close>=b.open?'#59d5ad':'#fa8190';c.lineWidth=1;c.beginPath();c.moveTo(x,y(b.high));c.lineTo(x,y(b.low));c.stroke();c.fillRect(x-step*.27,Math.min(y(b.open),y(b.close)),step*.54,Math.max(1,Math.abs(y(b.close)-y(b.open))));});
    c.fillStyle='#8b9db2';[0,Math.floor(bars.length/2),bars.length-1].filter((x,i,a)=>a.indexOf(x)===i).forEach(i=>{const b=bars[i];c.fillText(et(b.t).slice(0,5),Math.max(0,(b.t-start)/(end-start)*chartW-8),h-7);});
  }
  async function loadContext(){
    const token=++contextToken;
    try{const r=await fetch('/api/momentum?scanner=all');if(!r.ok)throw Error('Context unavailable');const d=await r.json();if(token!==contextToken)return;if(d.error)throw Error(d.error);contextRows=d.rows||[];contextError='';}
    catch{contextError='Saved context unavailable';contextRows=[];}
    renderContext();
  }
  function renderContext(){
    const r=contextRows.find(r=>r.ticker===selection);
    const news=frame?.news?.ticker===selection?frame.news:null;
    const dateTime=v=>new Date(v*1000).toLocaleString('en-US',{timeZone:'America/New_York'});
    $('headline').textContent=news?.articles?.length?'Latest provider headlines':news?.status==='available'?'No headlines returned by these feeds':'Provider news loading or unavailable';
    $('provider-news').innerHTML=(news?.articles||[]).map(a=>`<p>${a.url?`<a href="${esc(a.url)}" target="_blank" rel="noopener noreferrer">${esc(a.headline)}</a>`:esc(a.headline)}<small style="display:block;color:var(--muted)">${esc(a.provider)} · ${esc(a.publisher)} · ${esc(a.kind)}<br>Published ${esc(dateTime(a.published_at))} ET · First received ${esc(dateTime(a.first_seen_at))} ET<br>${esc(a.publication_timezone_note)}</small></p>`).join('');

    const live=frame?.selected?.ticker===selection?frame.selected:frame?.selected_reference?.ticker===selection?frame.selected_reference:null,f=live?.float_reference,rv=live?.rvol_5m;
    $('stock-float').textContent=f?.value?number(f.value)+(f.status==='available'?'':' · check'):'Unavailable';
    $('float-source').textContent=f?.source?`${f.source} · effective ${f.effective_date_raw||'unknown'} · ${f.reason||'dated provider value'}${f.age_warning?' · '+f.age_warning:''}`:'No dated free-float reference';
    $('stock-rvol').textContent=rv?.status==='available'?rv.ratio.toFixed(2)+'x':'Unavailable';
    $('rvol-source').textContent=rv?.status==='available'?`5m ending ${et(rv.current.as_of)} ET / same clock over ${rv.baseline.sample_sessions} prior sessions · not verified as Warrior RVOL`:(rv?.reason||'Waiting for reference history and completed volume window');
    $('context-stamp').textContent=news?.checked_at?'News checked '+et(news.checked_at)+' ET':'News not checked';
    const alternate=f?.alternate?` Other provider: ${number(f.alternate.value)} (${f.alternate.source}, ${f.alternate.provider_date_raw||'undated'}).`:'';
    const errors=live?.reference_errors?Object.entries(live.reference_errors).map(([k,v])=>`${k}: ${v}`).join(' · '):'';
    $('context-note').textContent=(news?.note||'Provider news has not loaded.')+(news?.omitted_count?' '+news.omitted_count+' additional returned articles omitted.':'')+(news?.stale?' News refresh is stale.':'')+(news?.errors&&Object.keys(news.errors).length?' News source errors: '+Object.entries(news.errors).map(([k,v])=>k+': '+v).join(' · '):'')+alternate+(f?.review_reason?' '+f.review_reason:'')+(errors?' Reference gaps: '+errors:'');
  }
  $('plan-form').onsubmit=e=>{e.preventDefault();if(!selection){report('Choose a ticker first.');return;}const g=updateGeometry();if(g.error){report(g.error);return;}locked={...g,ticker:selection,note:$('plan-note').value,locked_at:new Date().toISOString(),type:'manual_paper_plan',source_market_at:frame?.selected?.market_at??null};store();applyPlan();};
  inputs.slice(0,4).forEach(id=>$(id).addEventListener('input',updateGeometry));
  $('new-plan').onclick=()=>{locked=null;store();applyPlan();};
  $('export-plan').onclick=()=>{if(!locked)return;const url=URL.createObjectURL(new Blob([JSON.stringify(locked,null,2)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download='watchtower-paper-plan-'+selection+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
  $('export-timing').onclick=()=>{
    const payload={exported_at:new Date().toISOString(),samples:timingSamples,omitted_samples:timingDropped,
      note:'Last 1000 visible-tab frame samples. Wall-clock ages include clock offset; latest_event_age is the newest stream event, not all trades. Selected-event age includes inactivity. Monotonic callback time includes parsing and rendering work before requestAnimationFrame; it does not prove physical screen paint or Warrior parity.'};
    const url=URL.createObjectURL(new Blob([JSON.stringify(payload,null,2)],{type:'application/json'})),a=document.createElement('a');
    a.href=url;a.download='watchtower-browser-timing.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  };
  $('export-discovery').onclick=()=>{
    const payload={observations:discoveries,coverage:[],omitted_observation_attempts:discoveryOmitted,
      note:'First render-callback observation per session/family/ticker/filter, selected view only; not a physical-paint guarantee. Coverage is incomplete: UI caps, filters, warmup, reference gaps and unattended intervals must be reconciled before scoring misses. Alert names map published magnitude/time only, not exact Warrior eligibility. Do not mix filtered views in a full-universe comparison.'};
    const url=URL.createObjectURL(new Blob([JSON.stringify(payload,null,2)],{type:'application/json'})),a=document.createElement('a');
    a.href=url;a.download='watchtower-discovery-observations.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  };
  $('history-date').value=new Intl.DateTimeFormat('en-CA',{timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
  $('export-history').onclick=async()=>{
    const day=$('history-date').value;
    if(!day)return;
    $('export-history').disabled=true;
    $('history-status').textContent='Loading saved records…';
    try{
      let offset=0, records=[], page;
      do{
        const response=await fetch(`/api/trading/history?session=${encodeURIComponent(day)}&offset=${offset}`);
        if(!response.ok)throw new Error(response.status===401?'Sign in to export history':'History unavailable; no file exported');
        page=await response.json();records.push(...page.records);offset=page.next_offset;
      }while(offset!==null);
      const payload={...page,offset:0,next_offset:null,records,exported_at:new Date().toISOString()};
      const url=URL.createObjectURL(new Blob([JSON.stringify(payload,null,2)],{type:'application/json'})),a=document.createElement('a');
      a.href=url;a.download=`watchtower-server-history-${day}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
      $('history-status').textContent=`Exported ${records.filter(r=>r.kind==='alert').length} alerts, ${records.filter(r=>r.kind==='list_snapshot').length} list snapshots, ${records.filter(r=>r.kind==='list_membership').length} membership changes plus coverage records. Check gaps before comparison.`;
    }catch(error){$('history-status').textContent=error.message;}
    finally{$('export-history').disabled=false;}
  };
  $('symbol-search').oninput=renderRows;$('symbol-search').onkeydown=e=>{if(e.key==='Enter')select(e.target.value.trim().toUpperCase());};$('move-filter').onchange=renderRows;$('price-band').onchange=renderRows;
  document.querySelectorAll('[data-feed]').forEach(b=>b.onclick=()=>{feed=b.dataset.feed;document.querySelectorAll('[data-feed]').forEach(x=>x.classList.toggle('on',x===b));renderRows();});
  $('density').onclick=()=>{const compact=document.body.classList.toggle('compact');$('density').setAttribute('aria-pressed',String(compact));$('density').textContent=compact?'Comfortable view':'Compact view';};
  window.addEventListener('resize',()=>drawChart(frame?.history||[]));
  document.addEventListener('visibilitychange',()=>{tabVisible=!document.hidden;});
  setInterval(()=>{if(!tabVisible)return;$('clock').textContent=et(Date.now()/1000)+' ET';const elapsed=lastFrame?(performance.now()-lastFrame)/1000:null;$('frame-age').textContent=elapsed===null?'—':elapsed.toFixed(1)+'s ago';if(elapsed>3){$('feed-state').textContent='Browser stream interrupted';$('feed-state').className='amber';}else renderHealth();renderRows();renderFocus();},1000);
  setInterval(loadContext,60000);
  (async()=>{try{const r=await fetch('/api/me');if(!r.ok)throw Error('auth');const me=await r.json();user=me.username||'local';connect();loadContext();}catch{$('feed-state').textContent='Sign in required';$('feed-note').textContent='Use Account & research to sign in, then reload this workspace.';}})();
})();
