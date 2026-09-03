import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, ExternalLink } from "lucide-react";
import { fetchFiling } from "@/api/client";
import type { FilingDetail } from "@/api/types";
import { ErrorMessage, Loading } from "@/components/State";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
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
import { formatAmount, formatDate, txnTypeLabel } from "@/lib/format";

export default function FilingDetailPage() {
  const { docId = "" } = useParams();
  const [filing, setFiling] = useState<FilingDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchFiling(docId)
      .then((f) => {
        if (!cancelled) setFiling(f);
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
  }, [docId]);

  if (loading) return <Loading />;
  if (error) return <ErrorMessage message={error} />;
  if (!filing) return null;

  const meta = [
    { label: "Filer", value: filing.name },
    { label: "State / District", value: filing.state_district },
    { label: "Year", value: String(filing.year) },
    { label: "Filing date", value: formatDate(filing.filing_date) },
    { label: "Kind", value: filing.doc_kind },
  ];

  return (
    <section className="space-y-4">
      <p>
        <Button asChild variant="ghost" size="sm" className="-ml-2">
          <Link to="/transactions" className="flex items-center gap-1">
            <ArrowLeft className="size-4" />
            Back
          </Link>
        </Button>
      </p>
      <h1 className="text-2xl font-semibold tracking-tight">
        Filing {filing.doc_id}
      </h1>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Details</CardTitle>
        </CardHeader>
        <CardContent>
          <dl className="grid grid-cols-1 gap-x-6 gap-y-4 sm:grid-cols-2 lg:grid-cols-3">
            {meta.map((item) => (
              <div key={item.label}>
                <dt className="text-muted-foreground text-xs">{item.label}</dt>
                <dd className="mt-0.5 break-words text-sm font-medium">
                  {item.value}
                </dd>
              </div>
            ))}
            <div>
              <dt className="text-muted-foreground text-xs">Source</dt>
              <dd className="mt-0.5 break-words text-sm">
                <a
                  href={filing.pdf_url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-primary inline-flex items-center gap-1 hover:underline"
                >
                  {filing.pdf_url}
                  <ExternalLink className="size-3.5" />
                </a>
              </dd>
            </div>
          </dl>
        </CardContent>
      </Card>

      <h2 className="text-xl font-semibold tracking-tight">
        Transactions ({filing.transactions.length})
      </h2>
      <div className="rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>#</TableHead>
              <TableHead>Date</TableHead>
              <TableHead>Asset / Ticker</TableHead>
              <TableHead>Type</TableHead>
              <TableHead>Owner</TableHead>
              <TableHead>Amount</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {filing.transactions.map((t) => (
              <TableRow key={t.id}>
                <TableCell>{t.sequence + 1}</TableCell>
                <TableCell>{formatDate(t.txn_date)}</TableCell>
                <TableCell>
                  <div className="font-medium">{t.asset_name}</div>
                  {t.ticker && (
                    <span className="text-muted-foreground font-mono text-xs">
                      {t.ticker}
                    </span>
                  )}
                </TableCell>
                <TableCell>{txnTypeLabel(t.txn_type)}</TableCell>
                <TableCell>{t.owner ?? "—"}</TableCell>
                <TableCell className="whitespace-nowrap">
                  {formatAmount(t.amount_min, t.amount_max)}
                  <span className="text-muted-foreground mt-0.5 block text-xs">
                    {t.amount_raw}
                  </span>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </section>
  );
}
