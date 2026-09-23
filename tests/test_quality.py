"""Tests for derived data-quality signals."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from politician_dashboard.ingest.models import Filing, Transaction
from politician_dashboard.ingest.quality import (
    AFTER_FILING,
    AFTER_INGESTION_DATE,
    AFTER_NOTIFICATION,
    NOTIFICATION_AFTER_FILING,
    transaction_date_anomalies,
)

FILE = date(2026, 2, 9)
INGEST_DATE = date(2026, 2, 9)


def _filing(state_district: str = "TN09") -> Filing:
    return Filing(
        prefix="Hon.",
        last="Cohen",
        first="Steve",
        suffix="",
        filing_type="P",
        state_district=state_district,
        year=2026,
        filing_date=FILE,
        doc_id="20033889",
    )


def _transaction(
    txn_date: date,
    notification_date: date,
    asset_name: str = "Sony Group Corporation ADR (SONY)",
) -> Transaction:
    return Transaction(
        sequence=0,
        asset_name=asset_name,
        txn_type="P",
        txn_date=txn_date,
        notification_date=notification_date,
        amount_min=1001,
        amount_max=15000,
        amount_raw="$1,001 - $15,000",
        ticker="SONY",
        asset_type_code="ST",
    )


class TestTransactionDateAnomalies:
    def test_cohen_sony_source_anomaly_is_detected(self) -> None:
        # Official filing 20033889: txn 12/26/2026 after notification
        # 01/21/2026, after signature 02/09/2026, and after the ingestion
        # date of the run that stored it.
        violations = transaction_date_anomalies(
            _filing(),
            _transaction(date(2026, 12, 26), date(2026, 1, 21)),
            INGEST_DATE,
        )
        assert violations == [
            AFTER_NOTIFICATION,
            AFTER_FILING,
            AFTER_INGESTION_DATE,
        ]

    def test_notification_after_filing_is_detected(self) -> None:
        # House filing 20018054 (Rep. James Comer, KY01): txn 12/31/2024 is
        # before its 01/31/2025 notification, but the notification postdates
        # the filing (filed/signed 01/10/2025) - the report was signed before
        # it claims the trade was notified.
        filing = Filing(
            prefix="Hon.",
            last="Comer",
            first="James",
            suffix="",
            filing_type="P",
            state_district="KY01",
            year=2025,
            filing_date=date(2025, 1, 10),
            doc_id="20018054",
        )
        violations = transaction_date_anomalies(
            filing,
            _transaction(
                date(2024, 12, 31),
                date(2025, 1, 31),
                asset_name="Southwest Airlines Co (LUV)",
            ),
            date(2025, 2, 1),
        )
        assert violations == [NOTIFICATION_AFTER_FILING]

    def test_forward_dated_transaction_is_flagged(self) -> None:
        # A transaction date after the explicit ingestion date of the run is
        # flagged even when its other dates are internally consistent.
        filing = replace(_filing(), filing_date=date(2027, 1, 15))
        violations = transaction_date_anomalies(
            filing,
            _transaction(
                date(2026, 12, 26),
                date(2026, 12, 30),
                asset_name="Sony Group Corporation ADR (SONY)",
            ),
            INGEST_DATE,
        )
        assert violations == [AFTER_INGESTION_DATE]

    def test_transaction_on_ingestion_day_is_not_flagged(self) -> None:
        # A transaction on the ingestion date itself is not forward-dated.
        filing = replace(_filing(), filing_date=date(2026, 2, 12))
        violations = transaction_date_anomalies(
            filing,
            _transaction(date(2026, 2, 9), date(2026, 2, 11)),
            INGEST_DATE,
        )
        assert violations == []

    def test_normal_transaction_is_not_flagged(self) -> None:
        violations = transaction_date_anomalies(
            _filing(), _transaction(date(2026, 1, 5), date(2026, 1, 21)), INGEST_DATE
        )
        assert violations == []

    def test_transaction_on_notification_day_is_not_flagged(self) -> None:
        violations = transaction_date_anomalies(
            _filing(),
            _transaction(date(2026, 1, 21), date(2026, 1, 21)),
            INGEST_DATE,
        )
        assert violations == []

    def test_alert_does_not_require_filing_date(self) -> None:
        filing = replace(_filing(), filing_date=None)
        violations = transaction_date_anomalies(
            filing,
            _transaction(date(2026, 1, 5), date(2025, 12, 15)),
            INGEST_DATE,
        )
        assert violations == [AFTER_NOTIFICATION]