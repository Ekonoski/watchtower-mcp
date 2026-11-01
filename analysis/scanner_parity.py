"""Compare first observed stock discovery, without inventing live latency.

Input observations: session (ET date), ticker, family, observed_at (aware ISO),
clock_id, live (bool), evidence (unaggregated_screen or archive_group), source_ref.
Coverage: family, session, start/end (aware ISO), complete (bool).
This comparison is first-discovery only, not repeated-alert equivalence or P&L.
"""
from datetime import datetime


def stamp(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('An explicit timezone is required')
    return result


def first_observations(records):
    result = {}
    for record in records:
        key = (record['session'], record['family'], record['ticker'])
        if not record.get('source_ref'):
            raise ValueError('Every observation needs a source reference')
        when = stamp(record['observed_at'])
        if key not in result or when < stamp(result[key]['observed_at']):
            result[key] = record
    return result


def covered(coverage, key, when):
    return any(c.get('complete') is True and c['session'] == key[0] and c['family'] == key[1]
               and stamp(c['start']) <= when <= stamp(c['end']) for c in coverage)


def compare_first_discovery(reference, candidate):
    def full_universe(bundle):
        accepted, excluded = [], []
        for r in bundle.get('observations', []):
            filters = r.get('filters')
            if filters is not None and filters != {'band':'all','query':'','direction':'all'}:
                excluded.append({'observation':r,'reason':'Filtered view excluded from full-universe comparison'})
            else:
                accepted.append(r)
        return accepted, excluded
    ref_records, ref_excluded = full_universe(reference)
    cand_records, cand_excluded = full_universe(candidate)
    left = first_observations(ref_records)
    right = first_observations(cand_records)
    rows = []
    for key in sorted(left.keys() | right.keys()):
        a, b = left.get(key), right.get(key)
        row = dict(session=key[0], family=key[1], ticker=key[2], reference=a,
                   candidate=b, display_delta_seconds=None)
        if a and b:
            row['status'] = 'both_observed'
            comparable = (a.get('live') is True and b.get('live') is True
                          and a.get('evidence') == b.get('evidence') == 'unaggregated_screen'
                          and a.get('clock_id') and a['clock_id'] == b.get('clock_id')
                          and covered(reference.get('coverage', []), key, stamp(b['observed_at']))
                          and covered(candidate.get('coverage', []), key, stamp(a['observed_at'])))
            if comparable:
                row['display_delta_seconds'] = (stamp(b['observed_at'])-stamp(a['observed_at'])).total_seconds()
                row['timing_status'] = 'comparable_first_observations'
            else:
                row['timing_status'] = 'unavailable: live ungrouped observations on a common clock with overlapping coverage required'
        elif a:
            row['status'] = ('reference_only_with_candidate_coverage' if
                             covered(candidate.get('coverage', []), key, stamp(a['observed_at']))
                             else 'candidate_coverage_unknown')
        else:
            row['status'] = ('candidate_only_with_reference_coverage' if
                             covered(reference.get('coverage', []), key, stamp(b['observed_at']))
                             else 'reference_coverage_unknown')
        rows.append(row)
    counts = {}
    for row in rows:
        counts[row['status']] = counts.get(row['status'], 0)+1
    deltas = sorted(r['display_delta_seconds'] for r in rows if r['display_delta_seconds'] is not None)
    return {'scope': 'First observed stock per ET session and alert family; not repeated alerts, trade outcomes, or exact rule equivalence.',
            'timing_sign': 'Positive seconds means Watchtower appeared later.',
            'counts': counts, 'comparable_timing_count': len(deltas),
            'timing_p50_seconds': deltas[len(deltas)//2] if deltas else None,
            'timing_worst_seconds': max(deltas) if deltas else None,
            'excluded_reference':ref_excluded,'excluded_candidate':cand_excluded,
            'rows': rows}
