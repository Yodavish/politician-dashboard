-- 0006: Curated verification records and filing amendment relationships.
--
-- The ingestion pipeline stores source-reported dates verbatim and derives
-- quality flags that flag internally inconsistent records. Some of those
-- anomalies have a known explanation supported by additional authoritative
-- evidence (e.g. an amended filing that restates a trade on a corrected
-- date). Verification is a *curated* interpretation: it is only ever written
-- by an explicit curation step, never inferred automatically during
-- ingestion.
--
-- Design constraints honored here:
--   * The original transaction date is never overwritten (txn_date stays the
--     source-reported value; the verified date lives in a separate column).
--   * Quality flags remain derived from source dates only and are untouched
--     by verification.
--   * transactions.verification_source_doc_id is a plain TEXT reference (the
--     evidence document may not have been ingested yet); it is deliberately
--     NOT a foreign key so verification never depends on ingestion order.
--   * filings.amends_filing_id IS a real FK because it describes an actual
--     relationship between two filings stored in this database. It is always
--     set by an explicit curator action, never inferred from filing_status.
--
-- All additions are nullable, so existing rows migrate without any data fix.

ALTER TABLE filings
    ADD COLUMN amends_filing_id bigint REFERENCES filings (id),
    ADD COLUMN amendment_method text,
    ADD COLUMN amendment_confidence text,
    ADD COLUMN amendment_note text,
    ADD COLUMN amendment_verified_at timestamptz;

CREATE INDEX filings_amends_filing_id_idx ON filings (amends_filing_id);
ALTER TABLE filings ADD CONSTRAINT filings_no_self_amendment
    CHECK (amends_filing_id IS NULL OR amends_filing_id <> id);
ALTER TABLE filings ADD CONSTRAINT filings_amendment_confidence_check
    CHECK (amendment_confidence IS NULL OR amendment_confidence IN ('high', 'medium', 'low'));
ALTER TABLE filings ADD CONSTRAINT filings_amendment_method_check
    CHECK (amendment_method IS NULL OR amendment_method IN ('explicit_source', 'amendment_match', 'manual_review'));
ALTER TABLE filings ADD CONSTRAINT filings_amendment_requires_provenance
    CHECK (amends_filing_id IS NULL OR
           (amendment_method IS NOT NULL AND amendment_confidence IS NOT NULL));

ALTER TABLE transactions
    ADD COLUMN verified_transaction_date date,
    ADD COLUMN verification_method text,
    ADD COLUMN verification_confidence text,
    ADD COLUMN verification_source_doc_id text,
    ADD COLUMN verification_note text,
    ADD COLUMN verified_at timestamptz;

ALTER TABLE transactions ADD CONSTRAINT transactions_verification_method_check
    CHECK (verification_method IS NULL OR verification_method IN ('explicit_source', 'amendment_match', 'manual_review'));
ALTER TABLE transactions ADD CONSTRAINT transactions_verification_confidence_check
    CHECK (verification_confidence IS NULL OR verification_confidence IN ('high', 'medium', 'low'));
ALTER TABLE transactions ADD CONSTRAINT transactions_verification_source_doc_nonblank
    CHECK (verification_source_doc_id IS NULL OR verification_source_doc_id ~ '[^[:space:]]');
ALTER TABLE transactions ADD CONSTRAINT transactions_verified_date_requires_provenance
    CHECK (verified_transaction_date IS NULL OR
           (verification_method IS NOT NULL AND verification_confidence IS NOT NULL
            AND verification_source_doc_id IS NOT NULL
            AND verification_source_doc_id ~ '[^[:space:]]'));
