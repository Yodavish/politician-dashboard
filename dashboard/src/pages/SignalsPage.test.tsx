import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { BuyCluster } from "@/api/types";
import SignalsPage from "./SignalsPage";

const nvda: BuyCluster = {
  id: "bc_nvda_2025-04-03",
  type: "buy_cluster",
  label: "Buy cluster",
  ticker: "NVDA",
  asset_name: "NVIDIA Corporation Common Stock (NVDA)",
  transaction_count: 8,
  politician_count: 6,
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
  limitations: ["A cluster reflects disclosure timing only."],
};

function page(items: BuyCluster[], total = items.length) {
  return {
    items,
    pagination: { limit: 25, offset: 0, total, next_url: null, prev_url: null },
  };
}

function lastQuery(fetchMock: ReturnType<typeof vi.fn>): URLSearchParams {
  const call = fetchMock.mock.calls.at(-1)![0];
  return new URL(String(call), "http://localhost").searchParams;
}

function mockFetchOnce(body?: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(
          JSON.stringify(body ?? page([nvda])),
          { status: 200 },
        ),
    ),
  );
}

beforeEach(() => {
  vi.unstubAllGlobals();
});

describe("SignalsPage", () => {
  it("renders a card per signal from the API", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(page([nvda])), { status: 200 })),
    );
    render(
      <MemoryRouter initialEntries={["/signals"]}>
        <SignalsPage />
      </MemoryRouter>,
    );
    await screen.findByText("NVDA");
    expect(screen.getAllByTestId("signal-card")).toHaveLength(1);
    expect(screen.getByText("Jared Moskowitz")).toBeInTheDocument();
  });

  it("shows an empty state when no signals match", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(page([])), { status: 200 })),
    );
    render(
      <MemoryRouter initialEntries={["/signals"]}>
        <SignalsPage />
      </MemoryRouter>,
    );
    await waitFor(() =>
      expect(screen.getByTestId("empty-state")).toBeInTheDocument(),
    );
    expect(screen.getByText(/No signals match these filters/)).toBeInTheDocument();
  });

  it("sends the ticker filter to the API when the field changes", async () => {
    const fetchMock = vi.fn(
      async () => new Response(JSON.stringify(page([nvda])), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    render(
      <MemoryRouter initialEntries={["/signals"]}>
        <SignalsPage />
      </MemoryRouter>,
    );
    await screen.findByText("NVDA");
    expect(lastQuery(fetchMock).has("ticker")).toBe(false);

    fireEvent.change(screen.getByPlaceholderText("e.g. NVDA"), {
      target: { value: "AAPL" },
    });

    await waitFor(() => expect(lastQuery(fetchMock).get("ticker")).toBe("AAPL"));
  });

  it("sends date and politician filters from the URL on first render", async () => {
    const fetchMock = vi.fn(
      async () => new Response(JSON.stringify(page([nvda])), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    render(
      <MemoryRouter
        initialEntries={[
          "/signals?politician_id=ca11_nancy_pelosi&start_date=2025-01-01&end_date=2025-12-31",
        ]}
      >
        <SignalsPage />
      </MemoryRouter>,
    );

    await screen.findByText("NVDA");
    const query = lastQuery(fetchMock);
    expect(query.get("politician_id")).toBe("ca11_nancy_pelosi");
    expect(query.get("start_date")).toBe("2025-01-01");
    expect(query.get("end_date")).toBe("2025-12-31");
  });

  it("labels the politician field in plain language", async () => {
    mockFetchOnce();
    render(
      <MemoryRouter initialEntries={["/signals"]}>
        <SignalsPage />
      </MemoryRouter>,
    );
    await screen.findByText("NVDA");
    expect(screen.getByText("Politician")).toBeInTheDocument();
    expect(
      screen.getByPlaceholderText("Search politician..."),
    ).toBeInTheDocument();
    expect(screen.queryByText("Politician ID")).toBeNull();
  });

  it("sends the pattern span ceiling to the API", async () => {
    const fetchMock = vi.fn(
      async () => new Response(JSON.stringify(page([nvda])), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    render(
      <MemoryRouter initialEntries={["/signals"]}>
        <SignalsPage />
      </MemoryRouter>,
    );
    await screen.findByText("NVDA");
    expect(lastQuery(fetchMock).has("span_days_max")).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "≤ 7 days" }));

    await waitFor(() =>
      expect(lastQuery(fetchMock).get("span_days_max")).toBe("7"),
    );
  });

  it("omits the span parameter again when All is selected", async () => {
    const fetchMock = vi.fn(
      async () => new Response(JSON.stringify(page([nvda])), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    render(
      <MemoryRouter initialEntries={["/signals?span_days_max=7"]}>
        <SignalsPage />
      </MemoryRouter>,
    );
    await screen.findByText("NVDA");
    expect(lastQuery(fetchMock).get("span_days_max")).toBe("7");

    fireEvent.click(screen.getByRole("button", { name: "All" }));

    await waitFor(() =>
      expect(lastQuery(fetchMock).has("span_days_max")).toBe(false),
    );
  });

  it("restores the span ceiling from the URL", async () => {
    const fetchMock = vi.fn(
      async () => new Response(JSON.stringify(page([nvda])), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    render(
      <MemoryRouter initialEntries={["/signals?span_days_max=7"]}>
        <SignalsPage />
      </MemoryRouter>,
    );
    await screen.findByText("NVDA");
    expect(lastQuery(fetchMock).get("span_days_max")).toBe("7");
    expect(screen.getByRole("button", { name: "≤ 7 days" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: "All" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  it("treats a removed span ceiling as All instead of erroring", async () => {
    const fetchMock = vi.fn(
      async () => new Response(JSON.stringify(page([nvda])), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    render(
      <MemoryRouter initialEntries={["/signals?span_days_max=30"]}>
        <SignalsPage />
      </MemoryRouter>,
    );
    await screen.findByText("NVDA");
    expect(lastQuery(fetchMock).has("span_days_max")).toBe(false);
    expect(screen.getByRole("button", { name: "All" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("ignores an unrecognized span value instead of erroring", async () => {
    const fetchMock = vi.fn(
      async () => new Response(JSON.stringify(page([nvda])), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    render(
      <MemoryRouter initialEntries={["/signals?span_days_max=999"]}>
        <SignalsPage />
      </MemoryRouter>,
    );
    await screen.findByText("NVDA");
    expect(lastQuery(fetchMock).has("span_days_max")).toBe(false);
    expect(screen.getByRole("button", { name: "All" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("resets pagination when the span filter changes", async () => {
    const fetchMock = vi.fn(
      async () => new Response(JSON.stringify(page([nvda], 80)), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    render(
      <MemoryRouter initialEntries={["/signals?offset=50"]}>
        <SignalsPage />
      </MemoryRouter>,
    );
    await screen.findByText("NVDA");
    expect(lastQuery(fetchMock).get("offset")).toBe("50");

    fireEvent.click(screen.getByRole("button", { name: "≤ 7 days" }));

    await waitFor(() => expect(lastQuery(fetchMock).get("offset")).toBe("0"));
  });

  it("re-queries with the chosen sort key", async () => {
    const fetchMock = vi.fn(
      async () => new Response(JSON.stringify(page([nvda])), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    render(
      <MemoryRouter initialEntries={["/signals"]}>
        <SignalsPage />
      </MemoryRouter>,
    );
    await screen.findByText("NVDA");
    expect(lastQuery(fetchMock).get("sort")).toBe("-politician_count");

    fireEvent.click(screen.getByRole("button", { name: "Ticker" }));

    await waitFor(() => expect(lastQuery(fetchMock).get("sort")).toBe("-ticker"));
  });

  it("marks the active sort as pressed and shows its direction", async () => {
    mockFetchOnce();
    render(
      <MemoryRouter initialEntries={["/signals"]}>
        <SignalsPage />
      </MemoryRouter>,
    );
    await screen.findByText("NVDA");
    expect(
      screen.getByRole("button", { name: /Politicians/ }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(
      screen.getByRole("button", { name: /descending/ }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Ticker/ })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  it("flips the sort direction when the active key is clicked again", async () => {
    const fetchMock = vi.fn(
      async () => new Response(JSON.stringify(page([nvda])), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    render(
      <MemoryRouter initialEntries={["/signals?sort=-politician_count"]}>
        <SignalsPage />
      </MemoryRouter>,
    );
    await screen.findByText("NVDA");
    expect(lastQuery(fetchMock).get("sort")).toBe("-politician_count");

    fireEvent.click(screen.getByRole("button", { name: /Politicians/ }));

    await waitFor(() =>
      expect(lastQuery(fetchMock).get("sort")).toBe("politician_count"),
    );
    expect(screen.getByRole("button", { name: /ascending/ })).toBeInTheDocument();
  });

  it("reflects an ascending sort restored from the URL", async () => {
    mockFetchOnce();
    render(
      <MemoryRouter initialEntries={["/signals?sort=span_days"]}>
        <SignalsPage />
      </MemoryRouter>,
    );
    await screen.findByText("NVDA");
    expect(screen.getByRole("button", { name: /ascending/ })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Politicians/ }),
    ).toHaveAttribute("aria-pressed", "false");
  });

  it("surfaces an API error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ detail: "invalid sort key: nope" }), {
            status: 400,
          }),
      ),
    );
    render(
      <MemoryRouter initialEntries={["/signals"]}>
        <SignalsPage />
      </MemoryRouter>,
    );
    expect(
      await screen.findByText(/invalid sort key: nope/),
    ).toBeInTheDocument();
  });
});
