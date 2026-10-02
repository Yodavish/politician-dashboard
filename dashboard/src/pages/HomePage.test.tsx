import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Highlights } from "@/api/types";
import HomePage from "./HomePage";

const { fetchHighlights } = vi.hoisted(() => ({
  fetchHighlights: vi.fn(),
}));

vi.mock("@/api/client", () => ({ fetchHighlights }));

const highlights: Highlights = {
  generated_at: "2026-10-01T12:00:00Z",
  recent_cluster_activity: [
    {
      type: "buy_cluster",
      title: "Buy cluster",
      summary: "NVDA — 8 transactions involving 6 members",
      reason: "Three or more politicians disclosed purchases.",
      date_start: "2026-09-20",
      date_end: "2026-09-27",
      signal_id: "bc_nvda_2026-09-20",
      ticker: "NVDA",
      transaction_count: 8,
      politician_count: 6,
      detail_url: "/signals/bc_nvda_2026-09-20",
    },
  ],
  largest_disclosed_transactions: [
    {
      type: "largest_disclosed_purchase",
      title: "Largest Disclosed Purchase (by minimum amount)",
      ticker: "MSFT",
      politician_id: "ca11_example_person",
      politician_name: "Example Person",
      txn_date: "2026-09-18",
      amount_min: 50_000_000,
      amount_max: null,
      amount_raw: "Over $50,000,000",
      reason:
        "Ranked by disclosed minimum amount. This is not necessarily the definitively largest transaction when disclosed ranges overlap.",
      transaction_id: 123,
      filing_id: 456,
      doc_id: "20260001234",
      detail_url: "/filings/20260001234",
    },
  ],
};

describe("HomePage", () => {
  beforeEach(() => {
    fetchHighlights.mockReset();
  });

  it("groups clusters and disclosed transactions into separate sections", async () => {
    fetchHighlights.mockResolvedValue(highlights);
    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("Recent Cluster Activity")).toBeInTheDocument();
    expect(screen.getByText("Largest Disclosed Transactions")).toBeInTheDocument();
    expect(screen.getByText("NVDA — 8 transactions involving 6 members")).toBeInTheDocument();
    expect(screen.getByText(/Over \$50,000,000/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Review cluster evidence" })).toHaveAttribute(
      "href",
      "/signals/bc_nvda_2026-09-20",
    );
    expect(screen.getByRole("link", { name: "View filing" })).toHaveAttribute(
      "href",
      "/filings/20260001234",
    );
    expect(screen.getByText(/ranges overlap/)).toBeInTheDocument();
  });
});
