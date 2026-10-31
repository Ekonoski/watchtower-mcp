"""Frozen retention-buffer engine; extracted from the audited 2026-10-31 study.

Only operational additions: optional terminal liquidation suppression, open-only
last bar, and returning state. Selection and trade accounting are unchanged.
"""
import math
from collections import Counter
import numpy as np
import pandas as pd
from .universe import OLD

def rank_value(a,i,horizon):
    if horizon=='skipblend':return .5*(a['ret126skip21'][i]+a['ret252skip21'][i])
    return .5*(a['ret63'][i]+a['ret126'][i]) if horizon=='blend' else a['ret'+str(horizon)][i]

def targets(data,i,cfg,excluded=()):
    spy=max(rank_value(data[s],i,cfg['horizon']) for s in cfg.get('relative_to',['SPY']));ranked=[]
    for s in OLD['universe']:
        if s in excluded:continue
        a=data[s];score=rank_value(a,i,cfg['horizon'])
        if a['c'][i]>a['sma200'][i] and a['dv20'][i]>=20000000 and score>spy and (not cfg.get('legacy') or a['c'][i]>a['ema20'][i]):
            ranked.append((-(score/a['vol63'][i] if cfg.get('quality_rank') else score),s))
    out=[];sectors=Counter()
    for score,s in sorted(ranked):
        sector=OLD['sectors'][s]
        if sectors[sector]>=cfg.get('sector_cap',3):continue
        sectors[sector]+=1;out.append(s)
        if len(out)==cfg.get('rank_limit',10):break
    return out

def events_for_day(day,divs,holdings,events):
    for d in divs.get(day,[]):
        units=holdings.get(d['symbol'],0.)
        if units>0:events.append({**d,'ex_date':day,'units':units,'amount':units*d['amount_per_unit'],'paid_date':None})

def pay_events(day,events):
    paid=[]
    for x in events:
        if x['paid_date'] is None and x['pay_date']<=day:x['paid_date']=day;paid.append(x)
    return paid

def simulate(cal,data,divs,cfg,dates,cost=.0005,delay=0,excluded=(),entry_filter=None,corporate_exits=None, liquidate=True, open_only=False):
    core_symbol=cfg.get('core_symbol','SPY')
    inds=[i for i,d in enumerate(cal) if dates[0]<=d<=dates[1]];cash=100000.;active={};trades=[];curve=[];events=[];legs=[];core_units=0.;core_basis=0.;core_pnl=0.;core_div=0.;skips=Counter()
    blocks=[(pd.Timestamp(d).isocalendar().year,pd.Timestamp(d).isocalendar().week,0 if cfg['cadence']==1 or pd.Timestamp(d).weekday()<3 else 1) for d in cal]
    def stock_close(s,i,price,reason,fee_override=None):
        nonlocal cash
        p=active.pop(s);credit=p['units']*price*(1-(cost if fee_override is None else fee_override));cash+=credit;price_pnl=credit-p['debit'];pnl=price_pnl+p['dividend_income']
        trades.append({**{k:v for k,v in p.items() if k not in ('due_i','exit_reason')},'exit_date':cal[i],'exit_price':float(price),'price_pnl':price_pnl,'pnl':pnl,'net_r':pnl/(p['units']*p['risk']),'return_pct':100*pnl/p['debit'],'hold_sessions':i-p['entry_i']+(1 if reason=='period_end' else 0),'reason':reason})
    def core_order(i,quantity,price,reason):
        nonlocal cash,core_units,core_basis,core_pnl
        if abs(quantity)*price<1e-7:return
        if quantity>0:
            q=min(quantity,max(0,cash)/(price*(1+cost)));flow=-q*price*(1+cost);core_units+=q;core_basis-=flow;pnl=0
        else:
            q=min(-quantity,core_units);basis=core_basis*q/core_units;flow=q*price*(1-cost);pnl=flow-basis;core_units-=q;core_basis-=basis;core_pnl+=pnl
        cash+=flow;legs.append({'date':cal[i],'symbol':core_symbol,'side':'buy' if quantity>0 else 'sell','units':q,'price':float(price),'cashflow':flow,'realized_price_pnl':pnl,'reason':reason})
    for i in inds:
        day=cal[i];before=len(events);events_for_day(day,divs,{**{s:p['units'] for s,p in active.items()},core_symbol:core_units},events)
        for x in events[before:]:
            if x['symbol']==core_symbol:core_div+=x['amount']
            else:active[x['symbol']]['dividend_income']+=x['amount']
        cash+=sum(x['amount'] for x in pay_events(day,events));receivable=sum(x['amount'] for x in events if x['paid_date'] is None)
        for action in (corporate_exits or {}).get(day,[]):
            s=action['symbol']
            if s in active:
                child_open=float(data[action['child']]['o'][i])
                assert np.isfinite(child_open) and child_open>0,('Missing merger consideration price',s,day)
                px=action['cash']+action['ratio']*child_open*(1-cost)
                stock_close(s,i,px,'corporate_consideration',fee_override=0)
                trades[-1]['corporate_action']={**action,'child_open':child_open,'child_sale_cost':cost}
        for s in active:
            assert np.isfinite(data[s]['o'][i]) and np.isfinite(data[s]['c'][i]),('Unresolved held price/terminal hole',s,day)
        si=i-1-delay;rebalance=blocks[i-delay]!=blocks[si];chosen=targets(data,si,cfg,excluded) if rebalance else []
        retained=targets(data,si,{**cfg,'rank_limit':cfg.get('retain_rank',10)},excluded) if rebalance else []
        core_allowed=not cfg.get('core_trend') or data[core_symbol]['c'][si]>data[core_symbol]['sma200'][si]
        if not core_allowed and core_units:core_order(i,-core_units,data[core_symbol]['o'][i],'core_trend_exit')
        for s,p in list(active.items()):
            reason=p.get('exit_reason') if p.get('due_i')==i else ('rank_exit' if rebalance and s not in retained else None)
            if reason:stock_close(s,i,data[s]['o'][i],reason)
        sizing=cash+receivable+core_units*data[core_symbol]['o'][i]+sum(p['units']*data[s]['o'][i] for s,p in active.items())
        if rebalance:
            for s in chosen:
                if s in active:continue
                if entry_filter is not None and not entry_filter(s,day):skips['missed_entry']+=1;continue
                sector=OLD['sectors'][s]
                if len(active)>=10:skips['position_cap']+=1;continue
                if sum(p['sector']==sector for p in active.values())>=cfg.get('sector_cap',3):skips['sector_cap']+=1;continue
                a=data[s];px=float(a['o'][i]);atr=float(a['atr'][si]);risk=3*atr
                if not np.isfinite(px) or not np.isfinite(risk):skips['untradable_or_missing_price']+=1;continue
                if px>a['c'][si]+atr:skips['gap_above_one_ATR']+=1;continue
                if px-risk<=0:skips['risk_geometry']+=1;continue
                desired=min(.10*sizing,cfg['risk_budget']*sizing/risk*px)*(1+cost)
                if cash<desired and core_units>0:core_order(i,-min(core_units,(desired-cash)/(data[core_symbol]['o'][i]*(1-cost))),data[core_symbol]['o'][i],'fund_stock_entry')
                debit=min(desired,cash)
                if debit<1:skips['cash']+=1;continue
                units=debit/(px*(1+cost));cash-=debit
                active[s]={'symbol':s,'sector':sector,'signal_date':cal[si],'entry_date':day,'entry_i':i,'entry_price':px,'units':units,'debit':debit,'risk':risk,'stop':px-risk,'sizing_equity':sizing,'planned_risk_dollars':units*risk,'dividend_income':0.}
        if cfg['reserve']!='cash' and core_allowed and cash>1e-7:core_order(i,cash/(data[core_symbol]['o'][i]*(1+cost)),data[core_symbol]['o'][i],'invest_residual_cash')
        for s,p in list(active.items()):
            a=data[s]
            if i==inds[-1] and liquidate:stock_close(s,i,a['c'][i],'period_end')
            elif 'due_i' not in p and not (open_only and i==inds[-1]):
                reason='close_stop' if a['c'][i]<=p['stop'] else ('close_below_sma50' if cfg.get('legacy') and a['c'][i]<a['sma50'][i] else None)
                if reason:p['due_i']=i+1+delay;p['exit_reason']=reason
        if i==inds[-1] and liquidate and core_units:core_order(i,-core_units,data[core_symbol]['c'][i],'period_end')
        sv=sum(p['units']*data[s]['c'][i] for s,p in active.items());cv=core_units*data[core_symbol]['c'][i];equity=cash+receivable+sv+cv
        assert cash>=-1e-6 and len(active)<=10
        curve.append({'date':day,'equity':equity,'cash':cash,'receivable':receivable,'stock_value':sv,'core_units':core_units,'core_value':cv,'positions':len(active),'exposure_pct':100*(sv+cv)/equity,'stock_exposure_pct':100*sv/equity,'core_exposure_pct':100*cv/equity})
    return {'trades':trades,'core_legs':legs,'dividend_events':events,'curve':curve,'skips':dict(skips),
            'active':active,'core_units':core_units,'core_basis':core_basis,'cash':cash}
