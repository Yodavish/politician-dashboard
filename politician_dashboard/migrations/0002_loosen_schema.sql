-- 0002: Fix two concrete problems found while implementing PDF storage.
--
-- 1. filings.filing_date was NOT NULL, but a disclosure index row can
--    legitimately have a blank/malformed FilingDate. The model field is
--    nullable, so the column must accept NULL rather than inventing a date.
--
-- 2. transactions.txn_type was char(1) CHECK IN ('P','S','E'), but real data
--    carries "S (partial)" (a partial sale). Widen to text and keep the
--    original values, preserving source fidelity.

ALTER TABLE filings
    ALTER COLUMN filing_date DROP NOT NULL;

ALTER TABLE transactions
    ALTER COLUMN txn_type TYPE text,
    DROP CONSTRAINT transactions_txn_type_check,
    ADD CONSTRAINT transactions_txn_type_check
        CHECK (txn_type IN ('P', 'S', 'E', 'S (partial)'));
