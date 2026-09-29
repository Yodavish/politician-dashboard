import { useEffect, useState } from "react";
import { ArrowDown, ArrowUp } from "lucide-react";
import { fetchSignals } from "@/api/client";
import type { Signal, SignalType } from "@/api/types";
import { useFilters } from "@/hooks/useFilters";
import Pagination from "@/components/Pagination";
import SignalCard from "@/components/SignalCard";
import { Empty, ErrorMessage, Loading } from "@/components/State";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

interface SignalFilters {
  type: SignalType;
  limit: number;
  offset: number;
  ticker: string;
  politician_id: string;
  span_days_max: string;
  start_date: string;
  end_date: string;
  sort: string;
}

const defaults: SignalFilters = {
  type: "buy_cluster",
  limit: 25,
  offset: 0,
  ticker: "",
  politician_id: "",
  span_days_max: "",
  start_date: "",
  end_date: "",
  sort: "-politician_count",
};

// The signal to compute. Both share one clustering rule apart from the
// transaction type they read, so they are the same page with a different
// data source rather than two different pages.
const TYPES: { label: string; value: SignalType }[] = [
  { label: "Buy clusters", value: "buy_cluster" },
  { label: "Sell clusters", value: "sell_cluster" },
];

const SORTS = [
  { label: "Politicians", key: "politician_count" },
  { label: "Transactions", key: "transaction_count" },
  { label: "Disclosed amount", key: "total_max" },
  { label: "Span", key: "span_days" },
  { label: "Ticker", key: "ticker" },
];

// Width ceilings applied to already-computed clusters. The clustering rule
// caps a burst at 14 days, so any ceiling at or above 14 can never exclude
// anything and is omitted rather than shown as a control that does nothing.
// Only the 7-day cut is a real filter (50 signals → 24).
const SPAN_OPTIONS = [
  { label: "All", value: "" },
  { label: "≤ 7 days", value: "7" },
];

export default function SignalsPage() {
  const [filters, setFilters] = useFilters<SignalFilters>(defaults);
  const [data, setData] = useState<{ items: Signal[]; total: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Treat an unrecognized value in the URL as "All" rather than forwarding it
  // to the API, so a hand-edited or stale link cannot trigger a 422.
  const spanValue = SPAN_OPTIONS.some((o) => o.value === filters.span_days_max)
    ? filters.span_days_max
    : "";

  // Treat an unrecognized value in the URL as the default rather than
  // forwarding it to the API, so a hand-edited or stale link cannot trigger
  // a 422.
  const signalType = TYPES.some((t) => t.value === filters.type)
    ? filters.type
    : defaults.type;

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchSignals({
      type: signalType,
      limit: filters.limit,
      offset: filters.offset,
      ticker: filters.ticker || undefined,
      politician_id: filters.politician_id || undefined,
      span_days_max: spanValue || undefined,
      start_date: filters.start_date || undefined,
      end_date: filters.end_date || undefined,
      sort: filters.sort,
    })
      .then((res) => {
        if (!cancelled) setData({ items: res.items, total: res.pagination.total });
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [
    filters.ticker,
    filters.politician_id,
    signalType,
    spanValue,
    filters.start_date,
    filters.end_date,
    filters.limit,
    filters.offset,
    filters.sort,
  ]);

  const items = data?.items ?? [];
  const patch = (p: Partial<SignalFilters>) => setFilters({ ...p, offset: 0 });

  // `sort` is a leading "-" for descending, matching the API's sort keys.
  const sortKey = filters.sort.replace(/^-/, "");
  const sortDescending = filters.sort.startsWith("-");

  function toggleSort(key: string) {
    if (key !== sortKey) {
      patch({ sort: `-${key}` });
    } else {
      patch({ sort: sortDescending ? key : `-${key}` });
    }
  }

  return (
    <section className="space-y-4">
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">Signals</h1>
        <p className="text-muted-foreground text-sm">
          Patterns computed from the disclosures above. Each one links to the
          transactions that triggered it.
        </p>
      </div>

      <div
        className="flex items-center gap-1"
        role="group"
        aria-label="Signal type"
      >
        {TYPES.map((t) => {
          const active = signalType === t.value;
          return (
            <Button
              key={t.value}
              type="button"
              size="sm"
              variant={active ? "default" : "outline"}
              aria-pressed={active}
              className={active ? "h-9 px-3" : "text-muted-foreground h-9 px-3"}
              onClick={() => patch({ type: t.value })}
            >
              {t.label}
            </Button>
          );
        })}
      </div>

      <div className="flex flex-wrap items-end gap-3 rounded-lg border bg-card p-4">
        <div className="flex flex-col gap-1.5">
          <Label className="text-muted-foreground text-xs">Ticker</Label>
          <Input
            type="text"
            value={filters.ticker}
            placeholder="e.g. NVDA"
            maxLength={6}
            className="h-9 w-28 uppercase"
            onChange={(e) => patch({ ticker: e.target.value })}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label className="text-muted-foreground text-xs">Politician</Label>
          <Input
            type="text"
            value={filters.politician_id}
            placeholder="Search politician..."
            className="h-9 w-72"
            onChange={(e) => patch({ politician_id: e.target.value })}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label className="text-muted-foreground text-xs">Start date</Label>
          <Input
            type="date"
            value={filters.start_date}
            className="h-9 w-40"
            onChange={(e) => patch({ start_date: e.target.value })}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label className="text-muted-foreground text-xs">End date</Label>
          <Input
            type="date"
            value={filters.end_date}
            className="h-9 w-40"
            onChange={(e) => patch({ end_date: e.target.value })}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label className="text-muted-foreground text-xs">Pattern span</Label>
          <div
            className="flex items-center gap-1"
            role="group"
            aria-label="Pattern span"
          >
            {SPAN_OPTIONS.map((option) => {
              const active = spanValue === option.value;
              return (
                <Button
                  key={option.label}
                  type="button"
                  size="sm"
                  variant={active ? "default" : "outline"}
                  aria-pressed={active}
                  className={
                    active ? "h-9 px-3" : "text-muted-foreground h-9 px-3"
                  }
                  onClick={() =>
                    patch({ span_days_max: option.value })
                  }
                >
                  {option.label}
                </Button>
              );
            })}
          </div>
        </div>
      </div>

      {loading && <Loading label="Computing signals…" />}
      {error && <ErrorMessage message={error} />}
      {!loading && !error && items.length === 0 && (
        <Empty message="No signals match these filters." />
      )}

      {!loading && !error && items.length > 0 && (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-muted-foreground text-sm">Sort by:</span>
            {SORTS.map((s) => {
              const active = sortKey === s.key;
              const Direction = active && sortDescending ? ArrowDown : ArrowUp;
              return (
                <Button
                  key={s.key}
                  type="button"
                  size="sm"
                  variant={active ? "default" : "outline"}
                  aria-pressed={active}
                  className={
                    active
                      ? "h-8 gap-1.5 px-3"
                      : "text-muted-foreground h-8 gap-1.5 px-3"
                  }
                  onClick={() => toggleSort(s.key)}
                >
                  {s.label}
                  {active && (
                    <>
                      <Direction className="size-3.5" aria-hidden="true" />
                      <span className="sr-only">
                        {sortDescending ? "descending" : "ascending"}
                      </span>
                    </>
                  )}
                </Button>
              );
            })}
          </div>

          <div className="space-y-4">
            {items.map((signal) => (
              <SignalCard key={signal.id} signal={signal} />
            ))}
          </div>

          <Pagination
            offset={filters.offset}
            limit={filters.limit}
            total={data?.total ?? 0}
            onChangeOffset={(offset) => setFilters({ offset })}
          />
        </>
      )}
    </section>
  );
}
