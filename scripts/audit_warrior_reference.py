"""Normalize a saved Warrior export and inventory its actual alert families.

No vendor requests, credentials, chart changes, or inferred first-entry times.
"""
import argparse
from datetime import datetime
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo


def number(text):
    value = str(text).replace(',', '').strip()
    multiplier = {'K': 1e3, 'M': 1e6, 'B': 1e9}.get(value[-1:].upper(), 1)
    try:
        result = float(value[:-1] if multiplier != 1 else value)*multiplier
        return result if math.isfinite(result) else None
    except ValueError:
        return None


def audit(path):
    raw = json.loads(path.read_text(encoding='utf-8'))
    observations, problems, dedup = [], [], set()
    by_family = {}
    for i, record in enumerate(raw['rows']):
        c = record.get('cells', [])
        if len(c) != 11:
            problems.append({'row': i, 'reason': 'Expected 11 columns', 'cells': c})
            continue
        try:
            when = datetime.strptime(raw['date']+' '+c[0].splitlines()[0].strip(), '%Y-%m-%d %I:%M:%S %p').replace(tzinfo=ZoneInfo('America/New_York'))
        except ValueError:
            problems.append({'row': i, 'reason': 'Unparseable displayed time', 'value': c[0]})
            continue
        signature = tuple(c)
        if signature in dedup:
            continue
        dedup.add(signature)
        row = {'session':raw['date'], 'ticker':c[1], 'family':c[10],
               'observed_at':when.isoformat(), 'clock_id':None, 'live':False,
               'evidence':'archive_group', 'source_ref':str(path.resolve())+'#row='+str(i),
               'displayed_time_and_group':c[0], 'first_child_at':None,
               'price':number(c[2]), 'volume':number(c[3]), 'displayed_float':number(c[4]),
               'daily_rate_rvol':number(c[5]), 'five_minute_rvol_percent':number(c[6]),
               'gap_pct':number(c[7]), 'change_pct':number(c[8]),
               'expandable':record.get('expandable'), 'news_available_at_alert':None}
        observations.append(row)
        stats = by_family.setdefault(c[10], {'displayed_rows':0,'symbols':set(),
            'outside_preferred_3_8':0,'daily_rate_below_5':0,'daily_rate_unknown':0})
        stats['displayed_rows'] += 1
        stats['symbols'].add(c[1])
        stats['outside_preferred_3_8'] += row['price'] is not None and not 3 <= row['price'] <= 8
        stats['daily_rate_below_5'] += row['daily_rate_rvol'] is not None and row['daily_rate_rvol'] < 5
        stats['daily_rate_unknown'] += row['daily_rate_rvol'] is None
    for stats in by_family.values():
        stats['symbols'] = sorted(stats['symbols'])
    return {'source':str(path.resolve()),'captured_at':raw.get('capturedAt'),
            'coverage_note':raw.get('coverage') or raw.get('coverageWarning'),
            'scope':'Bounded historical displayed rows, not all alerts or contemporaneous receipt times. News at alert and first child times unknown.',
            'coverage':[], 'observations':observations,
            'summary':{'raw_rows':len(raw['rows']), 'normalized_rows':len(observations),
                       'duplicate_rows':len(raw['rows'])-len(observations)-len(problems),
                       'parse_errors':len(problems), 'families':by_family}, 'errors':problems}


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('source',type=Path);parser.add_argument('output',type=Path)
    args=parser.parse_args()
    result=audit(args.source)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result['summary'],indent=2))
