import unittest
import json
from pathlib import Path
from unittest.mock import patch
from analysis.scanner_parity import compare_first_discovery, first_observations
from scripts.audit_warrior_reference import audit


def record(ticker='TEST', family='Squeeze Alert - Up 5% in 5min', second=10, **kw):
    return dict(session='2026-11-02',ticker=ticker,family=family,
                observed_at=f'2026-11-02T07:00:{second:02d}-05:00',clock_id='paired-video-1',
                live=True,evidence='unaggregated_screen',source_ref='fixture',**kw)


def bundle(rows, complete=True):
    return {'observations':rows,'coverage':[dict(family='Squeeze Alert - Up 5% in 5min',
        session='2026-11-02',start='2026-11-02T07:00:00-05:00',end='2026-11-02T07:01:00-05:00',complete=complete)]}


class ParityTests(unittest.TestCase):
    def test_common_clock_records_delay_and_lead(self):
        for second, expected in ((12,2),(8,-2)):
            result=compare_first_discovery(bundle([record()]),bundle([record(second=second)]))
            self.assertEqual(result['rows'][0]['display_delta_seconds'],expected)

    def test_archive_never_claims_live_latency(self):
        archive=record();archive.update(live=False,evidence='archive_group')
        result=compare_first_discovery(bundle([archive]),bundle([record(second=12)]))
        self.assertIsNone(result['rows'][0]['display_delta_seconds'])
        self.assertEqual(result['comparable_timing_count'],0)

    def test_different_clock_is_not_comparable(self):
        other=record();other['clock_id']='other-computer'
        result=compare_first_discovery(bundle([record()]),bundle([other]))
        self.assertEqual(result['comparable_timing_count'],0)

    def test_gaps_are_not_misses(self):
        result=compare_first_discovery(bundle([record()]),bundle([],complete=False))
        self.assertEqual(result['rows'][0]['status'],'candidate_coverage_unknown')

    def test_coverage_allows_absence_to_be_reported(self):
        result=compare_first_discovery(bundle([record()]),bundle([]))
        self.assertEqual(result['rows'][0]['status'],'reference_only_with_candidate_coverage')

    def test_families_never_match_just_because_ticker_matches(self):
        result=compare_first_discovery(bundle([record()]),bundle([record(family='Ross 5 Pillars')]))
        self.assertEqual(len(result['rows']),2)
        self.assertNotIn('both_observed',result['counts'])

    def test_repeated_alerts_do_not_inflate_discovery(self):
        result=compare_first_discovery(bundle([record(),record(second=20)]),bundle([record(second=12)]))
        self.assertEqual(len(result['rows']),1)
        self.assertEqual(result['rows'][0]['display_delta_seconds'],2)

    def test_naive_timestamps_rejected(self):
        item=record();item['observed_at']='2026-11-02T07:00:10'
        with self.assertRaises(ValueError):first_observations([item])

    def test_missing_source_rejected(self):
        item=record();item['source_ref']=''
        with self.assertRaises(ValueError):first_observations([item])

    def test_personal_price_filter_is_not_full_universe_parity(self):
        candidate=record();candidate['filters']={'band':'preferred','query':'','direction':'all'}
        result=compare_first_discovery(bundle([record()]),bundle([candidate],complete=False))
        self.assertEqual(len(result['excluded_candidate']),1)
        self.assertEqual(result['comparable_timing_count'],0)
        self.assertEqual(result['rows'][0]['status'],'candidate_coverage_unknown')

    def test_archive_group_span_is_preserved_not_backdated(self):
        data={'date':'2026-08-19','rows':[{'cells':['06:58:43 pm\n\n(3 in 4sec)',
            'NXTT','7.44','119.07K','1.46M','0.68','862.13','-0.09','30.07','22.32K',
            'Squeeze Alert - Up 5% in 5min'],'expandable':True}]}
        with patch.object(Path, 'read_text', return_value=json.dumps(data)):
            result=audit(Path('archive.json'))
        self.assertEqual(result['summary']['parse_errors'],0)
        row=result['observations'][0]
        self.assertTrue(row['observed_at'].endswith('18:58:43-04:00'))
        self.assertIn('(3 in 4sec)',row['displayed_time_and_group'])
        self.assertIsNone(row['first_child_at'])
        self.assertEqual(row['five_minute_rvol_percent'],862.13)


if __name__=='__main__':unittest.main()
