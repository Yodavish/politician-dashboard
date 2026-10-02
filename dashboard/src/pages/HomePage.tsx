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
    <section className="space-y-8">
      <header className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">
          What’s Unusual Right Now?
        </h1>
        <p className="text-muted-foreground text-sm">
          Recent disclosure activity surfaced by clear, reviewable rules.
        </p>
      </header>

      <section className="space-y-3" aria-labelledby="clusters-heading">
        <div>
          <h2 id="clusters-heading" className="text-lg font-semibold">
            Recent Cluster Activity
          </h2>
          <p className="text-muted-foreground text-sm">
            The newest qualifying purchase and sale clusters in the dataset.
          </p>
        </div>
        {highlights.recent_cluster_activity.length === 0 ? (
          <Empty message="No qualifying clusters are available." />
        ) : (
          <div className="divide-y rounded-lg border bg-card">
            {highlights.recent_cluster_activity.map((item) => (
              <article key={item.signal_id} className="space-y-2 p-4">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <h3 className="font-semibold">
                    {item.title}: {item.ticker}
                  </h3>
                  <span className="text-muted-foreground text-sm">
                    {item.date_start} – {item.date_end}
                  </span>
                </div>
                <p className="text-sm">{item.summary}</p>
                <p className="text-muted-foreground text-sm">{item.reason}</p>
                <Link
                  className="text-primary text-sm hover:underline"
                  to={item.detail_url}
                >
                  Review cluster evidence
                </Link>
              </article>
            ))}
          </div>
        )}
      </section>

      <section className="space-y-3" aria-labelledby="largest-heading">
        <div>
          <h2 id="largest-heading" className="text-lg font-semibold">
            Largest Disclosed Transactions
          </h2>
          <p className="text-muted-foreground text-sm">
            Ranked by disclosed minimum amount. Overlapping ranges may prevent
            a definitive comparison.
          </p>
        </div>
        {highlights.largest_disclosed_transactions.length === 0 ? (
          <Empty message="No eligible purchase or sale disclosures are available." />
        ) : (
          <div className="grid gap-3 md:grid-cols-2">
            {highlights.largest_disclosed_transactions.map((item) => (
              <Card key={item.type}>
                <CardHeader>
                  <CardTitle className="text-base">{item.title}</CardTitle>
                  <CardDescription>
                    {item.ticker ?? "No ticker"} · {item.txn_date}
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-2">
                  <p className="text-sm">
                    {item.politician_name} · {item.amount_raw || formatAmount(item.amount_min, item.amount_max)}
                  </p>
                  <p className="text-muted-foreground text-xs">{item.reason}</p>
                  <Link
                    className="text-primary text-sm hover:underline"
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
