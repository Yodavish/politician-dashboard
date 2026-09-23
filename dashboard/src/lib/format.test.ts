import { describe, expect, it } from "vitest";
import {
  assetTypeLabel,
  formatAmount,
  HOUSE_ASSET_TYPE_LABELS,
  qualityWarningText,
  txnTypeLabel,
} from "./format";

describe("formatAmount", () => {
  it("formats the disclosed range", () => {
    expect(formatAmount(1001, 15000)).toBe("$1,001 - $15,000");
  });
  it("formats large values with separators", () => {
    expect(formatAmount(50001, 100000)).toBe("$50,001 - $100,000");
  });
});

describe("txnTypeLabel", () => {
  it("maps known codes", () => {
    expect(txnTypeLabel("P")).toBe("Purchase");
    expect(txnTypeLabel("S")).toBe("Sale");
    expect(txnTypeLabel("E")).toBe("Exchange");
    expect(txnTypeLabel("S (partial)")).toBe("Sale (partial)");
  });
  it("passes through unknown values", () => {
    expect(txnTypeLabel("X")).toBe("X");
  });
});

describe("assetTypeLabel", () => {
  it("maps House codes to official labels", () => {
    expect(assetTypeLabel("ST")).toBe("Stocks (including ADRs)");
    expect(assetTypeLabel("GS")).toBe("Government Securities and Agency Debt");
    expect(assetTypeLabel("CS")).toBe("Corporate Securities (Bonds and Notes)");
    expect(assetTypeLabel("CT")).toBe("Cryptocurrency");
    expect(assetTypeLabel("OT")).toBe("Other");
  });
  it("passes through Senate descriptive labels unchanged", () => {
    expect(assetTypeLabel("Stock")).toBe("Stock");
    expect(assetTypeLabel("Municipal Security")).toBe("Municipal Security");
  });
  it("passes through unknown codes", () => {
    expect(assetTypeLabel("ZZ")).toBe("ZZ");
  });
  it("covers the complete official reference", () => {
    const official = [
      "4K", "5C", "5F", "5P", "AB", "BA", "BK", "CO", "CS", "CT", "DB",
      "DO", "DS", "EF", "EQ", "ET", "FA", "FE", "FN", "FU", "GS", "HE",
      "HN", "IC", "IH", "IP", "IR", "MA", "MF", "MO", "OI", "OL", "OP",
      "OT", "PE", "PM", "PS", "RE", "RF", "RN", "RP", "RS", "SA", "ST",
      "TR", "VA", "VI", "WU",
    ];
    expect(Object.keys(HOUSE_ASSET_TYPE_LABELS)).toEqual(
      expect.arrayContaining(official),
    );
    expect(Object.keys(HOUSE_ASSET_TYPE_LABELS)).toHaveLength(official.length);
    for (const code of official) {
      expect(HOUSE_ASSET_TYPE_LABELS[code]).toMatch(/\S/);
    }
  });
});

describe("qualityWarningText", () => {
  it("maps the supported flags to plain-language messages", () => {
    expect(
      qualityWarningText(["transaction_date_after_notification"]),
    ).toBe("Transaction date is after the notification date.");
    expect(
      qualityWarningText(["transaction_date_after_ingestion_date"]),
    ).toBe("Transaction date is after the date this filing was ingested.");
  });
  it("reflects every present flag when several are raised", () => {
    expect(
      qualityWarningText([
        "transaction_date_after_notification",
        "notification_date_after_filing",
      ]),
    ).toBe(
      "Transaction date is after the notification date.\n" +
        "Notification date is after the filing date.",
    );
  });
  it("includes the filing date when filing-date context is available", () => {
    expect(
      qualityWarningText(["transaction_date_after_filing"], "2026-02-09"),
    ).toBe("Transaction date is after the filing date (2026-02-09).");
    expect(
      qualityWarningText(["notification_date_after_filing"], "2026-02-09"),
    ).toBe("Notification date is after the filing date (2026-02-09).");
  });
  it("does not expose internal flag names for unknown flags", () => {
    expect(qualityWarningText(["some_internal_flag"])).toBe("");
    expect(qualityWarningText([], "2026-02-09")).toBe("");
  });
});
