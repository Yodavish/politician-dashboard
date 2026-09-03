import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import {
  fetchPolitician,
  fetchPoliticianFilings,
  fetchPoliticianTransactions,
} from "@/api/client";
import type { FilingDetail, Politician, Transaction } from "@/api/types";
import { Empty, ErrorMessage, Loading } from "@/components/State";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import {
  OWNERS,
  TXN_TYPES,
  formatAmount,
  formatDate,
  txnTypeLabel,
} from "@/lib/format";

export default function PoliticianProfilePage() {
  const { politicianId = "" } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const tab = searchParams.get("tab") ?? "transactions";

  const [pol, setPol] = useState<Politician | null>(null);
  const [filings, setFilings] = useState<FilingDetail[]>([]);
  const [txns, setTxns] = useState<Transaction[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([
      fetchPolitician(politicianId),
      fetchPoliticianFilings(politicianId, { limit: 100 }),
      fetchPoliticianTransactions(politicianId, { limit: 100 }),
    ])
      .then(([p, f, t]) => {
        if (!cancelled) {
          setPol(p);
          setFilings(f.items);
          setTxns(t.items);
        }
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
  }, [politicianId]);

  if (loading) return <Loading />;
  if (error) return <ErrorMessage message={error} />;
  if (!pol) return <Empty message="Politician not found." />;

  const setTab = (t: string) => setSearchParams({ tab: t });

  return (
    <section className="space-y-4">
      <p>
        <Button asChild variant="ghost" size="sm" className="-ml-2">
          <Link to="/politicians" className="flex items-center gap-1">
            <ArrowLeft className="size-4" />
            All politicians
          </Link>
        </Button>
      </p>
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">{pol.name}</h1>
        <div className="text-muted-foreground flex flex-wrap gap-x-5 gap-y-1 text-sm">
          <span>
            {pol.state_district} ({pol.state}/{pol.district})
          </span>
          <span>{pol.filing_count} filings</span>
          <span>{pol.transaction_count} transactions</span>
        </div>
      </div>

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList>
          <TabsTrigger value="transactions">Transactions</TabsTrigger>
          <TabsTrigger value="filings">Filings</TabsTrigger>
        </TabsList>
      </Tabs>
      <div className="pt-2">
        {tab === "transactions" ? (
          <TransactionsTab
            txns={txns}
            filters={{
              txn_type: searchParams.get("txn_type") ?? "",
              owner: searchParams.get("owner") ?? "",
              ticker: searchParams.get("ticker") ?? "",
              asset_type_code: searchParams.get("asset_type_code") ?? "",
            }}
            setFilters={(patch) => {
              const next = new URLSearchParams(searchParams);
              for (const [k, v] of Object.entries(patch)) {
                if (v) next.set(k, v);
                else next.delete(k);
              }
              next.set("tab", tab);
              setSearchParams(next);
            }}
          />
        ) : (
          <FilingsTab filings={filings} />
        )}
      </div>
    </section>
  );
}

function TransactionsTab({
  txns,
  filters,
  setFilters,
}: {
  txns: Transaction[];
  filters: Record<string, string>;
  setFilters: (p: Record<string, string>) => void;
}) {
  if (txns.length === 0) return <Empty message="No transactions." />;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3 rounded-lg border bg-card p-4">
        <div className="flex flex-col gap-1.5">
          <Label className="text-muted-foreground text-xs">Ticker</Label>
          <Input
            type="text"
            value={filters.ticker}
            className="h-9 w-32"
            onChange={(e) => setFilters({ ticker: e.target.value })}
          />
        </div>
        <ProfileSelect
          label="Type"
          value={filters.txn_type}
          onValueChange={(v) => setFilters({ txn_type: v === "empty" ? "" : v })}
          options={TXN_TYPES}
        />
        <ProfileSelect
          label="Owner"
          value={filters.owner}
          onValueChange={(v) => setFilters({ owner: v === "empty" ? "" : v })}
          options={OWNERS}
        />
      </div>
      <div className="rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Date</TableHead>
              <TableHead>Asset / Ticker</TableHead>
              <TableHead>Type</TableHead>
              <TableHead>Owner</TableHead>
              <TableHead>Amount</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {txns
              .filter((t) =>
                filters.ticker
                  ? t.ticker
                      ?.toLowerCase()
                      .includes(filters.ticker.toLowerCase())
                  : true,
              )
              .filter((t) =>
                filters.txn_type ? t.txn_type === filters.txn_type : true,
              )
              .filter((t) => (filters.owner ? t.owner === filters.owner : true))
              .map((t) => (
                <TableRow key={t.id}>
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
                  </TableCell>
                </TableRow>
              ))}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}

function ProfileSelect({
  label,
  value,
  onValueChange,
  options,
}: {
  label: string;
  value: string;
  onValueChange: (v: string) => void;
  options: readonly string[];
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <Label className="text-muted-foreground text-xs">{label}</Label>
      <Select value={value || undefined} onValueChange={onValueChange}>
        <SelectTrigger className="h-9 w-40">
          <SelectValue placeholder="Any" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="empty">Any</SelectItem>
          {options.map((o) => (
            <SelectItem key={o} value={o}>
              {o}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

function FilingsTab({ filings }: { filings: FilingDetail[] }) {
  if (filings.length === 0) return <Empty message="No filings." />;
  return (
    <div className="rounded-lg border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Filing</TableHead>
            <TableHead>Year</TableHead>
            <TableHead>Filing date</TableHead>
            <TableHead>Kind</TableHead>
            <TableHead>Transactions</TableHead>
            <TableHead>Source PDF</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {filings.map((f) => (
            <TableRow key={f.doc_id}>
              <TableCell>
                <Link
                  to={`/filings/${f.doc_id}`}
                  className="text-primary hover:underline"
                >
                  {f.doc_id}
                </Link>
              </TableCell>
              <TableCell>{f.year}</TableCell>
              <TableCell>{formatDate(f.filing_date)}</TableCell>
              <TableCell>{f.doc_kind}</TableCell>
              <TableCell>{f.transaction_count}</TableCell>
              <TableCell>
                <a
                  href={f.pdf_url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-primary hover:underline"
                >
                  View
                </a>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
