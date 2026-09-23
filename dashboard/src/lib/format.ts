export function formatAmount(min: number, max: number): string {
  return `$${min.toLocaleString()} - $${max.toLocaleString()}`;
}

export function formatDate(iso: string | null): string {
  if (!iso) return "—";
  return iso;
}

export const TXN_TYPES = ["P", "S", "E", "S (partial)"] as const;

export const OWNERS = ["SP", "Self", "Spouse", "Joint", "Child"] as const;

export function txnTypeLabel(t: string): string {
  switch (t) {
    case "P":
      return "Purchase";
    case "S":
      return "Sale";
    case "E":
      return "Exchange";
    case "S (partial)":
      return "Sale (partial)";
    default:
      return t;
  }
}

// Complete list of U.S. House Financial Disclosure asset type codes from the
// official reference (https://fd.house.gov/reference/asset-type-codes.aspx).
// Senate filings store descriptive labels instead of these codes and pass
// through assetTypeLabel() unchanged.
export const HOUSE_ASSET_TYPE_LABELS: Record<string, string> = {
  "4K": "401K and Other Non-Federal Retirement Accounts",
  "5C": "529 College Savings Plan",
  "5F": "529 Portfolio",
  "5P": "529 Prepaid Tuition Plan",
  AB: "Asset-Backed Securities",
  BA: "Bank Accounts, Money Market Accounts and CDs",
  BK: "Brokerage Accounts",
  CO: "Collectibles",
  CS: "Corporate Securities (Bonds and Notes)",
  CT: "Cryptocurrency",
  DB: "Defined Benefit Pension",
  DO: "Debts Owed to the Filer",
  DS: "Delaware Statutory Trust",
  EF: "Exchange Traded Funds (ETF)",
  EQ: "Excepted/Qualified Blind Trust",
  ET: "Exchange Traded Notes",
  FA: "Farms",
  FE: "Foreign Exchange Position (Currency)",
  FN: "Fixed Annuity",
  FU: "Futures",
  GS: "Government Securities and Agency Debt",
  HE: "Hedge Funds & Private Equity Funds (EIF)",
  HN: "Hedge Funds & Private Equity Funds (non-EIF)",
  IC: "Investment Club",
  IH: "IRA (Held in Cash)",
  IP: "Intellectual Property & Royalties",
  IR: "IRA",
  MA: "Managed Accounts (e.g., SMA and UMA)",
  MF: "Mutual Funds",
  MO: "Mineral/Oil/Solar Energy Rights",
  OI: "Ownership Interest (Holding Investments)",
  OL: "Ownership Interest (Engaged in a Trade or Business)",
  OP: "Options",
  OT: "Other",
  PE: "Pensions",
  PM: "Precious Metals",
  PS: "Stock (Not Publicly Traded)",
  RE: "Real Estate Invest. Trust (REIT)",
  RF: "REIT (EIF)",
  RN: "REIT (non-EIF)",
  RP: "Real Property",
  RS: "Restricted Stock Units (RSUs)",
  SA: "Stock Appreciation Right",
  ST: "Stocks (including ADRs)",
  TR: "Trust",
  VA: "Variable Annuity",
  VI: "Variable Insurance",
  WU: "Whole/Universal Insurance",
};

export const ASSET_TYPES = Object.keys(HOUSE_ASSET_TYPE_LABELS);

export function assetTypeLabel(code: string): string {
  return HOUSE_ASSET_TYPE_LABELS[code] ?? code;
}

// Plain-language explanations for the derived data-quality flags computed by
// the ingestion pipeline (politician_dashboard.ingest.quality). Flag names
// are internal and never shown to users.
export const QUALITY_FLAG_MESSAGES: Record<string, string> = {
  transaction_date_after_notification:
    "Transaction date is after the notification date.",
  transaction_date_after_filing: "Transaction date is after the filing date.",
  transaction_date_after_ingestion_date:
    "Transaction date is after the date this filing was ingested.",
  notification_date_after_filing:
    "Notification date is after the filing date.",
};

export function qualityWarningText(
  flags: readonly string[],
  filingDate?: string | null,
): string {
  return flags
    .map((flag) => {
      if (flag === "transaction_date_after_filing" && filingDate) {
        return `Transaction date is after the filing date (${filingDate}).`;
      }
      if (flag === "notification_date_after_filing" && filingDate) {
        return `Notification date is after the filing date (${filingDate}).`;
      }
      return QUALITY_FLAG_MESSAGES[flag];
    })
    .filter((s): s is string => Boolean(s))
    .join("\n");
}
