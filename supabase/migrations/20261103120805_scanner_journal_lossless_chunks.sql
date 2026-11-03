-- Additive only: no UPDATE, DELETE, rewrite, retention, or changes to old payloads.
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '30s';

CREATE TABLE public.scanner_journal_chunks (
    id uuid PRIMARY KEY,
    codec text NOT NULL CHECK (codec = 'zlib-json-v1'),
    sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    raw_bytes integer NOT NULL CHECK (raw_bytes > 0 AND raw_bytes <= 41943040),
    record_count integer NOT NULL CHECK (record_count > 0 AND record_count <= 500),
    body bytea NOT NULL CHECK (octet_length(body) > 0),
    saved_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
ALTER TABLE public.scanner_journal_chunks ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.scanner_journal_chunks FROM PUBLIC, anon, authenticated;

-- Nullable, no default: metadata-only; existing rows remain byte-for-byte intact.
ALTER TABLE public.scanner_journal ADD COLUMN payload_chunk_id uuid;
-- NOT VALID avoids a scan of the existing multi-GB journal. PostgreSQL still
-- enforces this FK on every new write; every legacy reference is NULL.
ALTER TABLE public.scanner_journal ADD CONSTRAINT scanner_journal_chunk_fk
    FOREIGN KEY (payload_chunk_id) REFERENCES public.scanner_journal_chunks(id)
    ON DELETE RESTRICT NOT VALID;
COMMENT ON COLUMN public.scanner_journal.payload_chunk_id IS
    'NULL = original JSONB. Non-NULL = lossless zlib-json-v1 chunk; decode with PostgresJournal.export or scanner_journal_codec.restore. Never treat the storage marker as scanner data.';
COMMENT ON TABLE public.scanner_journal_chunks IS
    'Immutable scanner evidence, committed atomically with event references. No retention or deletion. Each chunk independently decodes and validates SHA-256.';
