import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import type { Signal } from "@/api/types";
import SignalCard from "./SignalCard";

export const cluster: Signal = {
  id: "bc_nvda_2025-04-03",
  type: "buy_cluster",
  label: "Buy cluster",
  ticker: "NVDA",
  asset_name: "NVIDIA Corporation Common Stock (NVDA)",
  transaction_count: 8,
  politician_count: 3,
  start_date: "2025-04-03",
  end_date: "2025-04-16",
  span_days: 13,
  total_min: 8008,
  total_max: 120000,
  politicians: [
    {
      id: "fl23_jared_moskowitz",
      name: "Jared Moskowitz",
      state_district: "FL23",
      transaction_count: 3,
      amount_min: 3003,
      amount_max: 45000,
    },
    {
      id: "ca11_nancy_pelosi",
      name: "Nancy Pelosi",
      state_district: "CA11",
      transaction_count: 1,
      amount_min: 1001,
      amount_max: 15000,
    },
    {
      id: "tx32_julie_johnson",
      name: "Julie Johnson",
      state_district: "TX32",
      transaction_count: 4,
      amount_min: 4004,
      amount_max: 60000,
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
    description: "Three or more politicians disclosing purchases of one ticker.",
  },
  limitations: [
    "A cluster reflects disclosure timing only.",
    "Amounts are disclosure ranges.",
  ],
};

export const sellCluster: Signal = {
  ...cluster,
  id: "sc_gs_2025-04-07",
  type: "sell_cluster",
  label: "Sell cluster",
  ticker: "GS",
  asset_name: "Goldman Sachs Group Common Stock (GS)",
  transaction_count: 5,
  politician_count: 5,
  start_date: "2025-04-07",
  end_date: "2025-04-11",
  span_days: 4,
  total_min: 160200,
  total_max: 300000,
  rule: { ...cluster.rule, type: "sell_cluster", label: "Sell cluster", txn_type: "S" },
};

describe("SignalCard", () => {
  it("shows the ticker, label, and asset name", () => {    render(
      <MemoryRouter>
        <SignalCard signal={cluster} />
      </MemoryRouter>,
    );
    expect(screen.getByText("NVDA")).toBeInTheDocument();
    expect(screen.getByText("Buy cluster")).toBeInTheDocument();
    expect(
      screen.getByText("NVIDIA Corporation Common Stock (NVDA)"),
    ).toBeInTheDocument();
  });

  it("shows the politician, transaction, window, and span counts", () => {
    render(
      <MemoryRouter>
        <SignalCard signal={cluster} />
      </MemoryRouter>,
    );
    const politicians = screen.getByText("Politicians").nextElementSibling;
    expect(politicians).toHaveTextContent("3");
    const transactions = screen.getByText("Transactions").nextElementSibling;
    expect(transactions).toHaveTextContent("8");
    const window = screen.getByText("Window").nextElementSibling;
    expect(window).toHaveTextContent("2025-04-03 – 2025-04-16");
    expect(screen.getByText("13 day span")).toBeInTheDocument();
  });

  it("presents the total as a disclosure range, not a single figure", () => {
    render(
      <MemoryRouter>
        <SignalCard signal={cluster} />
      </MemoryRouter>,
    );
    expect(screen.getByText("Disclosed amount (range)")).toBeInTheDocument();
    expect(screen.getByText("$8,008 - $120,000")).toBeInTheDocument();
  });

  it("names the transaction noun for the signal's own type", () => {
    const { unmount } = render(
      <MemoryRouter>
        <SignalCard signal={cluster} />
      </MemoryRouter>,
    );
    expect(
      screen.getByText("Politicians who disclosed purchases"),
    ).toBeInTheDocument();
    unmount();

    render(
      <MemoryRouter>
        <SignalCard signal={sellCluster} />
      </MemoryRouter>,
    );
    expect(screen.getByText("Sell cluster")).toBeInTheDocument();
    expect(
      screen.getByText("Politicians who disclosed sales"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Politicians who disclosed purchases"),
    ).toBeNull();
  });

  it("describes a sell cluster without implying intent", () => {
    render(
      <MemoryRouter>
        <SignalCard signal={sellCluster} />
      </MemoryRouter>,
    );
    const text = (document.body.textContent ?? "").toLowerCase();
    for (const forbidden of [
      "bearish",
      "exit",
      "selling pressure",
      "dump",
      "liquidat",
    ]) {
      expect(text).not.toContain(forbidden);
    }
  });

  it("names every politician and links to their profile", () => {
    render(
      <MemoryRouter>
        <SignalCard signal={cluster} />
      </MemoryRouter>,
    );
    expect(
      screen.getByRole("link", { name: "Jared Moskowitz" }),
    ).toHaveAttribute("href", "/politicians/fl23_jared_moskowitz");
    expect(
      screen.getByRole("link", { name: "Nancy Pelosi" }),
    ).toHaveAttribute("href", "/politicians/ca11_nancy_pelosi");
    expect(
      screen.getByRole("link", { name: "Julie Johnson" }),
    ).toHaveAttribute("href", "/politicians/tx32_julie_johnson");
  });

  it("links through to the signal detail page", () => {
    render(
      <MemoryRouter>
        <SignalCard signal={cluster} />
      </MemoryRouter>,
    );
    expect(screen.getByRole("link", { name: /View evidence/ })).toHaveAttribute(
      "href",
      "/signals/bc_nvda_2025-04-03",
    );
  });

  it("states that the pattern is not evidence of coordination", () => {
    render(
      <MemoryRouter>
        <SignalCard signal={cluster} />
      </MemoryRouter>,
    );
    expect(
      screen.getByText(/not evidence of coordination/i),
    ).toBeInTheDocument();
  });

  it("falls back to the ticker when the asset name is missing", () => {
    render(
      <MemoryRouter>
        <SignalCard signal={{ ...cluster, asset_name: null }} />
      </MemoryRouter>,
    );
    expect(screen.getAllByText("NVDA").length).toBeGreaterThan(0);
  });
});
