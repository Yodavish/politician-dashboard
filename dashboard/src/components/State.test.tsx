import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Empty, ErrorMessage, Loading } from "./State";

describe("shared page states", () => {
  it("announces loading progress as a polite status", () => {
    render(<Loading label="Loading trades…" />);

    expect(screen.getByRole("status")).toHaveAttribute("aria-busy", "true");
    expect(screen.getByText("Loading trades…")).toBeInTheDocument();
  });

  it("exposes errors as alerts and keeps the supplied message", () => {
    render(<ErrorMessage message="Unable to load records." />);

    expect(screen.getByRole("alert")).toHaveTextContent("Unable to load records.");
  });

  it("renders a helpful empty state", () => {
    render(<Empty message="No trades match these filters." />);

    expect(screen.getByTestId("empty-state")).toHaveTextContent(
      "No trades match these filters.",
    );
  });
});
