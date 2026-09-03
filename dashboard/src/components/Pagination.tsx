import { ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";

interface PaginationProps {
  offset: number;
  limit: number;
  total: number;
  onChangeOffset: (offset: number) => void;
}

export default function Pagination({
  offset,
  limit,
  total,
  onChangeOffset,
}: PaginationProps) {
  if (total <= limit) return null;
  const totalPages = Math.max(1, Math.ceil(total / limit));
  const currentPage = Math.floor(offset / limit) + 1;
  const hasPrev = offset > 0;
  const hasNext = offset + limit < total;

  return (
    <div className="flex items-center gap-3 py-2">
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={!hasPrev}
        onClick={() => onChangeOffset(Math.max(0, offset - limit))}
      >
        <ChevronLeft className="size-4" />
        Prev
      </Button>
      <span className="text-muted-foreground text-sm">
        Page {currentPage} of {totalPages}
      </span>
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={!hasNext}
        onClick={() => onChangeOffset(offset + limit)}
      >
        Next
        <ChevronRight className="size-4" />
      </Button>
    </div>
  );
}
