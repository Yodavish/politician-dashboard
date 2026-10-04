import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { fetchHighlights } from "@/api/client";
import type { Highlights } from "@/api/types";
import { Empty, ErrorMessage, Loading } from "@/components/State";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { formatAmount } from "@/lib/format";

export default function HomePage() {
  const [highlights, setHighlights] = useState<Highlights | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    fetchHighlights()
      .then((result) => {
        if (!cancelled) setHighlights(result);
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
  }, []);

  if (loading) return <Loading label="Loading recent disclosure activity…" />;
  if (error) return <ErrorMessage message={error} />;
  if (!highlights) return null;

  return (
    <section className="page-stack">
      <header className="page-heading">
        <p className="text-xs font-semibold uppercase tracking-[0.12em] text-primary">
          Public disclosure records
        </p>
        <h1 className="page-title">What’s Unusual Right Now?</h1>
        <p className="page-description">
          Recent disclosure activity surfaced by clear, reviewable rules. Trade
          dates: {highlights.activity_window.start_date} – {highlights.activity_window.end_date}.
        </p>
      </header>

      <section className="space-y-4" aria-labelledby="clusters-heading">
        <div>
          <h2 id="clusters-heading" className="section-heading">
            Recent Cluster Activity
          </h2>
          <p className="section-description">
            The newest qualifying purchase and sale clusters in the dataset.
          </p>
        </div>
        {highlights.recent_cluster_activity.length === 0 ? (
          <Empty message="No qualifying clusters are available in this six-month window." />
        ) : (
          <div className="overflow-hidden rounded-xl border bg-card shadow-sm">
            {highlights.recent_cluster_activity.map((item) => (
              <article key={item.signal_id} className="group space-y-2 border-b p-4 last:border-b-0 sm:p-5">
                <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
                  <h3 className="font-semibold tracking-tight">
                    {item.title}: <span className="font-mono text-primary">{item.ticker}</span>
                  </h3>
                  <span className="numeric text-muted-foreground text-sm">
                    {item.date_start} – {item.date_end}
                  </span>
                </div>
                <p className="text-sm leading-relaxed">{item.summary}</p>
                <p className="text-muted-foreground text-sm">{item.reason}</p>
                <Link
                  className="inline-flex min-h-10 items-center text-sm font-medium text-primary hover:underline"
                  to={item.detail_url}
                >
                  Review cluster evidence
                </Link>
              </article>
            ))}
          </div>
        )}
      </section>

      <section className="space-y-4" aria-labelledby="largest-heading">
        <div>
          <h2 id="largest-heading" className="section-heading">
            Largest Disclosed Transactions
          </h2>
          <p className="section-description">
            Ranked by disclosed minimum amount. Overlapping ranges may prevent
            a definitive comparison.
          </p>
        </div>
        {highlights.largest_disclosed_transactions.length === 0 ? (
          <Empty message="No eligible purchase or sale disclosures are available in this six-month window." />
        ) : (
          <div className="grid gap-4 md:grid-cols-2">
            {highlights.largest_disclosed_transactions.map((item) => (
              <Card key={item.type} className="gap-4 py-5">
                <CardHeader className="gap-2">
                  <CardTitle className="text-base tracking-tight">{item.title}</CardTitle>
                  <CardDescription>
                    {item.ticker ?? "No ticker"} · {item.txn_date}
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-2">
                  <p className="numeric text-sm font-medium">
                    {item.politician_name} · {item.amount_raw || formatAmount(item.amount_min, item.amount_max)}
                  </p>
                  <p className="text-muted-foreground text-xs">{item.reason}</p>
                  <Link
                    className="inline-flex min-h-10 items-center text-sm font-medium text-primary hover:underline"
                    to={item.detail_url}
                  >
                    View filing
                  </Link>
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </section>
    </section>
  );
}
