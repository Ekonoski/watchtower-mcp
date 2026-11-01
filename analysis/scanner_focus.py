"""Observed-stream discovery views, independent of entry signals and charts.

Thresholds are descriptive defaults, not Warrior rules or validated trades.
No news, float, or historical RVOL is inferred from price movement.
"""

PRICE_BANDS = {'preferred': (3, 8), 'broader': (3, 20), 'all': (0, float('inf'))}


class ScannerFocus:
    def __init__(self, hold_seconds=300):
        self.hold_seconds = hold_seconds
        self.attention = {}

    def reset(self):
        self.attention.clear()

    def observe(self, row):
        change, move = row.get('chg_pct'), row.get('move_1m')
        reasons = []
        if change is not None and change >= 10:
            reasons.append('day gain at least 10%')
        if move is not None and move >= 3:
            reasons.append('one-minute rise at least 3%')
        if reasons:
            ticker, stamp = row['ticker'], row['market_at']
            old = self.attention.get(ticker)
            first = old['first_seen'] if old and stamp-old['last_qualified'] <= self.hold_seconds else stamp
            self.attention[ticker] = dict(first_seen=first, last_qualified=stamp, reasons=reasons)

    def views(self, rows, now, limit=100):
        self.attention = {t: a for t, a in self.attention.items()
                          if 0 <= now-a['last_qualified'] <= self.hold_seconds}
        result = {}
        for band, (low, high) in PRICE_BANDS.items():
            eligible = [r for r in rows if low <= r['price'] <= high]
            active = [r for r in eligible if 0 <= now-r['market_at'] <= 15]
            leaders = sorted((r for r in active if r.get('chg_pct') is not None and r['chg_pct'] > 0),
                             key=lambda r: (-r['chg_pct'], r['ticker']))
            fresh = sorted((r for r in active if r.get('move_1m') is not None and r['move_1m'] >= 3),
                           key=lambda r: (-r['move_1m'], r['ticker']))
            volume = sorted((r for r in active if (r.get('volume_5m') or {}).get('volume') is not None
                             and 0 <= now-r['volume_5m']['as_of'] <= 15),
                            key=lambda r: (-r['volume_5m']['volume'],r['ticker']))
            focus = []
            for r in eligible:
                a = self.attention.get(r['ticker'])
                if a:
                    focus.append({**r, **a, 'hold_remaining_seconds': max(0, self.hold_seconds-(now-a['last_qualified'])),
                                  'stale': now-r['market_at'] > 15,
                                  'attention_state': 'holding' if r['market_at'] > a['last_qualified'] else 'qualified'})
            focus.sort(key=lambda r: (-r['first_seen'], r['ticker']))
            def capped(items):
                return {'rows': items[:limit], 'total': len(items), 'omitted': max(0, len(items)-limit)}
            result[band] = {'leaders': capped(leaders), 'fresh': capped(fresh), 'focus': capped(focus),
                            'volume': capped(volume),
                            'warming': sum(r.get('move_1m') is None for r in active),
                            'missing_day_reference': sum(r.get('chg_pct') is None for r in active)}
        return result
