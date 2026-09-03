// API response types matching the FastAPI backend schemas.

export interface Health {
  status: string;
  database: string;
}

export interface Politician {
  id: string;
  name: string;
  party: string | null;
  state: string;
  district: string;
  state_district: string;
  filing_count: number;
  transaction_count: number;
}

export interface FilingSummary {
  doc_id: string;
  year: number;
  name: string;
  state_district: string;
  filing_date: string | null;
  doc_kind: string;
  pdf_url: string;
  downloaded_at: string;
  created_at: string;
  transaction_count: number;
}

export interface Transaction {
  id: number;
  filing_id: number;
  doc_id: string;
  politician_id: string;
  sequence: number;
  asset_name: string;
  ticker: string | null;
  asset_type_code: string | null;
  txn_type: string;
  txn_date: string;
  notification_date: string;
  amount_min: number;
  amount_max: number;
  amount_raw: string;
  owner: string | null;
  filing_status: string | null;
  ownership_source: string | null;
  notes: string | null;
  txn_source_id: string | null;
}

export interface FilingDetail extends FilingSummary {
  transactions: Transaction[];
}

export interface Pagination {
  limit: number;
  offset: number;
  total: number;
  next_url: string | null;
  prev_url: string | null;
}

export interface Paginated<T> {
  items: T[];
  pagination: Pagination;
}
