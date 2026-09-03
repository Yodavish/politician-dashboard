import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { fetchPoliticians } from "@/api/client";
import type { Politician } from "@/api/types";
import { useFilters } from "@/hooks/useFilters";
import Pagination from "@/components/Pagination";
import { Empty, ErrorMessage, Loading } from "@/components/State";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

interface PolFilters {
  limit: number;
  offset: number;
  state: string;
  name: string;
}

const defaults: PolFilters = { limit: 100, offset: 0, state: "", name: "" };

export default function PoliticiansPage() {
  const [filters, setFilters] = useFilters<PolFilters>(defaults);
  const [data, setData] = useState<{ items: Politician[]; total: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchPoliticians({
      limit: filters.limit,
      offset: filters.offset,
      state: filters.state || undefined,
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
  }, [filters.state, filters.limit, filters.offset]);

  // Client-side name search over the loaded page (API has no name search param).
  const items = (data?.items ?? []).filter((p) =>
    filters.name
      ? p.name.toLowerCase().includes(filters.name.toLowerCase())
      : true,
  );

  const patch = (p: Partial<PolFilters>) => setFilters({ ...p, offset: 0 });

  return (
    <section className="space-y-4">
      <h1 className="text-2xl font-semibold tracking-tight">Politicians</h1>
      <div className="flex flex-wrap items-end gap-3 rounded-lg border bg-card p-4">
        <div className="flex flex-col gap-1.5">
          <Label className="text-muted-foreground text-xs">State</Label>
          <Input
            type="text"
            value={filters.state}
            placeholder="e.g. CA"
            maxLength={2}
            className="h-9 w-32"
            onChange={(e) => patch({ state: e.target.value })}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label className="text-muted-foreground text-xs">Name</Label>
          <Input
            type="text"
            value={filters.name}
            placeholder="Search loaded list"
            className="h-9 w-56"
            onChange={(e) => patch({ name: e.target.value })}
          />
        </div>
      </div>

      {loading && <Loading />}
      {error && <ErrorMessage message={error} />}
      {!loading && !error && items.length === 0 && <Empty />}

      {!loading && !error && items.length > 0 && (
        <>
          <div className="rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>State / District</TableHead>
                  <TableHead>Filings</TableHead>
                  <TableHead>Transactions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((p) => (
                  <TableRow key={p.id}>
                    <TableCell>
                      <Link
                        to={`/politicians/${p.id}`}
                        className="text-primary hover:underline"
                      >
                        {p.name}
                      </Link>
                    </TableCell>
                    <TableCell>{p.state_district}</TableCell>
                    <TableCell>{p.filing_count}</TableCell>
                    <TableCell>{p.transaction_count}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
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
