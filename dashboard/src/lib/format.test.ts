import { describe, expect, it } from "vitest";
import { formatAmount, txnTypeLabel } from "./format";

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
