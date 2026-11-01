"""Typed scanner reference inputs. Missing provenance is not qualification."""
import math


def positive(value):
    try:
        number = float(value)
        return number if math.isfinite(number) and number > 0 else None
    except (ValueError, TypeError):
        return None


def float_reading(record, now, max_age_days=14):
    """Return source/type/age and a tri-state small-float check.

    Effective-date age is a warning, not a substitute for receipt freshness.
    Float measurements update as needed; age alone does not invalidate them.
    Callers must provide actual source publication/effective time, not scan time.
    """
    result = {**(record or {}), 'qualifies_under_20m':None, 'status':'unavailable'}
    if not record:
        result['reason']='No float reference';return result
    value=positive(record.get('value'));at=record.get('effective_at');seen=record.get('received_at')
    if not record.get('source') or not value or not isinstance(at,(float,int)) or not isinstance(seen,(float,int)):
        result['reason']='Value, source, effective time and receipt time required';return result
    if not math.isfinite(at) or not math.isfinite(seen) or at>now or seen>now:
        result['reason']='Reference unavailable at this observation time';return result
    checked=record.get('checked_at',seen)
    if not isinstance(checked,(int,float)) or not math.isfinite(checked) or checked>now:
        result['reason']='Verification was not available at this observation time';return result
    if record.get('metric')!='free_float':
        result['reason']='Shares outstanding or an unverified metric is not free float';return result
    result['effective_age_days']=(now-at)/86400
    result['receipt_age_seconds']=now-seen
    result['reported_under_20m']=value<20_000_000
    result['age_warning']='Effective date is older than 14 days; provider updates float as needed' if now-at>max_age_days*86400 else None
    if now-seen>86400:
        result['status']='review';result['reason']='Provider reference has not been refreshed in 24 hours';return result
    if record.get('conflict'):
        result['status']='review';result['reason']=record['conflict'];return result
    if record.get('review_reason'):
        result['status']='review';result['reason']=record['review_reason'];return result
    result.update(status='available',qualifies_under_20m=value<20_000_000,reason=None)
    return result


def rvol_reading(current, baseline, now):
    """Ratio only when numerator and baseline explicitly share a definition.

    A window-volume baseline is not daily-rate RVOL. Baseline generation and
    exact Warrior normalization are separate acceptance work.
    """
    result={'ratio':None,'percent':None,'status':'unavailable','current':current,'baseline':baseline}
    if not current or not baseline:
        result['reason']='Historical volume baseline or current window missing';return result
    for item in (current, baseline):
        if not item.get('source') or item.get('complete') is not True:
            result['reason']='Source or complete coverage missing';return result
        for key in ('as_of','received_at'):
            v=item.get(key)
            if not isinstance(v,(int,float)) or not math.isfinite(v) or v>now:
                result['reason']='Input was not available at observation time';return result
    for key in ('method','trade_scope','session_scope','window_seconds','adjustment_basis'):
        if current.get(key) is None or current.get(key)!=baseline.get(key):
            result['reason']='Incompatible volume definition: '+key;return result
    mean=positive(baseline.get('mean_volume'))
    value=current.get('volume')
    if not mean or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:
        result['reason']='Invalid volume or nonpositive baseline';return result
    if not baseline.get('history_end_session') or baseline['history_end_session']>=current.get('session_date',''):
        result['reason']='Baseline must end before the current session';return result
    if baseline.get('sample_sessions',0)<2:
        result['reason']='Insufficient baseline sessions';return result
    result.update(status='available',ratio=value/mean,percent=100*value/mean,reason=None,
                  warrior_equivalence='unverified')
    return result


class AggregateWindows:
    """Provider one-second aggregate volumes; revisions replace, not double count."""
    def __init__(self):
        self.reset()

    def reset(self):
        self.bars={}
        self.first_at=None
        self.cache={}
        self.receipts={}
        self.aligned_cache={}

    def ingest(self, event, now):
        start,end=event.get('s'),event.get('e')
        volume=event.get('v');ticker=event.get('sym')
        if (event.get('ev')!='A' or event.get('otc') or not isinstance(ticker,str)
            or not isinstance(start,(int,float)) or not isinstance(end,(int,float))
            or not math.isfinite(start) or not math.isfinite(end)
            or not isinstance(volume,(int,float)) or not math.isfinite(volume) or volume<0
            or not 0<=end-start<=1000 or start%1000!=0 or end/1000>now+2 or now-end/1000>60):
            return
        if self.first_at is None:self.first_at=start/1000
        bars=self.bars.setdefault(ticker,{})
        bars[start/1000]=(volume,end/1000)
        self.receipts[ticker]=now
        self.cache.pop(ticker,None)
        self.aligned_cache.pop(ticker,None)
        newest=max(bars)
        for key in list(bars):
            if key<newest-610:del bars[key]

    def reading(self,ticker,now,seconds=300):
        bars=self.bars.get(ticker,{})
        if not bars:return {'volume':None,'status':'unavailable','as_of':None}
        cached=self.cache.get(ticker)
        if cached and cached['window_seconds']==seconds:
            return {**cached,'age_seconds':now-cached['as_of']}
        end=max(bars)+1
        complete=self.first_at is not None and end-seconds>=self.first_at
        result={'volume':sum(v[0] for t,v in bars.items() if end-seconds<=t<end) if complete else None,
                'status':'available' if complete else 'warming', 'as_of':end,
                'age_seconds':now-end,'window_seconds':seconds,
                'source':'Massive A.*', 'trade_scope':'provider_eligible_aggregate_trades',
                'method':'trailing_aggregate_window','complete':complete,
                'note':'Observed subscription window; not historical RVOL. Connection gaps reset warmup.'}
        self.cache[ticker]=result
        return result.copy()

    def aligned_reading(self,ticker,now):
        """Last completed clock-minute's five minutes, for minute-history comparison.

        Uses the symbol's last aggregate, not wall time, so a silent/disconnected
        feed cannot create fresh-looking zero volume. Sparse symbols can be stale.
        """
        bars=self.bars.get(ticker,{})
        if not bars:return None
        if ticker in self.aligned_cache:
            cached=self.aligned_cache[ticker]
            return {**cached,'age_seconds':now-cached['as_of']}
        end=(int(max(bars))+1)//60*60
        complete=self.first_at is not None and end-300>=self.first_at
        from datetime import datetime
        from zoneinfo import ZoneInfo
        result={'volume':sum(v[0] for t,v in bars.items() if end-300<=t<end) if complete else None,
            'complete':complete,'as_of':end,'received_at':self.receipts[ticker],
            'age_seconds':now-end,'window_seconds':300,'source':'Massive A.*',
            'trade_scope':'provider_eligible_aggregate_trades','session_scope':'extended_04_20_ET',
            'session_date':datetime.fromtimestamp(end,ZoneInfo('America/New_York')).date().isoformat(),
            'adjustment_basis':'unadjusted_no_splits_in_window','method':'same_clock_5m_14_sessions'}
        self.aligned_cache[ticker]=result
        return result.copy()
