import type {
  FilingDetail,
  Health,
  Paginated,
  Politician,
  Transaction,
} from "./types";

const BASE = "/api";

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function fetchJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new ApiError(res.status, body.detail ?? res.statusText);
  }
  return res.json() as Promise<T>;
}

export function fetchHealth(): Promise<Health> {
  return fetchJson<Health>(`${BASE}/health`);
}

export function fetchPoliticians(
  params?: Record<string, string | number | undefined>,
): Promise<Paginated<Politician>> {
  const qs = params ? "?" + toQuery(params) : "";
  return fetchJson<Paginated<Politician>>(`${BASE}/politicians${qs}`);
}

export function fetchPolitician(
  id: string,
): Promise<Politician> {
  return fetchJson<Politician>(`${BASE}/politicians/${id}`);
}

export function fetchPoliticianFilings(
  id: string,
  params?: Record<string, string | number | undefined>,
): Promise<Paginated<FilingDetail>> {
  const qs = params ? "?" + toQuery(params) : "";
  return fetchJson<Paginated<FilingDetail>>(
    `${BASE}/politicians/${id}/filings${qs}`,
  );
}

export function fetchPoliticianTransactions(
  id: string,
  params?: Record<string, string | number | undefined>,
): Promise<Paginated<Transaction>> {
  const qs = params ? "?" + toQuery(params) : "";
  return fetchJson<Paginated<Transaction>>(
    `${BASE}/politicians/${id}/transactions${qs}`,
  );
}

export function fetchFilings(
  params?: Record<string, string | number | undefined>,
): Promise<Paginated<FilingDetail>> {
  const qs = params ? "?" + toQuery(params) : "";
  return fetchJson<Paginated<FilingDetail>>(`${BASE}/filings${qs}`);
}

export function fetchFiling(docId: string): Promise<FilingDetail> {
  return fetchJson<FilingDetail>(`${BASE}/filings/${docId}`);
}

export function fetchTransactions(
  params?: Record<string, string | number | undefined>,
): Promise<Paginated<Transaction>> {
  const qs = params ? "?" + toQuery(params) : "";
  return fetchJson<Paginated<Transaction>>(`${BASE}/transactions${qs}`);
}

function toQuery(params: Record<string, string | number | undefined>): string {
  const entries = Object.entries(params).filter(
    ([, v]) => v !== undefined && v !== null && v !== "",
  );
  return new URLSearchParams(
    entries.map(([k, v]) => [k, String(v)]),
  ).toString();
}
