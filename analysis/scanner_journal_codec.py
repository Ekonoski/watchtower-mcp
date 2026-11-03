"""Lossless, independent journal chunks; no sampling or cross-chunk deltas.

Only bulky display-history kinds are packed. Operational SQL readers of
continuation candidates/universes and alerts keep their original JSONB.
"""
import hashlib
import json
import uuid
import zlib

CODEC = 'zlib-json-v1'
PACKED_KINDS = frozenset(('list_membership', 'continuation_state', 'list_snapshot'))
MARKER = {'_scanner_payload_codec': CODEC}
MAX_RAW_BYTES = 40 * 1024 * 1024  # Writer's entire queue is bounded at 32 MiB.


class JournalEvidenceError(RuntimeError):
    """Evidence is unavailable, never an empty or neutral scanner reading."""


def pack(records):
    """Return one self-contained chunk for the supplied logical records."""
    items = {r['id']: [r['kind'], r['payload']] for r in records}
    if not items or len(items) != len(records):
        raise JournalEvidenceError('Empty chunk or duplicate journal event IDs')
    raw = json.dumps(items, allow_nan=False, separators=(',', ':'),
                     sort_keys=True).encode('utf-8')
    if len(raw) > MAX_RAW_BYTES:
        raise JournalEvidenceError('Journal chunk exceeds the lossless codec size bound')
    digest = hashlib.sha256(raw).hexdigest()
    return {'id': str(uuid.UUID(hex=digest[:32])), 'codec': CODEC,
            'sha256': digest, 'raw_bytes': len(raw), 'record_count': len(items),
            'body': zlib.compress(raw, level=6)}


def unpack(chunk):
    """Validate before exposing any evidence; bounded even for corrupt input."""
    label = f"Scanner journal chunk {chunk['id']}"
    if chunk['codec'] != CODEC:
        raise JournalEvidenceError(f'{label}: unsupported codec {chunk["codec"]}')
    size = chunk['raw_bytes']
    if not isinstance(size, int) or not 0 < size <= MAX_RAW_BYTES:
        raise JournalEvidenceError(f'{label}: invalid uncompressed size')
    try:
        decoder = zlib.decompressobj()
        raw = decoder.decompress(bytes(chunk['body']), size + 1)
        if (len(raw) != size or not decoder.eof or decoder.unused_data
                or decoder.unconsumed_tail):
            raise ValueError('invalid compressed length or trailing data')
        if hashlib.sha256(raw).hexdigest() != chunk['sha256']:
            raise ValueError('SHA-256 mismatch')
        items = json.loads(raw)
        if not isinstance(items, dict) or len(items) != chunk['record_count']:
            raise ValueError('record count mismatch')
        return items
    except (ValueError, TypeError, zlib.error) as exc:
        raise JournalEvidenceError(f'{label}: corrupt evidence ({exc})') from exc


def restore(rows, chunks):
    """Restore a page independently, including pages starting inside a chunk."""
    decoded = {}
    for row in rows:
        chunk_id = row.pop('payload_chunk_id', None)
        if chunk_id is None:
            if isinstance(row['payload'], dict) and '_scanner_payload_codec' in row['payload']:
                raise JournalEvidenceError(f"Journal event {row['id']}: missing chunk reference")
            continue  # Legacy JSONB and small operational records.
        if row['payload'] != MARKER:
            raise JournalEvidenceError(f"Journal event {row['id']}: invalid storage marker")
        if chunk_id not in chunks:
            raise JournalEvidenceError(f"Journal event {row['id']}: chunk {chunk_id} unavailable")
        if chunk_id not in decoded:
            decoded[chunk_id] = unpack(chunks[chunk_id])
        item = decoded[chunk_id].get(row['id'])
        if not isinstance(item, list) or len(item) != 2 or item[0] != row['kind']:
            raise JournalEvidenceError(f"Journal event {row['id']}: missing or mismatched evidence")
        row['payload'] = item[1]
    return rows
