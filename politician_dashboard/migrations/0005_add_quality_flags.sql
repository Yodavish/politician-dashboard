-- 0005: Persist derived date-consistency flags for transactions.
--
-- The ingestion pipeline computes derived quality signals (see
-- politician_dashboard/ingest/quality.py) that flag internally inconsistent
-- records without mutating the source values themselves (e.g. a published
-- PTR text layer stating a transaction date that postdates its own
-- notification and the filing that discloses it). The source dates stay
-- verbatim; the flags are stored alongside as an array of violation codes.
-- Existing rows have no known violations, so the column defaults empty.

ALTER TABLE transactions
    ADD COLUMN quality_flags text[] NOT NULL DEFAULT '{}';