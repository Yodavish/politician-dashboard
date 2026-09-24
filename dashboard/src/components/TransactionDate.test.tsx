import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import TransactionDate from "./TransactionDate";

describe("TransactionDate", () => {
  it("renders the transaction date with no warning when flags are empty", () => {
    const { container } = render(
      <TransactionDate txnDate="2026-12-26" flags={[]} filingDate="2026-02-09" />,
    );
    expect(screen.getByText("12/26/2026")).toBeInTheDocument();
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
    expect(screen.getByText("12/26/2026")).toBeInTheDocument();
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

  it("shows a verified date prominently and keeps the warned source date", () => {
    render(
      <TransactionDate
        txnDate="2025-05-17"
        verifiedDate="2025-04-17"
        flags={["transaction_date_after_notification"]}
        filingDate="2025-06-01"
      />,
    );
    expect(screen.getByText("04/17/2025")).toBeInTheDocument();
    expect(screen.getByText("Verified")).toBeInTheDocument();
    expect(screen.getByText(/Source reported: 05\/17\/2025/)).toBeInTheDocument();
    expect(screen.getByRole("img")).toHaveAttribute(
      "title",
      "Transaction date is after the notification date.",
    );
  });
});
