import { Link } from "react-router-dom";
import { ArrowRight } from "lucide-react";
import type { Signal } from "@/api/types";
import { SIGNAL_TXN_NOUN } from "@/api/types";
import { formatAmount } from "@/lib/format";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

/**
 * Presentation shell shared by every signal card.
 *
 * The card shows what the data shows and nothing more: how many politicians
 * disclosed the transactions, over what window, and for what disclosed amount
 * range. The wording is derived from the signal's own type, so a buy cluster
 * reads "purchases" and a sell cluster reads "sales" without either implying
 * why anyone acted. It deliberately avoids any suggestion that the politicians
 * acted together, and always links through to the evidence on the detail page.
 */
export default function SignalCard({ signal }: { signal: Signal }) {
  const noun = SIGNAL_TXN_NOUN[signal.type].many;

  return (
    <Card data-testid="signal-card">
      <CardHeader>
        <div className="flex flex-wrap items-center gap-2">
          <CardTitle className="text-lg">{signal.ticker}</CardTitle>
          <span className="bg-muted text-muted-foreground rounded-full px-2 py-0.5 text-xs font-medium">
            {signal.label}
          </span>
        </div>
        <CardDescription>
          {signal.asset_name ?? signal.ticker}
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-4">
        <dl className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-4">
          <div>
            <dt className="text-muted-foreground text-xs">Politicians</dt>
            <dd className="text-sm font-semibold">
              {signal.politician_count}
            </dd>
          </div>
          <div>
            <dt className="text-muted-foreground text-xs">Transactions</dt>
            <dd className="text-sm font-semibold">
              {signal.transaction_count}
            </dd>
          </div>
          <div>
            <dt className="text-muted-foreground text-xs">Window</dt>
            <dd className="text-sm font-semibold">
              {signal.start_date} – {signal.end_date}
            </dd>
            <dd className="text-muted-foreground text-xs">
              {signal.span_days} day span
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
        </dl>

        <div>
          <h3 className="text-muted-foreground text-xs font-medium">
            Politicians who disclosed {noun}
          </h3>
          <ul className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1">
            {signal.politicians.map((person) => (
              <li key={person.id} className="text-sm">
                <Link
                  to={`/politicians/${person.id}`}
                  className="text-primary hover:underline"
                >
                  {person.name}
                </Link>{" "}
                <span className="text-muted-foreground">
                  ({person.state_district})
                </span>
              </li>
            ))}
          </ul>
        </div>

        <p className="text-muted-foreground text-xs">
          A disclosure pattern, not evidence of coordination. Amounts are
          disclosure ranges, and trades may predate one another by months.
        </p>
      </CardContent>

      <div className="flex justify-end px-6">
        <Button asChild variant="ghost" size="sm">
          <Link to={`/signals/${signal.id}`} className="flex items-center gap-1">
            View evidence
            <ArrowRight className="size-4" />
          </Link>
        </Button>
      </div>
    </Card>
  );
}
