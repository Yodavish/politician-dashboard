import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, ExternalLink } from "lucide-react";
import { fetchSignal } from "@/api/client";
import type { SignalDetail } from "@/api/types";
import { SIGNAL_TXN_NOUN } from "@/api/types";
import { ErrorMessage, Loading } from "@/components/State";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatAmount, txnTypeLabel } from "@/lib/format";

export default function SignalDetailPage() {
  const { signalId = "" } = useParams();
  const [signal, setSignal] = useState<SignalDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchSignal(signalId)
      .then((s) => {
        if (!cancelled) setSignal(s);
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
  }, [signalId]);

  if (loading) return <Loading />;
  if (error) return <ErrorMessage message={error} />;
  if (!signal) return null;

  const { rule } = signal;
  const noun = SIGNAL_TXN_NOUN[signal.type];

  return (
    <section className="space-y-4">
      <p>
        <Button asChild variant="ghost" size="sm" className="-ml-2">
          <Link
            to={`/signals?type=${signal?.type ?? "buy_cluster"}`}
            className="flex items-center gap-1"
          >
            <ArrowLeft className="size-4" />
            Back
          </Link>
        </Button>
      </p>

      <div className="space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="text-2xl font-semibold tracking-tight">
            {signal.ticker}
          </h1>
          <span className="bg-muted text-muted-foreground rounded-full px-2 py-0.5 text-xs font-medium">
            {signal.label}
          </span>
        </div>
        <p className="text-sm">
          {signal.politician_count} politician
          {signal.politician_count === 1 ? "" : "s"} disclosed{" "}
          {noun.many} of {signal.ticker}.
        </p>
        <p className="text-muted-foreground text-sm">
          {signal.asset_name ?? signal.ticker} · {signal.start_date} –{" "}
          {signal.end_date} ({signal.span_days} days)
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">How this signal was triggered</CardTitle>
          <CardDescription>{rule.description}</CardDescription>
        </CardHeader>
        <CardContent>
          <dl className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-4">
            <div>
              <dt className="text-muted-foreground text-xs">Politicians</dt>
              <dd className="text-sm font-semibold">
                {signal.politician_count} (min {rule.min_politicians})
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground text-xs">Transactions</dt>
              <dd className="text-sm font-semibold">
                {signal.transaction_count}
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground text-xs">Max gap</dt>
              <dd className="text-sm font-semibold">
                {rule.max_gap_days} days
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground text-xs">Max span</dt>
              <dd className="text-sm font-semibold">
                {rule.max_span_days} days
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground text-xs">
                Disclosed amount (range)
              </dt>
              <dd className="text-sm font-semibold">
                {formatAmount(signal.total_min, signal.total_max)}
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground text-xs">Transaction type</dt>
              <dd className="text-sm font-semibold">
                {txnTypeLabel(rule.txn_type)} ({rule.txn_type})
              </dd>
            </div>
          </dl>
          <p className="text-muted-foreground mt-3 text-xs">
            Signal id <code>{signal.id}</code>. Tickers must match{" "}
            <code>{rule.ticker_pattern}</code>{" "}
            {rule.excludes_future_dates &&
              "and transactions dated in the future are excluded."}
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Politicians involved</CardTitle>
          <CardDescription>
            Each person is grouped by name and district, not a legislative
            roster.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <ul className="flex flex-wrap gap-x-4 gap-y-1">
            {signal.politicians.map((person) => (
              <li key={person.id} className="text-sm">
                <Link
                  to={`/politicians/${person.id}`}
                  className="text-primary hover:underline"
                >
                  {person.name}
                </Link>{" "}
                <span className="text-muted-foreground">
                  ({person.state_district}) · {person.transaction_count}{" "}
                  {person.transaction_count === 1 ? noun.one : noun.many} ·{" "}
                  {formatAmount(person.amount_min, person.amount_max)}
                </span>
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Triggering transactions</CardTitle>
          <CardDescription>
            {signal.transaction_count} disclosed{" "}
            {signal.transaction_count === 1 ? noun.one : noun.many}, ordered
            by transaction date.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Txn date</TableHead>
                  <TableHead>Politician</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Disclosed amount</TableHead>
                  <TableHead>Filing</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {signal.transactions.map((t) => (
                  <TableRow key={t.id}>
                    <TableCell className="whitespace-nowrap">{t.txn_date}</TableCell>
                    <TableCell>
                      <Link
                        to={`/politicians/${t.politician_id}`}
                        className="text-primary hover:underline"
                      >
                        {t.politician_name}
                      </Link>
                    </TableCell>
                    <TableCell>{txnTypeLabel(t.txn_type)}</TableCell>
                    <TableCell className="whitespace-nowrap">
                      {t.amount_raw || formatAmount(t.amount_min, t.amount_max)}
                    </TableCell>
                    <TableCell>
                      <Link
                        to={`/filings/${t.doc_id}`}
                        className="text-primary inline-flex items-center gap-1 hover:underline"
                      >
                        {t.doc_id}
                        <ExternalLink className="size-3" />
                      </Link>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">What this signal does not show</CardTitle>
        </CardHeader>
        <CardContent>
          <ul className="text-muted-foreground list-disc space-y-1.5 pl-5 text-sm">
            {signal.limitations.map((limitation) => (
              <li key={limitation}>{limitation}</li>
            ))}
          </ul>
        </CardContent>
      </Card>
    </section>
  );
}
