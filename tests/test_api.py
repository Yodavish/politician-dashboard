"""Integration tests for the read-only V1 API.

Run against a disposable, seeded PostgreSQL database via the ``api_client``
fixture. Exercises grouping, filtering, sorting, pagination, and error
handling for the read endpoints.
"""

from __future__ import annotations

ADERHOLT_ID = "al04_robert_aderholt"
PELOSI_ID = "ca11_nancy_pelosi"


class TestHealth:
    def test_health_ok(self, api_client):
        resp = api_client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["database"] == "ok"


class TestPoliticians:
    def test_list_returns_derived_politicians(self, api_client):
        resp = api_client.get("/politicians")
        assert resp.status_code == 200
        items = resp.json()["items"]
        ids = {p["id"] for p in items}
        assert {ADERHOLT_ID, PELOSI_ID} <= ids
        by_id = {p["id"]: p for p in items}
        aderholt = by_id[ADERHOLT_ID]
        assert aderholt["name"] == "Robert Aderholt"
        assert aderholt["state_district"] == "AL04"
        assert aderholt["state"] == "AL"
        assert aderholt["district"] == "04"
        assert aderholt["party"] is None
        # 2 filings, 4 transactions (2 per filing)
        assert aderholt["filing_count"] == 2
        assert aderholt["transaction_count"] == 4

    def test_list_filters_by_state(self, api_client):
        resp = api_client.get("/politicians", params={"state": "ca"})
        items = resp.json()["items"]
        assert {p["id"] for p in items} == {PELOSI_ID}

    def test_politician_detail(self, api_client):
        resp = api_client.get(f"/politicians/{ADERHOLT_ID}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == ADERHOLT_ID
        assert body["filing_count"] == 2
        assert body["transaction_count"] == 4

    def test_politician_detail_unknown_404(self, api_client):
        resp = api_client.get("/politicians/al99_unknown_person")
        assert resp.status_code == 404

    def test_politician_filings(self, api_client):
        resp = api_client.get(f"/politicians/{ADERHOLT_ID}/filings")
        items = resp.json()["items"]
        assert {f["doc_id"] for f in items} == {"20032062", "20026537"}

    def test_politician_filings_filter_year(self, api_client):
        resp = api_client.get(
            f"/politicians/{ADERHOLT_ID}/filings", params={"year": 2023}
        )
        items = resp.json()["items"]
        assert {f["doc_id"] for f in items} == {"20026537"}

    def test_politician_transactions(self, api_client):
        resp = api_client.get(f"/politicians/{ADERHOLT_ID}/transactions")
        items = resp.json()["items"]
        assert len(items) == 4
        tickers = {t["ticker"] for t in items}
        assert tickers == {"GSK", "AAPL", "VTI", "MSFT"}

    def test_politician_transactions_filter_ticker(self, api_client):
        resp = api_client.get(
            f"/politicians/{ADERHOLT_ID}/transactions", params={"ticker": "GSK"}
        )
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["ticker"] == "GSK"

    def test_politician_transactions_filter_txn_type_partial(self, api_client):
        resp = api_client.get(
            f"/politicians/{ADERHOLT_ID}/transactions", params={"txn_type": "S (partial)"}
        )
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["txn_type"] == "S (partial)"


class TestFilings:
    def test_list_returns_filings_with_counts(self, api_client):
        resp = api_client.get("/filings")
        body = resp.json()
        assert body["pagination"]["total"] == 3
        by_doc = {f["doc_id"]: f for f in body["items"]}
        assert by_doc["20032062"]["transaction_count"] == 2
        assert by_doc["20026537"]["transaction_count"] == 2
        assert by_doc["20026727"]["transaction_count"] == 2

    def test_list_filter_year(self, api_client):
        resp = api_client.get("/filings", params={"year": 2024})
        items = resp.json()["items"]
        assert {f["doc_id"] for f in items} == {"20026727"}

    def test_list_filter_state(self, api_client):
        resp = api_client.get("/filings", params={"state": "ca"})
        items = resp.json()["items"]
        assert {f["doc_id"] for f in items} == {"20026727"}

    def test_list_filter_politician(self, api_client):
        resp = api_client.get("/filings", params={"politician_id": PELOSI_ID})
        items = resp.json()["items"]
        assert {f["doc_id"] for f in items} == {"20026727"}

    def test_list_filter_filing_date_range(self, api_client):
        resp = api_client.get(
            "/filings",
            params={"filing_date_min": "2024-01-01", "filing_date_max": "2024-12-31"},
        )
        items = resp.json()["items"]
        assert {f["doc_id"] for f in items} == {"20026727"}

    def test_detail_does_not_expose_raw_pdf(self, api_client):
        resp = api_client.get("/filings/20032062")
        assert resp.status_code == 200
        body = resp.json()
        assert body["doc_id"] == "20032062"
        assert "raw_pdf" not in body
        assert len(body["transactions"]) == 2
        # transactions include derived politician_id
        assert all(t["politician_id"] == ADERHOLT_ID for t in body["transactions"])

    def test_detail_unknown_404(self, api_client):
        resp = api_client.get("/filings/99999999")
        assert resp.status_code == 404


class TestTransactions:
    def test_list_returns_transactions(self, api_client):
        resp = api_client.get("/transactions")
        body = resp.json()
        assert body["pagination"]["total"] == 6
        assert len(body["items"]) == 6
        first = body["items"][0]
        # Default sort is -txn_date
        for key in ("id", "filing_id", "doc_id", "sequence", "asset_name",
                    "amount_min", "amount_max", "amount_raw", "owner"):
            assert key in first
        assert first["politician_id"] in {ADERHOLT_ID, PELOSI_ID}

    def test_filter_politician(self, api_client):
        resp = api_client.get("/transactions", params={"politician_id": PELOSI_ID})
        items = resp.json()["items"]
        assert len(items) == 2
        assert all(t["politician_id"] == PELOSI_ID for t in items)

    def test_filter_ticker(self, api_client):
        resp = api_client.get("/transactions", params={"ticker": "nvda"})
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["ticker"] == "NVDA"

    def test_filter_owner(self, api_client):
        resp = api_client.get("/transactions", params={"owner": "Spouse"})
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["owner"] == "Spouse"

    def test_filter_amount_range(self, api_client):
        resp = api_client.get(
            "/transactions",
            params={"amount_min": 50001, "amount_max": 100000},
        )
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["ticker"] == "NVDA"

    def test_filter_amount_min_exceeds_max_400(self, api_client):
        resp = api_client.get(
            "/transactions", params={"amount_min": 100, "amount_max": 50}
        )
        assert resp.status_code == 400

    def test_filter_txn_date_range(self, api_client):
        resp = api_client.get(
            "/transactions",
            params={"txn_date_min": "2024-01-01", "txn_date_max": "2024-02-05"},
        )
        items = resp.json()["items"]
        assert len(items) == 2

    def test_invalid_sort_400(self, api_client):
        resp = api_client.get("/transactions", params={"sort": "bogus"})
        assert resp.status_code == 400


class TestPagination:
    def test_offset_limit(self, api_client):
        resp = api_client.get("/transactions", params={"limit": 2, "offset": 0})
        body = resp.json()
        assert len(body["items"]) == 2
        assert body["pagination"]["total"] == 6
        assert body["pagination"]["limit"] == 2
        assert body["pagination"]["offset"] == 0
        assert body["pagination"]["next_url"] is not None

        resp2 = api_client.get("/transactions", params={"limit": 2, "offset": 4})
        body2 = resp2.json()
        assert len(body2["items"]) == 2
        assert body2["pagination"]["offset"] == 4
        assert body2["pagination"]["next_url"] is None
        assert body2["pagination"]["prev_url"] is not None

    def test_page_contents_do_not_overlap(self, api_client):
        first = api_client.get("/transactions", params={"limit": 2, "offset": 0}).json()
        second = api_client.get("/transactions", params={"limit": 2, "offset": 2}).json()
        ids1 = {t["id"] for t in first["items"]}
        ids2 = {t["id"] for t in second["items"]}
        assert ids1.isdisjoint(ids2)

    def test_limit_bounds_rejected(self, api_client):
        assert api_client.get("/transactions", params={"limit": 0}).status_code == 422
        assert api_client.get("/transactions", params={"limit": 101}).status_code == 422


class TestSorting:
    def test_descending_default(self, api_client):
        resp = api_client.get("/transactions")
        items = resp.json()["items"]
        dates = [t["txn_date"] for t in items]
        assert dates == sorted(dates, reverse=True)

    def test_ascending_by_ticker(self, api_client):
        resp = api_client.get("/transactions", params={"sort": "ticker"})
        items = resp.json()["items"]
        tickers = [t["ticker"] for t in items]
        assert tickers == sorted(tickers)


class TestErrorHandling:
    def test_invalid_date_400(self, api_client):
        resp = api_client.get("/filings", params={"filing_date_min": "not-a-date"})
        assert resp.status_code == 400

    def test_unknown_endpoint_404(self, api_client):
        assert api_client.get("/nope").status_code == 404
