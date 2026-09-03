CREATE TABLE filings (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    doc_id text NOT NULL UNIQUE,
    year integer NOT NULL,
    prefix text,
    first_name text NOT NULL,
    last_name text NOT NULL,
    suffix text,
    state_district text NOT NULL,
    filing_date date NOT NULL,
    doc_kind text NOT NULL CHECK (doc_kind IN ('efiled', 'scanned')),
    pdf_url text NOT NULL,
    raw_pdf bytea,
    downloaded_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE transactions (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    filing_id bigint NOT NULL REFERENCES filings (id) ON DELETE CASCADE,
    sequence integer NOT NULL,
    txn_source_id text,
    owner_token text,
    asset_name text NOT NULL,
    ticker text,
    asset_type_code text,
    txn_type char(1) NOT NULL CHECK (txn_type IN ('P', 'S', 'E')),
    txn_date date NOT NULL,
    notification_date date NOT NULL,
    amount_min numeric NOT NULL,
    amount_max numeric NOT NULL,
    amount_raw text NOT NULL,
    cap_gains_flag boolean,
    filing_status text,
    ownership_source text,
    notes text,
    UNIQUE (filing_id, sequence)
);

CREATE INDEX transactions_txn_date_idx ON transactions (txn_date);
CREATE INDEX transactions_ticker_idx ON transactions (ticker);
CREATE INDEX filings_filing_date_idx ON filings (filing_date);

CREATE TABLE ingest_runs (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    started_at timestamptz NOT NULL,
    finished_at timestamptz,
    status text NOT NULL CHECK (status IN ('success', 'partial', 'failed')),
    year_targets text[] NOT NULL,
    filings_indexed integer NOT NULL DEFAULT 0,
    filings_new integer NOT NULL DEFAULT 0,
    filings_skipped integer NOT NULL DEFAULT 0,
    scanned_skipped integer NOT NULL DEFAULT 0,
    download_failed integer NOT NULL DEFAULT 0,
    parse_failed integer NOT NULL DEFAULT 0,
    transactions_stored integer NOT NULL DEFAULT 0,
    error text
);