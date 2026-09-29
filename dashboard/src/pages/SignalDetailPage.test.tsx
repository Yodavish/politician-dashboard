import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { BuyClusterDetail } from "@/api/types";
import SignalDetailPage from "./SignalDetailPage";

const detail: BuyClusterDetail = {
  id: "bc_nvda_2025-04-03",
  type: "buy_cluster",
  label: "Buy cluster",
  ticker: "NVDA",
  asset_name: "NVIDIA Corporation Common Stock (NVDA)",
  transaction_count: 3,
  politician_count: 3,
  start_date: "2025-04-03",
  end_date: "2025-04-10",
  span_days: 7,
  total_min: 3003,
  total_max: 45000,
  politicians: [
    {
      id: "ca11_nancy_pelosi",
      name: "Nancy Pelosi",
      state_district: "CA11",
      transaction_count: 1,
      amount_min: 1001,
      amount_max: 15000,
    },
    {
      id: "fl23_jared_moskowitz",
      name: "Jared Moskowitz",
      state_district: "FL23",
      transaction_count: 1,
      amount_min: 1001,
      amount_max: 15000,
    },
    {
      id: "tx32_julie_johnson",
      name: "Julie Johnson",
      state_district: "TX32",
      transaction_count: 1,
      amount_min: 1001,
      amount_max: 15000,
    },
  ],
  rule: {
    type: "buy_cluster",
    label: "Buy cluster",
    txn_type: "P",
    min_politicians: 3,
    max_gap_days: 7,
    max_span_days: 14,
    ticker_pattern: "^[A-Z][A-Z0-9.\\-]{0,5}$",
    excludes_future_dates: true,
    materialized: false,
    description:
      "Three or more politicians disclosing open-market purchases (P) of the same equity ticker.",
  },
  limitations: [
    "A cluster reflects disclosure timing only. It is not evidence that the politicians coordinated, and implies nothing about their intent.",
    "Amounts are disclosure ranges, so the total is a range, not an exact sum.",
  ],
  transactions: [
    {
      id: 101,
      filing_id: 11,
      doc_id: "20030179",
      politician_id: "ca11_nancy_pelosi",
      politician_name: "Nancy Pelosi",
      sequence: 3,
      txn_type: "P",
      txn_date: "2025-04-03",
      notification_date: "2025-04-20",
      amount_min: 1001,
      amount_max: 15000,
      amount_raw: "$1,001 - $15,000",
      owner: "SP",
      asset_name: "NVIDIA Corporation Common Stock (NVDA)",
      ticker: "NVDA",
      asset_type_code: "ST",
    },
    {
      id: 102,
      filing_id: 12,
      doc_id: "20030282",
      politician_id: "fl23_jared_moskowitz",
      politician_name: "Jared Moskowitz",
      sequence: 1,
      txn_type: "P",
      txn_date: "2025-04-10",
      notification_date: "2025-04-24",
      amount_min: 1001,
      amount_max: 15000,
      amount_raw: "$1,001 - $15,000",
      owner: "Self",
      asset_name: "NVIDIA Corporation Common Stock (NVDA)",
      ticker: "NVDA",
      asset_type_code: "ST",
    },
    {
      id: 103,
      filing_id: 13,
      doc_id: "20030311",
      politician_id: "tx32_julie_johnson",
      politician_name: "Julie Johnson",
      sequence: 0,
      txn_type: "P",
      txn_date: "2025-04-05",
      notification_date: "2025-04-22",
      amount_min: 1001,
      amount_max: 15000,
      amount_raw: "$1,001 - $15,000",
      owner: "Joint",
      asset_name: "NVIDIA Corporation Common Stock (NVDA)",
      ticker: "NVDA",
      asset_type_code: "ST",
    },
  ],
};

function mockDetail(body: unknown, status = 200) {
  const fetchMock = vi.fn(
    async (_input: RequestInfo | URL) =>
      new Response(JSON.stringify(body), { status }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderAt(id = detail.id) {
  return render(
    <MemoryRouter initialEntries={[`/signals/${id}`]}>
      <Routes>
        <Route path="/signals/:signalId" element={<SignalDetailPage />} />
        <Route path="/signals" element={<div>signals list</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.unstubAllGlobals();
});

describe("SignalDetailPage", () => {
  it("fetches the signal named in the route", async () => {
    const fetchMock = mockDetail(detail);
    renderAt();
    await screen.findByText(/NVIDIA Corporation Common Stock/);
    const url = new URL(String(fetchMock.mock.calls[0][0]), "http://localhost");
    expect(url.pathname).toBe("/api/signals/bc_nvda_2025-04-03");
  });

  it("explains the rule that triggered the signal", async () => {
    mockDetail(detail);
    renderAt();
    await screen.findByText("How this signal was triggered");
    expect(
      screen.getByText(/Three or more politicians disclosing/),
    ).toBeInTheDocument();
    expect(screen.getByText("3 (min 3)")).toBeInTheDocument();
    expect(screen.getByText("7 days")).toBeInTheDocument();
    expect(screen.getByText("14 days")).toBeInTheDocument();
    expect(screen.getByText("Purchase (P)")).toBeInTheDocument();
  });

  it("shows the total as a disclosure range", async () => {
    mockDetail(detail);
    renderAt();
    await screen.findByText("Disclosed amount (range)");
    expect(screen.getByText("$3,003 - $45,000")).toBeInTheDocument();
  });

  it("lists the triggering transactions with links to people and filings", async () => {
    mockDetail(detail);
    renderAt();
    await screen.findByText("20030179");
    expect(screen.getByText("20030179")).toBeInTheDocument();
    expect(screen.getByText("20030282")).toBeInTheDocument();
    expect(
      screen.getAllByRole("link", { name: /Nancy Pelosi/ })[0],
    ).toHaveAttribute("href", "/politicians/ca11_nancy_pelosi");
    expect(
      screen.getByRole("link", { name: /20030179/ }),
    ).toHaveAttribute("href", "/filings/20030179");
  });

  it("surfaces the limitations verbatim", async () => {
    mockDetail(detail);
    renderAt();
    await screen.findByText("What this signal does not show");
    expect(
      screen.getByText(/implies nothing about their intent/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/the total is a range, not an exact sum/),
    ).toBeInTheDocument();
  });

  it("links back to the signals list", async () => {
    mockDetail(detail);
    renderAt();
    await screen.findByText(/NVIDIA Corporation Common Stock/);
    expect(screen.getByRole("link", { name: /Back/ })).toHaveAttribute(
      "href",
      "/signals",
    );
  });

  it("reports a 404 from the API", async () => {
    mockDetail({ detail: "unknown signal: bc_nvda_2025-04-03" }, 404);
    renderAt();
    expect(
      await screen.findByText(/unknown signal: bc_nvda_2025-04-03/),
    ).toBeInTheDocument();
  });
});
