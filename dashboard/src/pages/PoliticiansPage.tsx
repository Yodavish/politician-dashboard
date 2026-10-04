import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { fetchPoliticians } from "@/api/client";
import type { Politician } from "@/api/types";
import { useFilters } from "@/hooks/useFilters";
import Pagination from "@/components/Pagination";
import SortableTableHead from "@/components/SortableTableHead";
import { Empty, ErrorMessage, Loading } from "@/components/State";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Table,
  TableBody,
  TableCell,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

interface PolFilters {
  limit: number;
  offset: number;
  state: string;
  name: string;
  sort: string;
}

const defaults: PolFilters = { limit: 100, offset: 0, state: "", name: "", sort: "name" };

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
      name: filters.name || undefined,
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
  }, [filters.state, filters.name, filters.limit, filters.offset, filters.sort]);

  const items = data?.items ?? [];

  const patch = (p: Partial<PolFilters>) => setFilters({ ...p, offset: 0 });

  return (
    <section className="page-stack">
      <header className="page-heading">
        <p className="text-xs font-semibold uppercase tracking-[0.12em] text-primary">
          Public officials
        </p>
        <h1 className="page-title">Politicians</h1>
        <p className="page-description">
          Browse members of Congress and open a profile to review their filings and transactions.
        </p>
      </header>
      <div className="filter-panel grid grid-cols-1 gap-3 sm:grid-cols-2 lg:max-w-2xl">
        <div className="flex min-w-0 flex-col gap-1.5">
          <Label className="text-muted-foreground text-xs">State</Label>
          <Input
            type="text"
            value={filters.state}
            placeholder="e.g. CA"
            maxLength={2}
            className="w-full"
            onChange={(e) => patch({ state: e.target.value })}
          />
        </div>
        <div className="flex min-w-0 flex-col gap-1.5">
          <Label className="text-muted-foreground text-xs">Name</Label>
          <Input
            type="text"
            value={filters.name}
            placeholder="Search politicians"
            className="w-full"
            onChange={(e) => patch({ name: e.target.value })}
          />
        </div>
      </div>

      {loading && <Loading />}
      {error && <ErrorMessage message={error} />}
      {!loading && !error && items.length === 0 && <Empty />}

      {!loading && !error && items.length > 0 && (
        <>
          <div className="overflow-hidden rounded-xl border bg-card shadow-sm">
            <Table>
              <TableHeader>
                <TableRow>
                  <SortableTableHead
                    label="Name"
                    sortKey="name"
                    sort={filters.sort}
                    onSort={(sort) => patch({ sort })}
                  />
                  <SortableTableHead
                    label="State / District"
                    sortKey="state_district"
                    sort={filters.sort}
                    onSort={(sort) => patch({ sort })}
                  />
                  <SortableTableHead
                    label="Filings"
                    sortKey="filing_count"
                    sort={filters.sort}
                    onSort={(sort) => patch({ sort })}
                  />
                  <SortableTableHead
                    label="Transactions"
                    sortKey="transaction_count"
                    sort={filters.sort}
                    onSort={(sort) => patch({ sort })}
                  />
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
