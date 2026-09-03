-- 0003: Add indexes supporting the read-only V1 API.
--
-- 1. transactions(filing_id): a JOIN column for loading the transactions
--    of one filing / politician. Only a compound UNIQUE(filing_id, sequence)
--    existed before, which cannot serve lookups keyed on filing_id alone.
--
-- 2. filings(last_name, first_name, state_district): supports the derived
--    politician grouping (GROUP BY person identity) used by the /politicians
--    endpoints.

CREATE INDEX transactions_filing_id_idx ON transactions (filing_id);
CREATE INDEX filings_person_idx ON filings (last_name, first_name, state_district);
