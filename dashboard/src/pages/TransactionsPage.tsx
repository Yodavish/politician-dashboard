import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { X } from "lucide-react";
import { fetchTransactions } from "@/api/client";
import type { Transaction } from "@/api/types";
import { useFilters } from "@/hooks/useFilters";
import { usePoliticianNameMap } from "@/hooks/usePoliticians";
import Pagination from "@/components/Pagination";
import { Empty, ErrorMessage, Loading } from "@/components/State";
import { Button } from "@/components/ui/button";
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
import {
  ASSET_TYPES,
  OWNERS,
  TXN_TYPES,
  formatAmount,
  formatDate,
  txnTypeLabel,
} from "@/lib/format";

interface TxnFilters {
  limit: number;
  offset: number;
  ticker: string;
  txn_type: string;
  owner: string;
  asset_type_code: string;
  txn_date_min: string;
  txn_date_max: string;
  amount_min: string;
  amount_max: string;
}

const defaults: TxnFilters = {
  limit: 50,
  offset: 0,
  ticker: "",
  txn_type: "",
  owner: "",
  asset_type_code: "",
  txn_date_min: "",
  txn_date_max: "",
  amount_min: "",
  amount_max: "",
};

export default function TransactionsPage() {
  const [filters, setFilters] = useFilters<TxnFilters>(defaults);
  const [data, setData] = useState<{ items: Transaction[]; total: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const names = usePoliticianNameMap();

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchTransactions({
      limit: filters.limit,
      offset: filters.offset,
      ticker: filters.ticker || undefined,
      txn_type: filters.txn_type || undefined,
      owner: filters.owner || undefined,
      asset_type_code: filters.asset_type_code || undefined,
      txn_date_min: filters.txn_date_min || undefined,
      txn_date_max: filters.txn_date_max || undefined,
      amount_min: filters.amount_min ? Number(filters.amount_min) : undefined,
      amount_max: filters.amount_max ? Number(filters.amount_max) : undefined,
    })
      .then((res) => {
        if (!cancelled) {
          setData({ items: res.items, total: res.pagination.total });
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
    // Depend on the primitive filter values rather than the `filters` object
    // reference (the object identity changes every render and would loop).
  }, [
    filters.limit,
    filters.offset,
    filters.ticker,
    filters.txn_type,
    filters.owner,
    filters.asset_type_code,
    filters.txn_date_min,
    filters.txn_date_max,
    filters.amount_min,
    filters.amount_max,
  ]);

  const patch = (p: Partial<TxnFilters>) => setFilters({ ...p, offset: 0 });

  return (
    <section className="space-y-4">
      <h1 className="text-2xl font-semibold tracking-tight">Recent Trades</h1>
      <RealFilterBar filters={filters} patch={patch} />

      {loading && <Loading />}
      {error && <ErrorMessage message={error} />}
      {!loading && !error && data && data.items.length === 0 && <Empty />}

      {!loading && !error && data && data.items.length > 0 && (
        <>
          <div className="rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Date</TableHead>
                  <TableHead>Politician</TableHead>
                  <TableHead>Asset / Ticker</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Owner</TableHead>
                  <TableHead>Amount</TableHead>
                  <TableHead>Filing</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.items.map((t) => (
                  <TableRow key={t.id}>
                    <TableCell>{formatDate(t.txn_date)}</TableCell>
                    <TableCell>
                      <PoliticianCell id={t.politician_id} names={names} />
                    </TableCell>
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
                    <TableCell>
                      <Link
                        to={`/filings/${t.doc_id}`}
                        className="text-primary hover:underline"
                      >
                        {t.doc_id}
                      </Link>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          <Pagination
            offset={filters.offset}
            limit={filters.limit}
            total={data.total}
            onChangeOffset={(offset) => setFilters({ offset })}
          />
        </>
      )}
    </section>
  );
}

function PoliticianCell({
  id,
  names,
}: {
  id: string;
  names: Record<string, { name: string; stateDistrict: string }>;
}) {
  const entry = names[id];
  const label = entry ? `${entry.name} (${entry.stateDistrict})` : id;
  return (
    <Link to={`/politicians/${id}`} className="text-primary hover:underline">
      {label}
    </Link>
  );
}

function FilterSelect({
  label,
  value,
  onValueChange,
  options,
  optionLabel,
  placeholder = "Any",
}: {
  label: string;
  value: string;
  onValueChange: (v: string) => void;
  options: readonly string[];
  optionLabel?: (value: string) => string;
  placeholder?: string;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <Label className="text-muted-foreground text-xs">{label}</Label>
      <Select value={value || undefined} onValueChange={onValueChange}>
        <SelectTrigger className="h-9 w-40">
          <SelectValue placeholder={placeholder} />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="empty">{placeholder}</SelectItem>
          {options.map((o) => (
            <SelectItem key={o} value={o}>
              {optionLabel ? optionLabel(o) : o}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

function RealFilterBar({
  filters,
  patch,
}: {
  filters: TxnFilters;
  patch: (p: Partial<TxnFilters>) => void;
}) {
  const hasFilters =
    filters.ticker ||
    filters.txn_type ||
    filters.owner ||
    filters.asset_type_code ||
    filters.txn_date_min ||
    filters.txn_date_max ||
    filters.amount_min ||
    filters.amount_max;

  return (
    <div className="flex flex-wrap items-end gap-3 rounded-lg border bg-card p-4">
      <div className="flex flex-col gap-1.5">
        <Label className="text-muted-foreground text-xs">Ticker</Label>
        <Input
          type="text"
          value={filters.ticker}
          placeholder="e.g. NVDA"
          className="h-9 w-32"
          onChange={(e) => patch({ ticker: e.target.value })}
        />
      </div>
      <FilterSelect
        label="Type"
        value={filters.txn_type}
        onValueChange={(v) =>
          patch({ txn_type: v === "empty" ? "" : v })
        }
        options={TXN_TYPES}
        optionLabel={txnTypeLabel}
      />
      <FilterSelect
        label="Owner"
        value={filters.owner}
        onValueChange={(v) => patch({ owner: v === "empty" ? "" : v })}
        options={OWNERS}
      />
      <FilterSelect
        label="Asset type"
        value={filters.asset_type_code}
        onValueChange={(v) =>
          patch({ asset_type_code: v === "empty" ? "" : v })
        }
        options={ASSET_TYPES}
      />
      <div className="flex flex-col gap-1.5">
        <Label className="text-muted-foreground text-xs">From date</Label>
        <Input
          type="date"
          value={filters.txn_date_min}
          className="h-9 w-40"
          onChange={(e) => patch({ txn_date_min: e.target.value })}
        />
      </div>
      <div className="flex flex-col gap-1.5">
        <Label className="text-muted-foreground text-xs">To date</Label>
        <Input
          type="date"
          value={filters.txn_date_max}
          className="h-9 w-40"
          onChange={(e) => patch({ txn_date_max: e.target.value })}
        />
      </div>
      <div className="flex flex-col gap-1.5">
        <Label className="text-muted-foreground text-xs">Min amount</Label>
        <Input
          type="number"
          value={filters.amount_min}
          placeholder="10000"
          className="h-9 w-32"
          onChange={(e) => patch({ amount_min: e.target.value })}
        />
      </div>
      <div className="flex flex-col gap-1.5">
        <Label className="text-muted-foreground text-xs">Max amount</Label>
        <Input
          type="number"
          value={filters.amount_max}
          placeholder="50000"
          className="h-9 w-32"
          onChange={(e) => patch({ amount_max: e.target.value })}
        />
      </div>
      {hasFilters && (
        <Button type="button" variant="outline" size="sm" onClick={() => patch(clearFilters)}>
          <X className="size-4" />
          Clear
        </Button>
      )}
    </div>
  );
}

const clearFilters: Partial<TxnFilters> = {
  ticker: "",
  txn_type: "",
  owner: "",
  asset_type_code: "",
  txn_date_min: "",
  txn_date_max: "",
  amount_min: "",
  amount_max: "",
};
