import { TriangleAlert } from "lucide-react";

import { formatDate, qualityWarningText } from "@/lib/format";

export default function TransactionDate({
  txnDate,
  flags,
  filingDate,
}: {
  txnDate: string;
  flags: string[];
  filingDate?: string | null;
}) {
  const warning = qualityWarningText(flags, filingDate);
  return (
    <span className="inline-flex items-center gap-1 whitespace-nowrap">
      {formatDate(txnDate)}
      {warning && (
        <span
          title={warning}
          role="img"
          aria-label="Reported date may be inconsistent with the filing"
          className="text-amber-600"
        >
          <TriangleAlert className="size-3.5" />
        </span>
      )}
    </span>
  );
}
