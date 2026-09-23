-- 0004: Add a provenance column to filings and ingest_runs.
--
-- V1 starts with House Clerk disclosures only. The Senate eFD source being
-- added to V1 must be distinguishable by provenance: Senate filing IDs are
-- UUIDs that cannot collide with numeric House DocIDs, but relying on that
-- coincidence for distinction is fragile and invisible to the API.
--
-- Existing rows are House Clerk filings, so the DEFAULT 'house_clerk' means
-- no backfill is required.

ALTER TABLE filings
    ADD COLUMN source text NOT NULL DEFAULT 'house_clerk'
        CONSTRAINT filings_source_check
        CHECK (source IN ('house_clerk', 'senate_efd'));

ALTER TABLE ingest_runs
    ADD COLUMN source text NOT NULL DEFAULT 'house_clerk'
        CONSTRAINT ingest_runs_source_check
        CHECK (source IN ('house_clerk', 'senate_efd'));
