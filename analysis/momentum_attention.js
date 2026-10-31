describe_indicator('WT Daily Attention - Watchlist v1', 'lower', {shortName:'WT Daily Attention', decimals:2, warmup:206});
// Current review snapshot ONLY. Not historical entries, backtest signals or orders.
assert(current.resolution === 'D', 'Use Daily bars for this review list.');
const [sp,qq] = await Promise.all([
    request.history('SPY','D',{ext_session:false,chart_type:'candles'}),
    request.history('QQQ','D',{ext_session:false,chart_type:'candles'})
]);
assert(sp && !sp.error && qq && !qq.error, 'Benchmark history unavailable');
const valid=x=>typeof x==='number' && Number.isFinite(x);
const blend=p=>p.map((c,i)=>i>=126 && p[i-63]>0 && p[i-126]>0 ? 50*(c/p[i-63]+c/p[i-126]-2):null);
const score=blend(close);
const spy=land_points_onto_series(sp.time,blend(sp.close),time,'eq');
const qqq=land_points_onto_series(qq.time,blend(qq.close),time,'eq');
const ma=sma(close,200),dv=sma(mult(close,volume),20);
const moment=library('moment-timezone');
const now=Date.now()/1000;
const complete=time.map(t=>now>=moment.unix(t).tz('America/New_York').hour(16).minute(35).second(0).unix());
const ready=close.map((c,i)=>complete[i] && i>=199 && [c,ma[i],dv[i],score[i],spy[i],qqq[i]].every(valid));
const green=close.map((c,i)=>ready[i] && c>ma[i] && dv[i]>=20000000 && score[i]>spy[i] && score[i]>qqq[i]);
let j=time.length-1;while(j>=0 && !complete[j])j--;
let age=null;
if(j>=205 && ready[j]) {
    for(let k=j-4;k<=j;k++)if(ready[k-1] && green[k] && !green[k-1])age=j-k;
}
const lastComplete=times=>times.filter(t=>now>=moment.unix(t).tz('America/New_York').hour(16).minute(35).second(0).unix()).slice(-1)[0];
const available=j>=205 && ready[j] && ready[j-1] && time[j]===lastComplete(sp.time) && time[j]===lastComplete(qq.time);
const rising=available && score[j]>score[j-1];
const review=available && green[j] && (rising || age!==null);
const nearOnly=input.boolean('Near zero and rising only',true);
const selected=review && (!nearOnly || (rising && Math.abs(score[j])<=5));
const snapshot=v=>{const a=series_of(null);a[a.length-1]=available?v:null;return a;};
paint(snapshot(selected?1:0),{name:'ReviewNow',color:'#16a085'});
paint(snapshot(review && age!==null?1:0),{name:'NewGreenNow',hidden:true});
paint(snapshot(review && age===null && rising?1:0),{name:'EstablishedRisingNow',hidden:true});
paint(snapshot(review && Math.abs(score[j])<=5?1:0),{name:'NearZeroNow',hidden:true});
paint(snapshot(available?score[j]:null),{name:'MomentumPct',hidden:true});
register_signal(snapshot(selected),'Attention review now - NOT BUY');
register_signal(snapshot(review && rising && Math.abs(score[j])<=5),'Near zero and rising - REVIEW');
