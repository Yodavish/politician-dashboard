import { TableHead } from "@/components/ui/table";

interface SortableTableHeadProps {
  label: string;
  sortKey: string;
  sort: string;
  onSort: (sort: string) => void;
}

export default function SortableTableHead({
  label,
  sortKey,
  sort,
  onSort,
}: SortableTableHeadProps) {
  const activeKey = sort.startsWith("-") ? sort.slice(1) : sort;
  const active = activeKey === sortKey;
  const descending = sort.startsWith("-");

  return (
    <TableHead
      aria-sort={active ? (descending ? "descending" : "ascending") : "none"}
    >
      <button
        type="button"
        className="inline-flex items-center gap-1 rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        aria-label={`Sort by ${label}${
          active ? (descending ? ", descending" : ", ascending") : ""
        }`}
        onClick={() =>
          onSort(active && !descending ? `-${sortKey}` : sortKey)
        }
      >
        {label}
        {active && <span aria-hidden="true">{descending ? "↓" : "↑"}</span>}
      </button>
    </TableHead>
  );
}
