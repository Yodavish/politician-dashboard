// API response types matching the FastAPI backend schemas.

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

export interface AmendmentFiling {
  doc_id: string;
  name: string;
  filing_date: string | null;
  pdf_url: string;
  amends_doc_id: string | null;
  amendment_method: string | null;
  amendment_confidence: string | null;
  amendment_note: string | null;
  amendment_verified_at: string | null;
}

export interface Transaction {
  id: number;
  filing_id: number;
  doc_id: string;
  filing_date: string | null;
  politician_id: string;
  politician_name: string;
  sequence: number;
  asset_name: string;
  ticker: string | null;
  asset_type_code: string | null;
  txn_type: string;
  txn_date: string;
  notification_date: string;
  amount_min: number;
  amount_max: number | null;
  amount_raw: string;
  owner: string | null;
  filing_status: string | null;
  ownership_source: string | null;
  notes: string | null;
  txn_source_id: string | null;
  quality_flags: string[];
  verified_transaction_date: string | null;
  verification_method: string | null;
  verification_confidence: string | null;
  verification_source_doc_id: string | null;
  verification_note: string | null;
  verified_at: string | null;
  verification_source_doc_exists: boolean;
}

export interface FilingDetail extends FilingSummary {
  amends_doc_id: string | null;
  amendment_method: string | null;
  amendment_confidence: string | null;
  amendment_note: string | null;
  amendment_verified_at: string | null;
  amendments: AmendmentFiling[];
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

// --- Signals -------------------------------------------------------------
//
// Signals are computed on the server from filings and transactions. The
// `rule` block travels with every signal so the thresholds that produced it
// are always visible, and `limitations` carries the caveats that keep a
// disclosure pattern from reading as a conclusion.

export type SignalType = "buy_cluster" | "sell_cluster";

/**
 * Transaction wording for user-facing copy, keyed by signal type.
 *
 * Deliberately descriptive: it names what was disclosed and nothing about
 * why. A sell cluster says "sales", never "exit", "bearish" or "selling
 * pressure".
 */
export const SIGNAL_TXN_NOUN: Record<
  SignalType,
  { one: string; many: string }
> = {
  buy_cluster: { one: "purchase", many: "purchases" },
  sell_cluster: { one: "sale", many: "sales" },
};

export interface SignalRule {
  type: SignalType;
  label: string;
  txn_type: string;
  min_politicians: number;
  max_gap_days: number;
  max_span_days: number;
  ticker_pattern: string;
  excludes_future_dates: boolean;
  materialized: boolean;
  description: string;
}

export interface SignalPolitician {
  id: string;
  name: string;
  state_district: string;
  transaction_count: number;
  amount_min: number;
  amount_max: number | null;
}

export interface SignalTransaction {
  id: number;
  filing_id: number;
  doc_id: string;
  politician_id: string;
  politician_name: string;
  sequence: number;
  txn_type: string;
  txn_date: string;
  notification_date: string;
  amount_min: number;
  amount_max: number | null;
  amount_raw: string;
  owner: string | null;
  asset_name: string;
  ticker: string | null;
  asset_type_code: string | null;
}

export interface Signal {
  id: string;
  type: SignalType;
  label: string;
  ticker: string;
  asset_name: string | null;
  transaction_count: number;
  politician_count: number;
  start_date: string;
  end_date: string;
  span_days: number;
  total_min: number;
  total_max: number | null;
  politicians: SignalPolitician[];
  rule: SignalRule;
  limitations: string[];
}

export interface SignalDetail extends Signal {
  transactions: SignalTransaction[];
}

export interface ClusterHighlight {
  type: SignalType;
  title: string;
  summary: string;
  reason: string;
  date_start: string;
  date_end: string;
  signal_id: string;
  ticker: string;
  transaction_count: number;
  politician_count: number;
  detail_url: string;
}

export interface LargestTransactionHighlight {
  type: "largest_disclosed_purchase" | "largest_disclosed_sale";
  title: string;
  ticker: string | null;
  politician_id: string;
  politician_name: string;
  txn_date: string;
  amount_min: number;
  amount_max: number | null;
  amount_raw: string;
  reason: string;
  transaction_id: number;
  filing_id: number;
  doc_id: string;
  detail_url: string;
}

export interface Highlights {
  generated_at: string;
  recent_cluster_activity: ClusterHighlight[];
  largest_disclosed_transactions: LargestTransactionHighlight[];
}
