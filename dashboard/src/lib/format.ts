export function formatAmount(min: number, max: number): string {
  return `$${min.toLocaleString()} - $${max.toLocaleString()}`;
}

export function formatDate(iso: string | null): string {
  if (!iso) return "—";
  return iso;
}

export const TXN_TYPES = ["P", "S", "E", "S (partial)"] as const;

export const OWNERS = ["SP", "Self", "Spouse", "Joint", "Child"] as const;

export const ASSET_TYPES = ["ST", "GS", "CS", "OT", "AB"] as const;

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
