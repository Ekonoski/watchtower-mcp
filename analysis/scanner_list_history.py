"""Sample the same scanner views as the UI, independently of browser filters.

One-second target cadence is observation, not exact entry/display latency.
Checkpoint all ranks/row context every 30 seconds; membership events between.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

ET=ZoneInfo('America/New_York')
LIST_RULES='scanner-focus-v1-top100-sampled1s'
FAMILIES=('leaders','fresh','focus','volume')


class ListHistory:
    def __init__(self, record):
        self.record=record
        self.scope=None
        self.previous={}
        self.seen=set()
        self.last_sample=None
        self.last_checkpoint=None

    def capture(self, snapshot, now, generation, stream_state):
        session=datetime.fromtimestamp(now,ET).date().isoformat()
        scope=(session,generation)
        reset=scope!=self.scope
        previous_at=None if reset else self.last_sample
        gap=previous_at is not None and now-previous_at>2.5
        if reset:
            self.previous={};self.seen=set();self.last_checkpoint=None
            self.scope=scope
        common={'list_rules':LIST_RULES,'stream_state':stream_state,
            'scope':list(scope),'previous_sample_at':previous_at,'sample_at':now,
            'sample_gap_seconds':now-previous_at if previous_at is not None else None,
            'coverage_gap':gap,'market_session':snapshot['session_date'],
            'tracked_count':snapshot['tracked_count'],'warm_seconds':snapshot['warm_seconds'],
            'source_market_at':snapshot['market_at'],'source_received_at':snapshot['received_at'],
            'limit_per_list':100,'browser_filters_applied':False}
        views={};pool={}
        for band, group in snapshot['scanner'].items():
            views[band]={'warming':group['warming'],'missing_day_reference':group['missing_day_reference']}
            for family in FAMILIES:
                view=group[family];rows=view['rows'];key=(band,family)
                current={row['ticker']:{**row,'rank':i+1} for i,row in enumerate(rows)}
                previous=self.previous.get(key,{})
                entered=[];left=[]
                for ticker,row in current.items():
                    pool.setdefault(ticker,{}).update({k:v for k,v in row.items() if k!='rank'})
                    if ticker not in previous:
                        occurrence=(band,family,ticker)
                        arrival=('baseline' if reset else 'after_gap' if gap else
                                 'returning' if occurrence in self.seen else 'new_in_observed_scope')
                        entered.append({'ticker':ticker,'arrival':arrival,'row':row})
                        self.seen.add(occurrence)
                for ticker,row in previous.items():
                    if ticker not in current:
                        left.append({'ticker':ticker,'last_observed_row':row,
                            'reason':'No longer in sampled top 100: eligibility, freshness, band or rank; not inferred as trade failure'})
                views[band][family]={'tickers':[row['ticker'] for row in rows],
                    'total':view['total'],'omitted':view['omitted']}
                if (entered or left) and not reset:
                    self.record('list_membership',{**common,'band':band,'family':family,
                        'entered':entered,'left':left,'ranked_tickers':views[band][family]['tickers'],
                        'total':view['total'],'omitted':view['omitted']},now)
                self.previous[key]=current
        if reset or gap or self.last_checkpoint is None or now-self.last_checkpoint>=30:
            self.record('list_snapshot',{**common,'baseline':reset,'views':views,'rows_by_ticker':pool,
                'rank_note':'Ticker array order is rank, starting at 1. Rank-only changes are checkpointed every 30 seconds.',
                'timing_note':'New membership observed between samples; initial/reconnect baseline does not establish first market appearance.'},now)
            self.last_checkpoint=now
        self.last_sample=now
