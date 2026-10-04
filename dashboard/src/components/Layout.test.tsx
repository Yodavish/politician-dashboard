import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";
import Layout from "./Layout";

describe("Layout navigation", () => {
  it("navigates to the selected page and marks its link current", async () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <Layout>
          <Routes>
            <Route path="/" element={<p>Overview route</p>} />
            <Route path="/transactions" element={<p>Trades route</p>} />
          </Routes>
        </Layout>
      </MemoryRouter>,
    );

    const tradesLink = screen.getByText("Recent trades").closest("a");
    expect(tradesLink).toHaveAttribute("href", "/transactions");
    fireEvent.click(tradesLink!);

    expect(await screen.findByText("Trades route")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Recent trades/ })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("provides a skip link to the main content", () => {
    render(
      <MemoryRouter>
        <Layout><p>Page content</p></Layout>
      </MemoryRouter>,
    );

    expect(screen.getByRole("link", { name: "Skip to main content" })).toHaveAttribute(
      "href",
      "#main-content",
    );
    expect(screen.getByRole("main")).toHaveAttribute("id", "main-content");
  });
});
