import copy
import unittest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

from analysis.scanner_journal_codec import (
    CODEC, MARKER, PACKED_KINDS, JournalEvidenceError, pack, unpack, restore,
)
from analysis.scanner_journal import PostgresJournal


def event(number=1, kind='continuation_state'):
    return dict(id=f'run:{number:012d}', run_id='run', kind=kind,
                session_date='2026-11-03', observed_at=1793700000.123456,
                payload={'ticker': 'TEST', 'direction': 'down', 'previous_flags': [None, False],
                         'row': {'price': 0.0000123456789, 'market_at': 1793700000.1,
                                 'received_at': 1793700000.123456, 'volume': 9007199254740993,
                                 'reference': {'status': 'unavailable', 'error': 'long cause ' * 40},
                                 'continuation': {'coverage_gap': True, 'state': 'quiet / cooling',
                                                  'volume_returning': None, 'prior_runs': []}},
                         'ranked_tickers': ['Z', 'A'], 'omitted': 33,
                         'unicode': '⚠ café', 'empty': {}, 'false': False, 'zero': 0})


def packed_row(row, chunk):
    return {**copy.deepcopy(row), 'payload': dict(MARKER), 'payload_chunk_id': chunk['id']}


class CodecTests(unittest.TestCase):
    def test_snapshot_rank_arrays_and_membership_details_survive(self):
        row = event(kind='list_snapshot')
        row['payload'] = {'baseline': True, 'coverage_gap': True,
            'previous_sample_at': None, 'sample_at': row['observed_at'],
            'views': {'preferred': {'leaders': {'tickers': ['Z', 'A'], 'total': 103, 'omitted': 101}}},
            'rows_by_ticker': {'Z': event()['payload']['row'], 'A': {'price': 3.5}},
            'scope': ['2026-11-03', 2], 'browser_filters_applied': False}
        chunk = pack([row])
        self.assertEqual(restore([packed_row(row, chunk)], {chunk['id']: chunk}), [row])

    def test_every_field_and_scalar_roundtrips_without_mutation(self):
        rows = [event(i, kind) for i, kind in enumerate(sorted(PACKED_KINDS))]
        before = copy.deepcopy(rows)
        chunk = pack(rows)
        recovered = restore([packed_row(r, chunk) for r in rows], {chunk['id']: chunk})
        self.assertEqual(recovered, before)
        self.assertEqual(rows, before)
        self.assertEqual(pack(rows), chunk)

    def test_page_can_start_inside_chunk_and_mix_old_and_new(self):
        rows = [event(i) for i in range(20)]
        chunk = pack(rows)
        legacy = event(21, 'alert')
        page = [packed_row(r, chunk) for r in rows[7:10]] + [legacy]
        self.assertEqual(restore(page, {chunk['id']: chunk}), rows[7:10] + [legacy])

    def test_batching_compresses_repeated_context(self):
        rows = [event(i) for i in range(100)]
        chunk = pack(rows)
        self.assertLess(len(chunk['body']), chunk['raw_bytes'] * .1)
        self.assertLess(len(chunk['body']), sum(len(pack([r])['body']) for r in rows))

    def test_missing_and_mismatched_evidence_is_never_empty_history(self):
        row = event(); chunk = pack([row])
        with self.assertRaises(JournalEvidenceError):
            restore([packed_row(row, chunk)], {})
        with self.assertRaises(JournalEvidenceError):
            restore([packed_row(event(2), chunk)], {chunk['id']: chunk})
        wrong = packed_row(row, chunk); wrong['kind'] = 'alert'
        with self.assertRaises(JournalEvidenceError):
            restore([wrong], {chunk['id']: chunk})
        with self.assertRaises(JournalEvidenceError):
            restore([{**row, 'payload': dict(MARKER)}], {})

    def test_corruption_and_unknown_versions_are_explicit(self):
        chunk = pack([event()])
        for delta in ({'body': chunk['body'][:-1]}, {'body': chunk['body'] + b'trailing'},
                      {'sha256': '0' * 64}, {'raw_bytes': chunk['raw_bytes'] - 1},
                      {'raw_bytes': 1000000000}, {'record_count': 2}, {'codec': 'future-v99'}):
            with self.subTest(delta=list(delta)), self.assertRaises(JournalEvidenceError):
                unpack({**chunk, **delta})

    def test_duplicate_event_ids_rejected(self):
        with self.assertRaises(JournalEvidenceError):
            pack([event(), event()])


class StoreTests(unittest.TestCase):
    def connection(self):
        conn = MagicMock(); cur = conn.cursor.return_value.__enter__.return_value
        return conn, cur

    def test_chunk_and_event_writes_share_transaction_and_inline_kinds_survive(self):
        conn, cur = self.connection(); cur.fetchall.return_value = []
        records = [event(), event(2, 'alert'), event(3, 'continuation_candidate'),
                   event(4, 'continuation_universe'), event(5, 'heartbeat')]
        with patch('analysis.scanner_journal.connection', return_value=conn), \
                patch('psycopg2.extras.execute_values') as insert:
            PostgresJournal().write(records)
        args = insert.call_args.args[2]
        self.assertIsNotNone(args[0][-1])
        self.assertTrue(all(row[-1] is None for row in args[1:]))
        import json
        for actual, original in zip(args[1:], records[1:]):
            self.assertEqual(json.loads(actual[-2]), original['payload'])
        conn.commit.assert_called_once(); conn.rollback.assert_not_called()

    def test_failure_after_chunk_insert_rolls_back_both(self):
        conn, cur = self.connection(); cur.fetchall.return_value = []
        with patch('analysis.scanner_journal.connection', return_value=conn), \
                patch('psycopg2.extras.execute_values', side_effect=RuntimeError('insert failed')):
            with self.assertRaises(RuntimeError):PostgresJournal().write([event()])
        conn.rollback.assert_called_once(); conn.commit.assert_not_called(); conn.close.assert_called_once()

    def test_ack_lost_retry_does_not_write_orphan_chunks(self):
        conn, cur = self.connection(); cur.fetchall.return_value = [(event()['id'],)]
        with patch('analysis.scanner_journal.connection', return_value=conn), \
                patch('psycopg2.extras.execute_values') as insert:
            PostgresJournal().write([event()])
        insert.assert_not_called()
        self.assertFalse(any('INSERT' in c.args[0] for c in cur.execute.call_args_list))

    def test_retry_with_new_arrivals_only_packs_unsaved_ids(self):
        conn, cur = self.connection(); cur.fetchall.return_value = [(event()['id'],)]
        with patch('analysis.scanner_journal.connection', return_value=conn), \
                patch('psycopg2.extras.execute_values') as insert:
            PostgresJournal().write([event(), event(2)])
        self.assertEqual([r[0] for r in insert.call_args.args[2]], [event(2)['id']])
        chunk_call = next(c for c in cur.execute.call_args_list if 'INSERT INTO public.scanner_journal_chunks' in c.args[0])
        self.assertEqual(chunk_call.args[1][4], 1)

    def test_export_restores_mixed_page_and_keeps_metadata_and_total(self):
        conn, cur = self.connection(); row = event(); chunk = pack([row]); at = datetime.now(timezone.utc)
        cur.fetchone.return_value = (5001,)
        cur.fetchall.side_effect = [
            [(row['id'], 'run', row['kind'], at, at, dict(MARKER), chunk['id']),
             ('legacy', 'old-run', 'alert', at, at, {'direction': 'down'}, None)],
            [tuple(chunk[k] for k in ('id', 'codec', 'sha256', 'raw_bytes', 'record_count', 'body'))],
        ]
        with patch('analysis.scanner_journal.connection', return_value=conn):
            result = PostgresJournal().export('2026-11-03', offset=4000, limit=2)
        self.assertEqual(result['next_offset'], 4002)
        self.assertEqual(result['total'], 5001)
        self.assertEqual(result['records'][0]['payload'], row['payload'])
        self.assertEqual(result['records'][0]['observed_at'], at.isoformat())
        self.assertEqual(result['records'][1]['payload'], {'direction': 'down'})
        self.assertNotIn('payload_chunk_id', result['records'][0])


if __name__ == '__main__':unittest.main()
