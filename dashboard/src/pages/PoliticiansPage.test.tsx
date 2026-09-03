import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import PoliticiansPage from "./PoliticiansPage";

const sample = {
  items: [
    {
      id: "al04_robert_aderholt",
      name: "Robert Aderholt",
      party: null,
      state: "AL",
      district: "04",
      state_district: "AL04",
      filing_count: 2,
      transaction_count: 4,
    },
  ],
  pagination: { limit: 100, offset: 0, total: 1, next_url: null, prev_url: null },
};

function mockFetchOnce(body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(body), { status: 200 })),
  );
}

beforeEach(() => {
  vi.unstubAllGlobals();
});

describe("PoliticiansPage", () => {
  it("renders the politician name and counts from the API", async () => {
    mockFetchOnce(sample);
    render(
      <MemoryRouter initialEntries={["/politicians"]}>
        <PoliticiansPage />
      </MemoryRouter>,
    );
    await screen.findByText("Robert Aderholt");
    expect(screen.getByText("AL04")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getByText("4")).toBeInTheDocument();
  });

  it("shows an empty state when there are no results", async () => {
    mockFetchOnce({ items: [], pagination: { total: 0 } });
    render(
      <MemoryRouter initialEntries={["/politicians"]}>
        <PoliticiansPage />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.queryByText("Robert Aderholt")).toBeNull());
    expect(screen.getByText(/No results/)).toBeInTheDocument();
  });
});
