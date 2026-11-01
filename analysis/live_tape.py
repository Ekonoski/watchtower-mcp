"""Single-process, opt-in stock trade stream for the discretionary trading UI.

No orders, automatic entries, database writes, or reinterpretation of old scans.
Run one owning process per Massive stock connection allowance. A second owner
can disconnect the first; enabling this is an explicit operational decision.
"""
import asyncio
import json
import logging
import math
import os
import time
from collections import deque
from bisect import bisect_right
from datetime import datetime
from zoneinfo import ZoneInfo
from analysis.scanner_focus import ScannerFocus
from analysis.scanner_inputs import AggregateWindows, float_reading, rvol_reading
from analysis.scanner_reference_data import Providers, ReferenceLoader, baseline_for

ET = ZoneInfo('America/New_York')
log = logging.getLogger(__name__)


def finite(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


class TapeBook:
    """Builds causal observed-trade windows; unknown windows stay unknown."""
    def __init__(self):
        self.rows = {}
        self.history = {}
        self.previous_close = {}
        self.alerts = deque(maxlen=150)
        self.lags = deque(maxlen=2000)
        self.last_message_at = None
        self.last_event_at = None
        self.session = None
        self.version = 0
        self.invalid = 0
        self.out_of_order = 0
        self.dropped_symbols = 0
        self.alert_total = 0
        self.warm_since = None
        self.cooled = {}
        self.trade_sequence = {}
        self.filtered_conditions = 0
        self.volume_state = {}
        self.focus = ScannerFocus()
        self.aggregate_windows = AggregateWindows()
        self.observed_highs = {}
        self.high_references = {}
        self.float_references = {}
        self.volume_profiles = {}
        self.reference_errors = {}

    def apply_references(self,result):
        ticker=result['ticker']
        self.float_references[ticker]=result.get('float')
        self.volume_profiles[ticker]=result.get('history')
        self.reference_errors[ticker]=result.get('errors',{})

    def reference_context(self,ticker,now):
        current=self.aggregate_windows.aligned_reading(ticker,now)
        baseline=baseline_for(self.volume_profiles.get(ticker),current['as_of']) if current else None
        rv=rvol_reading(current,baseline,now)
        if current and now-current['as_of']>75:
            rv.update(status='unavailable',ratio=None,percent=None,reason='Completed-minute volume window is stale (>75 seconds)')
        return {'float_reference':float_reading(self.float_references.get(ticker),now),
                'rvol_5m':rv,'reference_errors':dict(self.reference_errors.get(ticker,{})),
                'inputs_observed_at':now}

    def reset_windows(self):
        # A disconnect is a history hole, not a quiet market.
        self.history.clear()
        self.cooled.clear()
        self.warm_since = None
        self.trade_sequence.clear()
        self.focus.reset()
        self.aggregate_windows.reset()
        self.observed_highs.clear()
        for row in self.rows.values():
            row.update(move_1m=None,move_5m=None,move_10m=None)

    def ingest(self, event, received_at=None):
        now = time.time() if received_at is None else received_at
        if event.get('ev') == 'A':
            self.aggregate_windows.ingest(event,now)
            # Slow aggregates supply cumulative volume only. They must never
            # overwrite the faster trade price or its timestamp.
            stamp=finite(event.get('e'));symbol=event.get('sym');volume=finite(event.get('av'))
            old=self.volume_state.get(symbol,{}) if isinstance(symbol,str) else {}
            if (isinstance(symbol,str) and 0<len(symbol)<=16 and stamp
                and stamp/1000<=now+2 and now-stamp/1000<=60
                and volume is not None and volume>=0 and not event.get('otc')
                and stamp/1000>=old.get('market_at',0)):
                self.volume_state[symbol]={'value':volume,'market_at':stamp/1000}
            return False
        if event.get('ev') != 'T' or event.get('x') == 62:
            return False
        # Explicit display subset, not SIP official-last reconstruction.
        # Includes timely extended-hours Form T and odd lots; excludes late,
        # average-price, cancelled, corrected and unknown-condition prints.
        condition_list=event.get('c') or [0]
        if not isinstance(condition_list,list) or not all(isinstance(c,int) for c in condition_list):
            self.invalid+=1
            return False
        codes=set(condition_list)
        if not codes.issubset({0,12,14,37,41}):
            self.filtered_conditions+=1
            return False
        symbol=event.get('sym');sequence=finite(event.get('q'))
        stamp=finite(event.get('t'))
        participant=finite(event.get('pt'))
        if participant and stamp and stamp-participant>2000:
            self.filtered_conditions+=1
            return False
        size=finite(event.get('ds',event.get('s')))
        if (sequence is None or not isinstance(symbol,str) or not 0<len(symbol)<=16
            or not stamp or stamp/1000>now+2 or now-stamp/1000>60
            or size is None or size<=0):
            self.invalid+=1
            return False
        day=datetime.fromtimestamp(stamp/1000,ET).date().isoformat()
        if self.session and day<self.session:
            self.out_of_order+=1
            return False
        if day==self.session and sequence<=self.trade_sequence.get(symbol,-1):
            self.out_of_order+=1
            return False
        accepted=self._ingest_price({'ev':'A','sym':symbol,'e':stamp,
            'c':event.get('p'),'o':event.get('p'),'h':event.get('p'),'l':event.get('p'),
            'v':event.get('ds',event.get('s'))},now)
        if accepted:
            self.trade_sequence[symbol]=sequence
            self.rows[symbol]['conditions']=sorted(codes)
            self.rows[symbol]['price_kind']='Observed trade (includes odd lots / extended hours)'
        return accepted

    def _ingest_price(self, event, received_at=None):
        now = time.time() if received_at is None else received_at
        if event.get('ev') != 'A' or event.get('otc'):
            return False
        symbol = event.get('sym')
        price, stamp = finite(event.get('c')), finite(event.get('e'))
        if not isinstance(symbol, str) or not symbol or len(symbol) > 16 or not price or price <= 0 or not stamp:
            self.invalid += 1
            return False
        market = stamp / 1000
        if market > now + 2 or now - market > 60:
            # Do not admit a delayed feed into the current-move list.
            self.invalid += 1
            return False
        day = datetime.fromtimestamp(market, ET).date().isoformat()
        if self.session != day:
            self.rows.clear()
            self.reset_windows()
            self.alerts.clear()
            self.session = day
        old = self.rows.get(symbol)
        if old and market < old['market_at']:
            self.out_of_order += 1
            return False
        if not old and len(self.rows) >= 15000:
            self.dropped_symbols += 1
            return False
        self.last_message_at = now
        self.last_event_at = max(market, self.last_event_at or market)
        # Signed clock difference, not network latency. Clock skew is visible.
        self.lags.append((now-market)*1000)
        if self.warm_since is None:
            self.warm_since = market
        h = self.history.setdefault(symbol, deque())
        bar=(market, price, finite(event.get('h')) or price,
             finite(event.get('l')) or price, finite(event.get('o')) or price, finite(event.get('v')))
        # Coalesce intra-second history, but expose the latest price immediately.
        # Keep the FIRST event's open; timestamps remain actual last events.
        if h and int(h[-1][0])==int(market):
            last=h[-1];h[-1]=(market,price,max(last[2],bar[2]),min(last[3],bar[3]),last[4],(last[5] or 0)+(bar[5] or 0))
        else:h.append(bar)
        while len(h)>1 and h[1][0] < market-610:
            h.popleft()
        def move(seconds):
            if market-self.warm_since < seconds:
                return None
            # A reference must be known by the start of the window. Do not
            # turn an unobserved / inactive window into a zero percent move.
            index=bisect_right(h,market-seconds,key=lambda x:x[0])-1
            ref=h[index] if index>=0 else None
            if ref is None or market-seconds-ref[0] > 15:
                return None
            return (price/ref[1]-1)*100
        prev = self.previous_close.get(symbol)
        old_high=self.observed_highs.get(symbol)
        ref=self.high_references.get(symbol) or {}
        # A later snapshot cannot establish a breakout on an earlier trade.
        ref_high=ref.get('high') if ref.get('session')==day and ref.get('as_of',float('inf'))<=market else None
        known_before=max([v for v in (old_high,ref_high) if v is not None],default=None)
        self.observed_highs[symbol]=max(price,old_high or price)
        row={'ticker':symbol,'price':price,'market_at':market,'received_at':now,
             'chg_pct':(price/prev-1)*100 if prev and prev>0 else None,
             'volume':None,'volume_second':h[-1][5],
             'move_1m':move(60),'move_5m':move(300),'move_10m':move(600),
             'known_high':max(price,known_before or price),
             'new_known_high':price>known_before if known_before is not None else None,
             'high_reference_at':ref.get('as_of') if ref_high is not None else None,
             'hod_coverage':'unverified: observed trades and any dated provider daily high; not a qualified HOD alert'}
        self.rows[symbol]=row
        self.focus.observe(row)
        for seconds,field,threshold in ((300,'move_5m',5),(600,'move_10m',10)):
            change=row[field]
            if change is None:
                continue
            side='up' if change>=threshold else 'down' if change<=-threshold else None
            previous=old.get(field) if old else None
            crossed=previous is None or (side=='up' and previous<threshold) or (side=='down' and previous>-threshold)
            key=(symbol,seconds,side)
            if side and crossed and market-self.cooled.get(key,0)>=60:
                self.cooled[key]=market
                self.alert_total+=1
                self.alerts.appendleft({'id':self.alert_total,'ticker':symbol,'direction':side,
                    'label':f'{threshold}% / {seconds//60}m','move_pct':change,
                    'price':price,'market_at':market,'received_at':now,
                    'reference_context':self.reference_context(symbol,now)})
        self.version+=1
        return True

    def snapshot(self, now=None, selected=None):
        now=time.time() if now is None else now
        active=[r for r in self.rows.values() if now-r['market_at']<=15]
        active.sort(key=lambda r:(abs(r['move_1m']) if r['move_1m'] is not None else -1,
                                  r['volume_second'] or 0),reverse=True)
        lags=sorted(self.lags)
        enriched={}
        def with_volume(r):
            if r['ticker'] in enriched:return enriched[r['ticker']]
            vol=self.volume_state.get(r['ticker']) or {}
            if not vol or datetime.fromtimestamp(vol['market_at'],ET).date().isoformat()!=self.session:
                vol={}
            result={**r,'volume':vol.get('value'),'volume_market_at':vol.get('market_at'),
                    'volume_5m':self.aggregate_windows.reading(r['ticker'],now),
                    **self.reference_context(r['ticker'],now)}
            enriched[r['ticker']]=result
            return result
        selected_row=self.rows.get(selected)
        scanner = self.focus.views([with_volume(r) for r in self.rows.values()], now)
        return {'version':self.version,'sent_at':now,'session_date':self.session,
                'scanner': scanner,
                'received_at':self.last_message_at,'market_at':self.last_event_at,
                'rows':[with_volume(r) for r in active[:100]],'active_count':len(active),'omitted_count':max(0,len(active)-100),
                'tracked_count':len(self.rows),'alerts':list(self.alerts),
                'alerts_omitted':max(0,self.alert_total-len(self.alerts)),
                'lag_p50_ms':lags[len(lags)//2] if lags else None,
                'lag_p95_ms':lags[min(len(lags)-1,int(len(lags)*.95))] if lags else None,
                'lag_sample_count':len(lags),'invalid_events':self.invalid,
                'out_of_order':self.out_of_order,'dropped_symbols':self.dropped_symbols,'filtered_conditions':self.filtered_conditions,
                'warm_seconds':max(0,now-self.warm_since) if self.warm_since else 0,
                'selected':with_volume(selected_row) if selected_row else None,'history':list(self.history.get(selected,[]))}


class LiveTape:
    def __init__(self):
        self.book=TapeBook()
        self.task=None
        self.state='disabled'
        self.reason='Streaming is not enabled. Saved scans are not a live substitute.'
        self.reference_status='unavailable'
        self.reference_at=None
        self.reference_task=None
        self.scanner_reference_task=None
        self.float_catalog_task=None
        self.float_catalog_status={'status':'not started'}
        self.float_catalog_attempt_at=None
        self.scanner_loader=None
        self.selected=None
        self.news_context=None
        self.news_task=None

    def enabled(self):
        return os.getenv('WATCHTOWER_LIVE_TAPE','').lower() in ('1','true','yes')

    async def ensure_started(self):
        if self.enabled() and self.task is None:
            self.task=asyncio.create_task(self._run())

    async def seed_references(self, key):
        import requests
        def fetch():
            r=requests.get('https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers',
                           headers={'Authorization':'Bearer '+key},timeout=25)
            r.raise_for_status()
            return r.json().get('tickers',[])
        try:
            rows=await asyncio.to_thread(fetch)
            prev={r['ticker']:finite((r.get('prevDay') or {}).get('c')) for r in rows if r.get('ticker')}
            self.book.previous_close={k:v for k,v in prev.items() if v and v>0}
            # The snapshot update timestamp is not the time its high printed.
            # Keep it as a dated reference, never backdate a new-high signal.
            for row in rows:
                high=finite((row.get('day') or {}).get('h'));updated=finite(row.get('updated'))
                if row.get('ticker') and high and high>0 and updated:
                    at=updated/1e9  # Full-market snapshot updated is Unix ns.
                    if 0<=time.time()-at<=86400:
                        self.book.high_references[row['ticker']]={'high':high,'as_of':at,
                            'session':datetime.fromtimestamp(at,ET).date().isoformat()}
            self.reference_status='available'
            self.reference_at=time.time()
        except Exception as exc:
            detail=(type(exc).__name__+': '+str(exc)).replace(key,'[REDACTED]')
            log.warning('Live tape reference fetch failed: %s',detail)
            self.reference_status='unavailable: '+detail

    async def _run(self):
        try:
            await self._stream()
        finally:
            for task in (self.reference_task,self.scanner_reference_task,self.float_catalog_task,self.news_task):
                if task and not task.done():
                    task.cancel()
                    try:await task
                    except asyncio.CancelledError:pass

    async def load_selected_news(self):
        while self.enabled():
            ticker=self.selected
            if ticker and time.time()-self.news_context.rows.get(ticker,{}).get('checked_at',0)>=60:
                try:await asyncio.to_thread(self.news_context.fetch,ticker)
                except Exception as exc:log.warning('Selected-symbol news unavailable: %s',exc)
            await asyncio.sleep(1)

    async def seed_float_catalog(self):
        self.float_catalog_attempt_at=time.time()
        self.float_catalog_status={'status':'loading'}
        try:
            result=await asyncio.to_thread(self.scanner_loader.provider.load_float_catalog)
            persistence=await asyncio.to_thread(self.scanner_loader.persist_catalog,result)
            for ticker,record in result['records'].items():
                old=self.book.float_references.get(ticker)
                if (not old or old.get('catalog_only') or
                    record['effective_at']>old.get('effective_at',0) or
                    result['received_at']-old.get('received_at',0)>86400):
                    self.book.float_references[ticker]=record
            self.float_catalog_status={'status':'available','received_at':result['received_at'],
                'record_count':len(result['records']),'invalid_count':len(result['invalid']),
                'invalid':result['invalid'],'persistence':persistence,
                'scope':'Provider catalog; corporate-action checks pending per stock'}
        except Exception as exc:
            self.float_catalog_status={'status':'unavailable','reason':str(exc)}
            log.warning('Float catalog load failed: %s',exc)

    async def load_scanner_references(self):
        loader=self.scanner_loader
        while self.enabled():
            now=time.time();target=datetime.fromtimestamp(now,ET).date().isoformat()
            catalog_day=datetime.fromtimestamp(loader.provider.float_catalog_at,ET).date().isoformat() if loader.provider.float_catalog_at else None
            if (catalog_day!=target and now-(self.float_catalog_attempt_at or 0)>1800
                and (self.float_catalog_task is None or self.float_catalog_task.done())):
                self.float_catalog_task=asyncio.create_task(self.seed_float_catalog())
            candidates=sorted((r for r in self.book.rows.values() if 0<=now-r['market_at']<=15 and 3<=r['price']<=20),
                              key=lambda r:r.get('chg_pct') or 0,reverse=True)
            names=([self.selected] if self.selected else [])+[r['ticker'] for r in candidates[:30]]
            loader.request(names,target,now)
            if not loader.pending:
                loader.status='idle';await asyncio.sleep(2);continue
            ticker=loader.pending.pop(0);loader.attempted[ticker]=now;loader.status='loading '+ticker
            try:
                result=await asyncio.to_thread(loader.fetch_one,ticker,target)
                loader.loaded[ticker]=result
                if result.get('errors'):loader.errors[ticker]=result['errors']
                else:loader.errors.pop(ticker,None)
                self.book.apply_references(result)
            except Exception as exc:
                loader.errors[ticker]={'loader':str(exc)}
                self.book.reference_errors[ticker]=loader.errors[ticker]
                log.warning('Scanner reference load failed for %s: %s',ticker,exc)
            await asyncio.sleep(1)

    async def _stream(self):
        import websockets
        key=os.getenv('POLYGON_API_KEY') or os.getenv('MASSIVE_API_KEY')
        if not key:
            self.state='unavailable';self.reason='Market-data credential unavailable.';return
        self.reference_task=asyncio.create_task(self.seed_references(key))
        self.scanner_loader=ReferenceLoader(Providers(key,os.getenv('FMP_API_KEY')),
            cache_dir=os.getenv('WATCHTOWER_SCANNER_REFERENCE_CACHE'))
        from analysis.scanner_news import NewsContext
        self.news_context=NewsContext(Providers(key,os.getenv('FMP_API_KEY')))
        self.news_task=asyncio.create_task(self.load_selected_news())
        self.scanner_reference_task=asyncio.create_task(self.load_scanner_references())
        backoff=2
        while self.enabled():
            self.state='connecting';self.reason='Connecting to real-time stock trades.'
            self.book.reset_windows()
            try:
                async with websockets.connect('wss://socket.massive.com/stocks',
                    ping_interval=20,ping_timeout=20,open_timeout=15,max_size=16*1024*1024,max_queue=32) as ws:
                    await ws.send(json.dumps({'action':'auth','params':key}))
                    subscribed=False
                    while self.enabled():
                        try:
                            raw=await asyncio.wait_for(ws.recv(),timeout=10)
                        except asyncio.TimeoutError:
                            self.state='quiet';self.reason='Connection open; no market messages in 10 seconds.'
                            continue
                        now=time.time()
                        for event in json.loads(raw):
                            if event.get('ev')=='status':
                                status=event.get('status','')
                                if status=='auth_success' and not subscribed:
                                    await ws.send(json.dumps({'action':'subscribe','params':'T.*,A.*'}))
                                    subscribed=True;self.state='warming';self.reason='Waiting for real-time trade events.'
                                elif status in ('auth_failed','not_authorized','max_connections','error') or 'not authorized' in str(event.get('message','')).lower():
                                    self.state='unavailable'
                                    self.reason=str(event.get('message') or status).replace(key,'[REDACTED]')
                                    return  # Never compete with another owning process.
                            elif self.book.ingest(event,now):
                                self.state='receiving';self.reason='Receiving raw stock trades; Warrior speed parity is not yet verified.'
                                backoff=2
                                if self.reference_at and datetime.fromtimestamp(self.reference_at,ET).date()!=datetime.fromtimestamp(now,ET).date() and self.reference_task.done():
                                    self.book.previous_close.clear()
                                    self.reference_task=asyncio.create_task(self.seed_references(key))
                        # A busy receive buffer must not starve browser frames.
                        await asyncio.sleep(0)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                detail=(type(exc).__name__+': '+str(exc)).replace(key,'[REDACTED]')
                log.warning('Stock stream disconnected: %s',detail)
                self.state='disconnected';self.reason='Stock stream disconnected: '+detail
            await asyncio.sleep(backoff)
            backoff=min(60,backoff*2)

    def snapshot(self, selected=None):
        self.selected=selected
        result=self.book.snapshot(selected=selected)
        result.update(state=self.state,reason=self.reason,enabled=self.enabled(),
                      selected_reference={'ticker':selected,**self.book.reference_context(selected,time.time())} if selected else None,
                      reference_status=self.reference_status,reference_at=self.reference_at,
                      scanner_reference_status=self.scanner_loader.diagnostics() if self.scanner_loader else {'status':'not started'},
                      float_catalog_status=self.float_catalog_status,
                      news=self.news_context.reading(selected,time.time()) if self.news_context and selected else {'status':'unavailable','articles':[]},
                      parity_status='unverified',source='Massive T.* prices; A.* cumulative volume',
                      level2_available=False,quote_available=False)
        return result


live_tape=LiveTape()
