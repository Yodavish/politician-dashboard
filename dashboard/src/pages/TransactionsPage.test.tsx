import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import TransactionsPage from "./TransactionsPage";

const assetName =
  "JT Long Is Pwr Auth Var 9/01/55 [GS] with an unusually long description";
const sample = {
  items: [
    {
      id: 1,
      filing_id: 1,
      doc_id: "20030001",
      filing_date: "2025-01-01",
      politician_id: "ca11_nancy_pelosi",
      politician_name: "Nancy Pelosi",
      sequence: 1,
      asset_name: assetName,
      ticker: null,
      asset_type_code: null,
      txn_type: "P",
      txn_date: "2024-12-01",
      notification_date: "2025-01-01",
      amount_min: 1001,
      amount_max: 15000,
      amount_raw: "$1,001 - $15,000",
      owner: null,
      filing_status: null,
      ownership_source: null,
      notes: null,
      txn_source_id: null,
      quality_flags: ["transaction_date_after_notification"],
      verified_transaction_date: null,
      verification_method: null,
      verification_confidence: null,
      verification_source_doc_id: null,
      verification_note: null,
      verified_at: null,
      verification_source_doc_exists: false,
    },
    {
      id: 2,
      filing_id: 2,
      doc_id: "20030002",
      filing_date: "2025-01-02",
      politician_id: "ca11_nancy_pelosi",
      politician_name: "Nancy Pelosi",
      sequence: 1,
      asset_name: "NVIDIA Corporation",
      ticker: "NVDA",
      asset_type_code: "ST",
      txn_type: "P",
      txn_date: "2024-12-02",
      notification_date: "2025-01-02",
      amount_min: 15001,
      amount_max: 50000,
      amount_raw: "$15,001 - $50,000",
      owner: "Self",
      filing_status: "New",
      ownership_source: null,
      notes: null,
      txn_source_id: null,
      quality_flags: [],
      verified_transaction_date: null,
      verification_method: null,
      verification_confidence: null,
      verification_source_doc_id: null,
      verification_note: null,
      verified_at: null,
      verification_source_doc_exists: false,
    },
  ],
  pagination: { limit: 50, offset: 0, total: 2, next_url: null, prev_url: null },
};

function CurrentSearch() {
  return <output data-testid="location-search">{useLocation().search}</output>;
}

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(sample), { status: 200 })),
  );
});

describe("TransactionsPage", () => {
  it("renders Asset and Ticker separately and keeps the full asset accessible", async () => {
    render(
      <MemoryRouter initialEntries={["/transactions"]}>
        <TransactionsPage />
        <CurrentSearch />
      </MemoryRouter>,
    );

    await screen.findByText(assetName);
    expect(
      screen.getByRole("columnheader", { name: "Asset" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("columnheader", { name: /Ticker/ }),
    ).toBeInTheDocument();
    expect(screen.getAllByText("—")).toHaveLength(2);
    expect(screen.getByText("NVDA")).toBeInTheDocument();
    expect(screen.getByTitle(assetName)).toHaveClass("line-clamp-2");
    expect(
      screen.getByRole("img", { name: /Reported date may be inconsistent/ }),
    ).toBeInTheDocument();
  });

  it("shows sortable headers and requests the selected server sort", async () => {
    const fetchMock = vi.mocked(fetch);
    render(
      <MemoryRouter initialEntries={["/transactions?offset=4"]}>
        <TransactionsPage />
        <CurrentSearch />
      </MemoryRouter>,
    );

    await screen.findByText(assetName);
    const amountHeader = screen.getByRole("button", { name: "Sort by Amount" });
    expect(amountHeader).toBeInTheDocument();
    fireEvent.click(amountHeader);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenLastCalledWith(
        expect.stringContaining("sort=amount_min"),
      );
    });
    await waitFor(() => {
      const search = new URLSearchParams(
        screen.getByTestId("location-search").textContent,
      );
      expect(search.get("sort")).toBe("amount_min");
      expect(search.get("offset")).toBe("0");
    });
  });
});
