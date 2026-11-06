describe_indicator('WT Portfolio - Watchtower BUY SELL v1', 'price', {shortName:'WT Live Portfolio', decimals:2});
// Private portfolio feed. This key reads ONLY the model, never broker/accounts.
assert(current.resolution === 'D', 'Use the Daily chart for this portfolio.');
assert(current.chart_type === 'candles', 'Use regular candles for the model chart.');
const key = input.text('Private Watchtower feed key', '', {hide_in_legend:true});
const showStatus = input.boolean('Show portfolio status', true);
const response = await request.http('https://watchtower-mcp-production-a6ba.up.railway.app/integrations/trendspider/momentum-v1', 30, {'Authorization':'Bearer '+key});
assert(response && response.version === 'retention_buffer_v1', 'Watchtower feed unavailable. Check the private feed key or service.');
const data = response;
const moment = library('moment-timezone');
const now = Date.now();
const fresh = data.status === 'ready' && now <= Date.parse(data.expires_at);
const aliases = {'META':'FB','BKNG':'PCLN','XYZ':'SQ','BRK/B':'BRK.B','BRK-B':'BRK.B'};
const ticker = aliases[current.ticker] || current.ticker;
const member = data.universe.includes(ticker);
const records = data.events[ticker] || [];
const days = time.map(t => moment.unix(t).tz('America/New_York').format('YYYY-MM-DD'));
const buys = series_of(null), sells = series_of(null), corp = series_of(null), stop = series_of(null);
const buySignal = series_of(false), sellSignal = series_of(false), buyPlan = series_of(false), sellPlan = series_of(false);
const byDay = {};
for (const e of records) { if (!byDay[e.date]) byDay[e.date]=[]; byDay[e.date].push(e); }
const h = data.held[ticker];
const plans = fresh ? data.plans.filter(p => p.symbol === ticker) : [];
for (let i=0;i<days.length;i++) {
    for (const e of byDay[days[i]] || []) {
        const forward = e.source === 'forward_model';
        const suffix = e.source === 'reconciled_model' ? ' reconstructed' : forward ? ' model' : ' history';
        if (e.side === 'BUY') { buys[i]='BUY'+suffix; if(fresh && forward && e.date===data.asof) buySignal[i]=true; }
        else if (e.side === 'SELL') { sells[i]=(e.reason==='close_stop' ? 'SELL stop' : ticker==='QQQ' ? 'SELL reserve' : 'SELL rank')+suffix; if(fresh && forward && e.date===data.asof) sellSignal[i]=true; }
        else corp[i]='CORP settle';
    }
    if (fresh && h && h.stop && days[i]>=h.entry_date && days[i]<=data.asof) stop[i]=h.stop;
    if (days[i]===data.signal_close) {
        buyPlan[i]=plans.some(p=>p.side==='BUY');
        sellPlan[i]=plans.some(p=>p.side==='SELL');
    }
}
paint(buys,{name:'Portfolio model entries',style:'labels_below',color:'#ffffff',backgroundColor:'#15803d',fontSize:11,verticalOffset:12});
paint(sells,{name:'Portfolio model exits',style:'labels_above',color:'#ffffff',backgroundColor:'#b91c1c',fontSize:11,verticalOffset:12});
paint(corp,{name:'Corporate settlement',style:'labels_above',color:'#ffffff',backgroundColor:'#a16207',fontSize:10,verticalOffset:12});
paint(stop,{name:'Current model CLOSE stop - next open exit',color:'#dc2626',thickness:2});
register_signal(buySignal,'Watchtower model BUY observed');
register_signal(sellSignal,'Watchtower model SELL observed');
register_signal(buyPlan,'Watchtower conditional BUY plan for next session');
register_signal(sellPlan,'Watchtower SELL plan for next session');
const planText = plans.length ? plans.map(p=>p.side+' PLAN '+p.session+(p.maximum_open ? ' | opening cap $'+p.maximum_open.toFixed(2) : '')).join(' | ') : 'No new order planned for this stock';
const globalPlans = fresh ? data.plans.map(p=>(data.aliases[p.symbol]||p.symbol)+' '+p.side).join(', ') : '';
const last = records.length ? records[records.length-1] : null;
paint_overlay('WT Watchtower portfolio',{position:'top_right',order:'above_all',offset_y:40},{
    background:'rgba(17,24,39,0.95)',color:'#e5e7eb',fontSize:'11px',padding:'8px',borderRadius:'5px',
    rows:showStatus ? [
        {cells:[{text:'<b>WATCHTOWER '+(fresh ? 'CONNECTED' : 'UNAVAILABLE / STALE')+'</b> | '+data.asof+' '+data.phase,color:fresh ? '#86efac' : '#fca5a5'}]},
        {cells:[{text:!member ? 'OUTSIDE STRATEGY COHORT' : (h && h.units>0 ? 'MODEL HOLD' : 'MODEL FLAT')+(h && h.stop ? ' | daily close stop $'+h.stop.toFixed(2) : '')}]},
        {cells:[{text:fresh ? planText : 'New signals suppressed. '+(data.error || 'Refresh chart to check the feed.')} ]},
        {cells:[{text:'Portfolio next session '+(data.next_session || 'pending close')+': '+(globalPlans || (fresh ? 'no new stock orders' : 'unavailable'))}]},
        {cells:[{text:last ? 'Last: '+last.date+' '+last.side+' $'+last.price.toFixed(2)+' | '+last.reason : 'No model trade recorded'}]},
        {cells:[{text:'Checked '+moment(data.checked_at).tz('America/New_York').format('MMM D HH:mm:ss z')+' | '+data.note}]},
        {cells:[{text:'Green momentum = eligible, not BUY. Plans are conditional; arrows record model opening decisions.'}]}
    ] : []
});
