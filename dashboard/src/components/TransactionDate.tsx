import { Check, TriangleAlert } from "lucide-react";

import { qualityWarningText } from "@/lib/format";

export default function TransactionDate({
  txnDate,
  flags,
  filingDate,
  verifiedDate,
}: {
  txnDate: string;
  flags: string[];
  filingDate?: string | null;
  verifiedDate?: string | null;
}) {
  const warning = qualityWarningText(flags, filingDate);
  const displayDate = (value: string) => {
    const [year, month, day] = value.split("-");
    return `${month}/${day}/${year}`;
  };
  return (
    <span className="inline-flex flex-col whitespace-nowrap">
      {verifiedDate && (
        <span className="inline-flex items-center gap-1 font-semibold">
          {displayDate(verifiedDate)}
          <Check className="size-3.5 text-green-700" aria-hidden="true" />
          <span className="text-xs font-normal">Verified</span>
        </span>
      )}
      <span className={verifiedDate ? "text-muted-foreground text-xs" : "inline-flex items-center gap-1"}>
        {verifiedDate ? "Source reported: " : ""}{displayDate(txnDate)}
        {warning && (
          <span
            title={warning}
            role="img"
            aria-label="Reported date may be inconsistent with the filing"
            className="ml-1 text-amber-600"
          >
            <TriangleAlert className="size-3.5" />
          </span>
        )}
      </span>
    </span>
  );
}
