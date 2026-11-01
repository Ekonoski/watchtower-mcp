"""Recent-runner watchlist; observation rules, not entries or Warrior parity."""
import asyncio
import os
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from analysis.scanner_focus import PRICE_BANDS

ET=ZoneInfo('America/New_York')
RULES='continuation-v1-5sessions-10pct-1mturnover'


def load_recent(target):
    from analysis.scanner_journal import connection
    conn=connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout='15s'")
            cur.execute("SELECT trade_date FROM daily_prices WHERE ticker='SPY' AND trade_date<%s ORDER BY trade_date DESC LIMIT 6",(target,))
            dates=[r[0] for r in cur.fetchall()]
            if len(dates)<6:raise RuntimeError('Five prior market sessions plus predecessor unavailable')
            sessions=dates[:5];pairs=list(zip(dates[:-1],dates[1:]))
            rows=[];coverage=[]
            for day,previous in pairs:
                cur.execute('SELECT count(*) FROM daily_prices WHERE trade_date=%s',(day,))
                coverage.append({'session':day.isoformat(),'daily_symbols':cur.fetchone()[0]})
                cur.execute("""SELECT a.ticker,a.high::float8,a.close::float8,a.volume::float8,
                    b.close::float8,a.created_at FROM daily_prices a JOIN daily_prices b
                    ON b.ticker=a.ticker AND b.trade_date=%s WHERE a.trade_date=%s
                    AND b.close>0 AND a.high>=b.close*1.1 AND a.close*a.volume>=1000000
                    ORDER BY a.ticker""",(previous,day))
                for ticker,high,close,volume,prior,created in cur.fetchall():
                    rows.append({'ticker':ticker,'runner_session':day.isoformat(),'peak_gain_pct':(high/prior-1)*100,
                        'last_close':close,'prior_close':prior,'daily_high':high,'daily_volume':volume,
                        'approx_turnover':close*volume,'source':'daily_prices cached daily bar',
                        'source_created_at':created.isoformat() if created else None,
                        'catalyst_status':'unverified','corporate_actions':'Not verified; check splits before interpreting the prior move'})
            # Preserve future premarket-only runners, even if they closed weak.
            cur.execute("""SELECT DISTINCT ON (session_date,payload->>'ticker') payload
                FROM scanner_journal WHERE kind='continuation_candidate' AND session_date=ANY(%s)
                ORDER BY session_date,payload->>'ticker',observed_at DESC,id DESC""",(sessions,))
            rows.extend(r[0] for r in cur.fetchall())
        return {'target_session':target,'sessions':[d.isoformat() for d in sessions],
                'coverage':coverage,'runners':rows,'source_note':'Cached daily universe plus recorded live candidates; instrument type, news, float and corporate actions are not admission gates. Missing intraday spikes or daily bars are not reconstructed.'}
    finally:conn.close()


class Continuations:
    def __init__(self, record):
        self.record=record;self.seeds={};self.status={'state':'loading'}
        self.target=None;self.previous={};self.scope=None;self.observed_today={}
        self.views={band:{'rows':[],'total':0,'omitted':0} for band in PRICE_BANDS}
        self.task=None;self.stopping=False
        self.last_sample=None

    def apply(self, result, now):
        seeds={}
        for row in result['runners']:
            if row['runner_session']>=result['target_session']:continue
            if row['runner_session'] not in result['sessions']:continue
            seeds.setdefault(row['ticker'],[]).append(row)
        self.seeds=seeds;self.target=result['target_session']
        self.status={'state':'available','loaded_at':now,'target_session':self.target,
            'sessions':result['sessions'],'coverage':result['coverage'],'runner_count':len(seeds),
            'rules':RULES,'note':result['source_note']}
        self.record('continuation_universe',{'status':self.status,'runners':seeds},now)

    async def refresh(self):
        import time
        while not self.stopping:
            target=datetime.fromtimestamp(time.time(),ET).date().isoformat()
            try:self.apply(await asyncio.to_thread(load_recent,target),time.time())
            except Exception as exc:
                detail=f'{type(exc).__name__}: {exc}'
                for name,value in os.environ.items():
                    if value and any(s in name for s in ('PASSWORD','TOKEN','KEY')):detail=detail.replace(value,'[REDACTED]')
                logging.getLogger(__name__).error('Continuation history unavailable: %s',detail)
                self.status={'state':'unavailable','reason':detail,
                    'last_target_session':self.target,'retained_runner_count':len(self.seeds)}
                self.record('continuation_error',self.status)
            # Wake on the new ET day; refresh cached references hourly otherwise.
            for _ in range(3600):
                if self.stopping or datetime.fromtimestamp(time.time(),ET).date().isoformat()!=target:break
                await asyncio.sleep(1)

    def sample(self, book, now):
        day=datetime.fromtimestamp(now,ET).date().isoformat()
        scope=(day,book.window_generation)
        baseline=scope!=self.scope
        gap=self.last_sample is not None and now-self.last_sample>2.5
        if baseline:self.previous={};self.scope=scope
        # Save each runner's observed peak in 5-percentage-point increments.
        # Today's candidates only enter the carried watchlist on a later session.
        for ticker,row in book.rows.items():
            if not 0<=now-row['market_at']<=15 or (row.get('chg_pct') or 0)<10:continue
            volume=book.volume_state.get(ticker,{})
            if (not volume or not 0<=now-volume['market_at']<=60
                or row['price']*volume['value']<1_000_000):continue
            key=(day,ticker);bucket=int(row['chg_pct']//5)
            if bucket<=self.observed_today.get(key,-1):continue
            self.observed_today[key]=bucket
            self.record('continuation_candidate',{'ticker':ticker,'runner_session':day,
                'peak_gain_pct':row['chg_pct'],'last_close':None,'observed_price':row['price'],
                'market_at':row['market_at'],'volume_market_at':volume['market_at'],
                'approx_turnover':row['price']*volume['value'],'source':'live observed trade and cumulative volume',
                'catalyst_status':'unverified','corporate_actions':'Not verified'},now)
        self.observed_today={k:v for k,v in self.observed_today.items() if k[0]==day}
        rows=[]
        valid=self.target==day and self.status.get('state')=='available'
        for ticker,seeds in self.seeds.items():
            seed=max(seeds,key=lambda s:s['runner_session'])
            raw=book.rows.get(ticker)
            current=raw is not None and 0<=now-raw['market_at']<=15
            row=book.with_volume(raw,now) if raw else {'ticker':ticker,'price':seed.get('last_close') or seed.get('observed_price'),
                'market_at':None,'received_at':None,'chg_pct':None,'move_1m':None,'volume':None}
            one=book.aggregate_windows.reading(ticker,now,60)
            five=book.aggregate_windows.reading(ticker,now,300)
            usable=lambda w:w.get('complete') is True and w.get('as_of') is not None and 0<=now-w['as_of']<=15 and w.get('volume') is not None
            ratio=(one['volume']/(five['volume']/5)) if usable(one) and usable(five) and five['volume']>0 else None
            volume_back=bool(one['volume']>=10000 and five['volume']>=25000 and ratio>=2) if ratio is not None else None
            momentum_back=(row['move_1m']>=3) if current and row.get('move_1m') is not None else None
            if not valid:volume_back=momentum_back=None
            state=('volume + momentum' if volume_back and momentum_back else 'momentum returning' if momentum_back else
                   'volume returning' if volume_back else 'waiting for data' if not current or volume_back is None or momentum_back is None else 'quiet / cooling')
            context={'state':state,'volume_returning':volume_back,'momentum_returning':momentum_back,
                'one_minute_volume':one,'five_minute_volume':five,'local_volume_pace_ratio':ratio,
                'volume_rule':'1m >=10k shares; 5m >=25k shares; 1m pace >=2x mean of same observed 5m; not historical RVOL',
                'momentum_rule':'+3% in observed 1m; price <=15s old','runner_session':seed['runner_session'],
                'prior_runs':seeds,'price_is_live':current,'rules':RULES,'universe_available':valid}
            context.update(sample_at=now,previous_sample_at=self.last_sample,coverage_gap=gap)
            row={**row,'continuation':context}
            previous=self.previous.get(ticker)
            flags=(volume_back,momentum_back)
            if previous!=flags:
                self.record('continuation_state',{'ticker':ticker,'baseline':baseline or previous is None,
                    'previous_flags':previous,'row':row,'scope':list(scope)},now)
            self.previous[ticker]=flags;rows.append(row)
        rows.sort(key=lambda r:(not (r['continuation']['volume_returning'] and r['continuation']['momentum_returning']),
            not bool(r['continuation']['momentum_returning']),not bool(r['continuation']['volume_returning']),
            -(r.get('move_1m') or 0),-datetime.fromisoformat(r['continuation']['runner_session']).toordinal(),
            -max(s['peak_gain_pct'] for s in r['continuation']['prior_runs']),r['ticker']))
        for band,(low,high) in PRICE_BANDS.items():
            selected=[r for r in rows if r.get('price') is not None and low<=r['price']<=high]
            self.views[band]={'rows':selected[:100],'total':len(selected),'omitted':max(0,len(selected)-100),
                'history_status':self.status}
        self.last_sample=now
