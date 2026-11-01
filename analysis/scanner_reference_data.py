"""Read-only sourced float and Massive minute-history inputs for scanner comparison.

No legacy mixed float cache. Same-clock RVOL is explicitly our measurement,
not a claim that Warrior uses the same normalization.
"""
from datetime import datetime, date, timedelta, timezone
import json
import math
import re
import time
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.parse import urlsplit, urlunsplit, urlencode, parse_qsl
from urllib.error import HTTPError
from zoneinfo import ZoneInfo

ET=ZoneInfo('America/New_York')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        raise RuntimeError('Provider redirect refused')


class Providers:
    def __init__(self,massive_key,fmp_key=None):
        self.massive_key=massive_key
        self.fmp_key=fmp_key
        self.opener=build_opener(NoRedirect())
        self.float_catalog={}
        self.float_catalog_at=None

    def load_float_catalog(self):
        rows=self.pages('https://api.massive.com/stocks/vX/float?limit=5000&sort=ticker.asc')
        now=time.time();records={};invalid=[]
        for row in rows:
            ticker=row.get('ticker')
            try:
                if not isinstance(ticker,str) or not re.fullmatch(r'[A-Z0-9.\-]{1,16}',ticker):
                    raise ValueError('Invalid ticker')
                record=normalize_massive_float([row],ticker,now)
                record['review_reason']='Corporate-action and cross-provider checks pending'
                record['catalog_only']=True
                if ticker in records:raise ValueError('Duplicate ticker in float catalog')
                records[ticker]=record
            except ValueError as exc:invalid.append({'ticker':ticker,'reason':str(exc)})
        if not records:raise ValueError('Float catalog has no valid dated records')
        # Publish only after every page was received; a failed refresh keeps the prior catalog dated.
        self.float_catalog=records;self.float_catalog_at=now
        return {'records':records,'received_at':now,'provider_rows':len(rows),'invalid':invalid}

    def get(self,url,provider='massive'):
        host='api.massive.com' if provider=='massive' else 'financialmodelingprep.com'
        key=self.massive_key if provider=='massive' else self.fmp_key
        if not key:raise RuntimeError(provider+' credential unavailable')
        p=urlsplit(url)
        if p.scheme!='https' or p.hostname!=host or p.port not in (None,443) or p.username:
            raise ValueError('Unexpected provider destination')
        pairs=[(k,v) for k,v in parse_qsl(p.query) if k.lower()!='apikey']
        headers={}
        if provider=='fmp':pairs.append(('apikey',key))
        else:headers['Authorization']='Bearer '+key
        clean=urlunsplit((p.scheme,p.netloc,p.path,urlencode(pairs),''))
        try:
            with self.opener.open(Request(clean,headers=headers),timeout=30) as response:
                data=json.load(response)
        except HTTPError as exc:
            detail=exc.read().decode('utf-8',errors='replace').replace(key,'[REDACTED]')
            raise RuntimeError(f'{provider} HTTP {exc.code}: {detail}') from None
        except Exception as exc:
            raise RuntimeError(str(exc).replace(key,'[REDACTED]')) from None
        if isinstance(data,dict) and (data.get('status') in ('ERROR','NOT_AUTHORIZED') or 'Error Message' in data):
            raise RuntimeError(json.dumps(data).replace(key,'[REDACTED]'))
        return data

    def pages(self,url):
        results=[];seen=set()
        for _ in range(50):
            if url in seen:raise RuntimeError('Repeated pagination URL')
            seen.add(url);data=self.get(url)
            if not isinstance(data.get('results',[]),list):raise ValueError('Invalid results array')
            results.extend(data.get('results',[]))
            url=data.get('next_url')
            if not url:return results
        raise RuntimeError('Pagination cap reached; history incomplete')

    def sessions_before(self,target):
        day=date.fromisoformat(target)
        rows=self.pages(f'https://api.massive.com/v2/aggs/ticker/SPY/range/1/day/{day-timedelta(days=45)}/{day-timedelta(days=1)}?adjusted=false&sort=asc&limit=50000')
        sessions=sorted({datetime.fromtimestamp(r['t']/1000,ET).date().isoformat() for r in rows})
        sessions=[s for s in sessions if s<target][-14:]
        if len(sessions)!=14:raise RuntimeError('Fourteen prior market sessions unavailable')
        return sessions

    def load(self,ticker,target,sessions):
        if not re.fullmatch(r'[A-Z0-9.\-]{1,16}',ticker):raise ValueError('Invalid ticker')
        result={'ticker':ticker,'target_session':target,'float':None,'history':None,'errors':{}}
        try:
            result['float']=self.load_float(ticker)
        except Exception as exc:result['errors']['float']=str(exc)
        try:
            details=self.get(f'https://api.massive.com/v3/reference/tickers/{ticker}').get('results',{})
            listed=details.get('list_date')
            if not listed or listed>sessions[0]:raise RuntimeError('Listing history does not cover all 14 baseline sessions')
            splits=self.pages('https://api.massive.com/v3/reference/splits?'+urlencode({'ticker':ticker,'execution_date.gte':sessions[0],'execution_date.lte':target,'limit':1000}))
            if splits:raise RuntimeError('Split in baseline window; raw-share volumes not comparable')
            start=datetime.fromisoformat(sessions[0]).replace(hour=4,tzinfo=ET)
            end=datetime.fromisoformat(sessions[-1]).replace(hour=20,tzinfo=ET)
            bars=self.pages(f'https://api.massive.com/v2/aggs/ticker/{ticker}/range/1/minute/{int(start.timestamp()*1000)}/{int(end.timestamp()*1000)-1}?adjusted=false&sort=asc&limit=50000')
            result['history']=build_profile(bars,sessions,target,time.time())
        except Exception as exc:result['errors']['history']=str(exc)
        result['retrieved_at']=time.time()
        return result

    def load_float(self,ticker):
        if not re.fullmatch(r'[A-Z0-9.\-]{1,16}',ticker):raise ValueError('Invalid ticker')
        cached=self.float_catalog.get(ticker)
        if cached and self.float_catalog_at is not None and 0<=time.time()-self.float_catalog_at<86400:
            record={k:v for k,v in cached.items() if k not in ('review_reason','catalog_only')}
        else:
            rows=self.pages('https://api.massive.com/stocks/vX/float?'+urlencode({'ticker':ticker}))
            record=normalize_massive_float(rows,ticker,time.time())
        try:
            splits=self.pages('https://api.massive.com/v3/reference/splits?'+urlencode({
                'ticker':ticker,'execution_date.gt':record['effective_date_raw'],
                'execution_date.lte':datetime.now(ET).date().isoformat(),'limit':1000}))
            record['splits_after_effective_date']=splits
            record['corporate_actions_checked_at']=time.time()
            if splits:record['review_reason']='Split occurred after the float effective date; value is not automatically adjusted'
        except Exception as exc:
            record['review_reason']='Corporate-action verification unavailable: '+str(exc)
        # Cross-provider disagreement stays visible; do not select the smaller number.
        if self.fmp_key:
            try:
                other=self.get('https://financialmodelingprep.com/stable/shares-float?'+urlencode({'symbol':ticker}),'fmp')
                candidates=[r for r in other if r.get('symbol')==ticker and r.get('floatShares') is not None]
                if candidates:
                    row=max(candidates,key=lambda r:str(r.get('date','')))
                    value=float(row['floatShares'])
                    if not math.isfinite(value) or value<=0:raise ValueError('Invalid FMP float')
                    record['alternate']={'source':'FMP floatShares','value':value,'provider_date_raw':row.get('date'),
                        'filing_url':row.get('source'),'received_at':time.time(),
                        'date_note':'Provider timezone unspecified; not a verified publication timestamp'}
                    if abs(value-record['value'])/record['value']>.1:
                        record['conflict']='Provider float values differ by more than 10%; reconcile before qualifying'
                else:record['alternate_status']='FMP floatShares unavailable'
            except Exception as exc:record['alternate_status']='FMP cross-check unavailable: '+str(exc)
        record['checked_at']=time.time()
        return record


def normalize_massive_float(rows,ticker,received_at):
    parsed=[]
    for row in rows:
        if row.get('ticker')!=ticker:continue
        raw=row.get('effective_date')
        try:
            day=date.fromisoformat(raw)
            at=datetime.combine(day,datetime.min.time(),timezone.utc).timestamp()
            value=float(row['free_float'])
        except (ValueError,TypeError,KeyError):continue
        if not math.isfinite(value) or value<=0 or at>received_at:continue
        parsed.append({'value':value,'metric':'free_float','source':'Massive /stocks/vX/float free_float',
            'effective_at':at,'effective_date_raw':raw,'effective_precision':'day',
            'received_at':received_at,'verification':'Provider-reported; not independently reconciled to filings',
            'historical_availability':'Latest record only; usable no earlier than our receipt time'})
    if not parsed:raise ValueError('No valid dated Massive free_float record for ticker')
    return max(parsed,key=lambda r:r['effective_at'])


def build_profile(bars,sessions,target,received_at):
    if len(sessions)!=14 or len(set(sessions))!=14 or sessions!=sorted(sessions) or sessions[-1]>=target:
        raise ValueError('Require 14 ordered distinct prior sessions')
    totals={s:[0.0]*960 for s in sessions};seen={};sessions_with_data=set()
    for bar in bars:
        t=bar['t'];value=float(bar['v'])
        if not math.isfinite(value) or value<0 or t%60000:raise ValueError('Invalid minute bar')
        stamp=datetime.fromtimestamp(t/1000,ET);session=stamp.date().isoformat()
        minute=stamp.hour*60+stamp.minute-240
        if session not in totals or not 0<=minute<960:continue
        if t in seen:
            if seen[t]!=value:raise ValueError('Conflicting duplicate bar')
            continue
        seen[t]=value;totals[session][minute]=value;sessions_with_data.add(session)
    missing=sorted(set(sessions)-sessions_with_data)
    if missing:raise ValueError('Entire baseline sessions absent: '+','.join(missing))
    # Missing individual intervals in a fully paginated aggregate response represent no eligible trades.
    average=[sum(totals[s][i] for s in sessions)/14 for i in range(960)]
    prefix=[0.0]
    for value in average:prefix.append(prefix[-1]+value)
    return {'target_session':target,'sessions':sessions,'sample_sessions':14,'history_end_session':sessions[-1],
            'source':'Massive unadjusted minute aggregates','complete':True,'received_at':received_at,
            'as_of':datetime.fromisoformat(sessions[-1]).replace(hour=20,tzinfo=ET).timestamp(),
            'mean_minute_volume':average,'mean_cumulative_volume':prefix,
            'mean_daily_extended_volume':prefix[-1],
            'trade_scope':'provider_eligible_aggregate_trades','session_scope':'extended_04_20_ET',
            'adjustment_basis':'unadjusted_no_splits_in_window','bar_count':len(seen),
            'method':'same_clock_14_session_mean','warrior_equivalence':'unverified'}


def baseline_for(profile,end_epoch,seconds=300):
    if not profile:return None
    dt=datetime.fromtimestamp(end_epoch,ET);minute=dt.hour*60+dt.minute-240
    if dt.date().isoformat()!=profile['target_session'] or dt.second or not 5<=minute<=960:return None
    if seconds!=300:return None
    prefix=profile['mean_cumulative_volume']
    return {k:v for k,v in profile.items() if k not in ('mean_minute_volume','mean_cumulative_volume')}|{
        'mean_volume':prefix[minute]-prefix[minute-5],'window_seconds':300,
        'method':'same_clock_5m_14_sessions'}


class ReferenceLoader:
    """Bounded background loader. No requests on the trade-ingestion path.

    Successful results are reused for the target session; failures retry after
    30 minutes. Disk caching is optional and contains market data only.
    """
    def __init__(self,provider,cache_dir=None,max_symbols=200):
        self.provider=provider;self.cache_dir=cache_dir;self.max_symbols=max_symbols
        self.target=None;self.sessions=None;self.pending=[];self.loaded={};self.attempted={}
        self.errors={};self.deferred=set();self.status='idle'

    def request(self,tickers,target,now):
        if target!=self.target:
            self.target=target;self.sessions=None;self.pending=[];self.loaded={}
            self.attempted={};self.errors={};self.deferred=set()
        for ticker in tickers:
            if not re.fullmatch(r'[A-Z0-9.\-]{1,16}',ticker):continue
            if ticker in self.pending:continue
            result=self.loaded.get(ticker)
            if result and not result.get('errors'):continue
            if now-self.attempted.get(ticker,-1e20)<1800:continue
            if ticker not in self.attempted and len(set(self.attempted)|set(self.pending))>=self.max_symbols:
                self.deferred.add(ticker);continue
            self.pending.append(ticker)
        # A newly selected stock or a new leading gainer must not wait behind
        # yesterday's ordering of the same pending queue. Unselected work stays queued.
        priority={ticker:i for i,ticker in reversed(list(enumerate(tickers)))}
        self.pending.sort(key=lambda t:priority.get(t,len(priority)))

    def fetch_one(self,ticker,target):
        """Called in a worker thread; returns a result for main-loop application."""
        from pathlib import Path
        path=Path(self.cache_dir)/target/(ticker+'.json') if self.cache_dir else None
        if path and path.exists():
            try:
                cached=json.loads(path.read_text(encoding='utf-8'))
                if (cached.get('ticker')==ticker and cached.get('target_session')==target
                    and not cached.get('errors') and 0<=time.time()-cached['retrieved_at']<86400):return cached
            except (ValueError,KeyError,OSError):pass
        if self.sessions is None:self.sessions=self.provider.sessions_before(target)
        result=self.provider.load(ticker,target,self.sessions)
        if path:
            try:
                path.parent.mkdir(parents=True,exist_ok=True)
                temporary=path.with_suffix('.tmp')
                temporary.write_text(json.dumps(result),encoding='utf-8');temporary.replace(path)
            except OSError as exc:result['errors']['cache']='Market reference persistence failed: '+str(exc)
        return result

    def persist_catalog(self,result):
        """Keep receipt-stamped observations, never backdate to effective dates."""
        if not self.cache_dir:return {'status':'disabled','reason':'Reference cache directory is not configured'}
        from pathlib import Path
        day=datetime.fromtimestamp(result['received_at'],ET).date().isoformat()
        path=Path(self.cache_dir)/'float_catalog'/day/(str(int(result['received_at']*1_000_000))+'.json')
        try:
            path.parent.mkdir(parents=True,exist_ok=True)
            with path.open('x',encoding='utf-8') as handle:json.dump(result,handle)
            return {'status':'saved','received_at':result['received_at']}
        except OSError as exc:return {'status':'unavailable','reason':str(exc)}

    def diagnostics(self):
        return {'status':self.status,'target_session':self.target,'loaded_count':len(self.loaded),
            'pending_count':len(self.pending),'deferred_count':len(self.deferred),'symbol_cap':self.max_symbols,
            'failed_count':len(self.errors),'errors':dict(self.errors),
            'scope':'Selected symbol plus top 30 active $3–20 day gainers; references do not gate lists yet'}
