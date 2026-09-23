import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import TransactionDate from "./TransactionDate";

describe("TransactionDate", () => {
  it("renders the transaction date with no warning when flags are empty", () => {
    const { container } = render(
      <TransactionDate txnDate="2026-12-26" flags={[]} filingDate="2026-02-09" />,
    );
    expect(screen.getByText("2026-12-26")).toBeInTheDocument();
    expect(container.querySelector("[title]")).not.toBeInTheDocument();
  });

  it("shows a warning indicator with a plain-language message for flagged dates", () => {
    render(
      <TransactionDate
        txnDate="2026-12-26"
        flags={["transaction_date_after_notification"]}
        filingDate="2026-02-09"
      />,
    );
    expect(screen.getByText("2026-12-26")).toBeInTheDocument();
    const warning = screen.getByRole("img");
    expect(warning).toHaveAttribute(
      "title",
      "Transaction date is after the notification date.",
    );
  });

  it("includes the filing date in the message when relevant", () => {
    render(
      <TransactionDate
        txnDate="2026-12-26"
        flags={["transaction_date_after_filing"]}
        filingDate="2026-02-09"
      />,
    );
    expect(screen.getByRole("img")).toHaveAttribute(
      "title",
      "Transaction date is after the filing date (2026-02-09).",
    );
  });
});
