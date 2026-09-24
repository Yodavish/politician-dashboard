import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fetchFiling } from "@/api/client";
import type { FilingDetail } from "@/api/types";
import FilingDetailPage from "./FilingDetailPage";

vi.mock("@/api/client", () => ({ fetchFiling: vi.fn() }));

const filing: FilingDetail = {
  doc_id: "20033889",
  year: 2026,
  name: "Test Member",
  state_district: "ZZ00",
  filing_date: "2026-02-09",
  doc_kind: "efiled",
  pdf_url: "https://example.invalid/20033889.pdf",
  downloaded_at: "2026-02-10T00:00:00Z",
  created_at: "2026-02-10T00:00:00Z",
  transaction_count: 1,
  amends_doc_id: null,
  amendment_method: null,
  amendment_confidence: null,
  amendment_note: null,
  amendment_verified_at: null,
  amendments: [{
    doc_id: "20034452",
    name: "Test Member",
    filing_date: "2026-03-01",
    pdf_url: "https://example.invalid/20034452.pdf",
    amends_doc_id: "20033889",
    amendment_method: "amendment_match",
    amendment_confidence: "medium",
    amendment_note: "Relationship inferred from matching records.",
    amendment_verified_at: "2026-03-02T00:00:00Z",
  }],
  transactions: [{
    id: 1,
    filing_id: 1,
    doc_id: "20033889",
    filing_date: "2026-02-09",
    politician_id: "zz00_test_member",
    politician_name: "Test Member",
    sequence: 0,
    asset_name: "Sony Group Corporation",
    ticker: "SONY",
    asset_type_code: "ST",
    txn_type: "P",
    txn_date: "2026-12-26",
    notification_date: "2026-01-21",
    amount_min: 1001,
    amount_max: 15000,
    amount_raw: "$1,001 - $15,000",
    owner: "SP",
    filing_status: "Amended",
    ownership_source: null,
    notes: null,
    txn_source_id: null,
    quality_flags: ["transaction_date_after_notification"],
    verified_transaction_date: "2025-12-26",
    verification_method: "amendment_match",
    verification_confidence: "medium",
    verification_source_doc_id: "20034452",
    verification_note: "Matched amended filing.",
    verified_at: "2026-03-02T00:00:00Z",
    verification_source_doc_exists: true,
  }],
};

describe("FilingDetailPage", () => {
  beforeEach(() => {
    vi.mocked(fetchFiling).mockResolvedValue(filing);
  });

  it("shows amendment relationships and transaction verification provenance", async () => {
    render(
      <MemoryRouter initialEntries={["/filings/20033889"]}>
        <Routes>
          <Route path="/filings/:docId" element={<FilingDetailPage />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByText("Amendments")).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "20034452" })[0]).toHaveAttribute(
      "href",
      "/filings/20034452",
    );
    expect(screen.getByText("Verified date")).toBeInTheDocument();
    expect(screen.getByText("Matched amended filing.")).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "20034452" })).toHaveLength(2);
    expect(screen.getByText(/Source reported: 12\/26\/2026/)).toBeInTheDocument();
  });
});
