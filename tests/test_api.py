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

    def test_list_filters_by_name_case_insensitively(self, api_client):
        resp = api_client.get("/politicians", params={"name": "nAnCy"})
        body = resp.json()
        assert [p["id"] for p in body["items"]] == [PELOSI_ID]
        assert body["pagination"]["total"] == 1

    def test_list_filters_by_name_and_state(self, api_client):
        matching = api_client.get(
            "/politicians", params={"name": "Nancy", "state": "ca"}
        ).json()
        assert [p["id"] for p in matching["items"]] == [PELOSI_ID]

        excluded = api_client.get(
            "/politicians", params={"name": "Nancy", "state": "al"}
        ).json()
        assert excluded["items"] == []
        assert excluded["pagination"]["total"] == 0

    def test_list_paginates_filtered_results(self, api_client):
        resp = api_client.get(
            "/politicians", params={"name": "a", "limit": 1, "offset": 1}
        )
        body = resp.json()
        assert [p["id"] for p in body["items"]] == [PELOSI_ID]
        assert body["pagination"]["total"] == 2
        assert body["pagination"]["offset"] == 1

    def test_list_sorting_by_name_state_and_numeric_counts(self, api_client):
        for sort, key in (
            ("name", "name"),
            ("state_district", "state_district"),
            ("filing_count", "filing_count"),
            ("transaction_count", "transaction_count"),
        ):
            body = api_client.get("/politicians", params={"sort": sort}).json()
            values = [p[key] for p in body["items"]]
            assert values == sorted(values), sort

        descending = api_client.get(
            "/politicians", params={"sort": "-transaction_count"}
        ).json()["items"]
        counts = [p["transaction_count"] for p in descending]
        assert counts == sorted(counts, reverse=True)

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

    def test_detail_exposes_amendment_relationships_and_verification(self, api_client):
        import psycopg

        with psycopg.connect(api_client.app.state.database_url, autocommit=True) as conn:
            conn.execute(
                "UPDATE filings SET amends_filing_id = "
                "(SELECT id FROM filings WHERE doc_id = '20026537'), "
                "amendment_method = 'amendment_match', amendment_confidence = 'medium', "
                "amendment_note = 'Curated relationship' "
                "WHERE doc_id = '20026727'"
            )
            conn.execute(
                "UPDATE transactions SET verified_transaction_date = '2025-04-17', "
                "verification_method = 'amendment_match', "
                "verification_confidence = 'high', "
                "verification_source_doc_id = '20026727', "
                "verification_note = 'Source confirmation', verified_at = now() "
                "WHERE ticker = 'CSCO'"
            )
        original = api_client.get("/filings/20026537").json()
        assert original["amendments"][0]["doc_id"] == "20026727"
        assert original["amendments"][0]["amendment_method"] == "amendment_match"
        amended = api_client.get("/filings/20026727").json()
        assert amended["amends_doc_id"] == "20026537"
        tx = next(t for t in amended["transactions"] if t["ticker"] == "CSCO")
        assert tx["verified_transaction_date"] == "2025-04-17"
        assert tx["verification_source_doc_exists"] is True
        assert tx["txn_date"] == "2024-02-05"


class TestTransactions:
    def test_list_returns_transactions(self, api_client):
        resp = api_client.get("/transactions")
        body = resp.json()
        assert body["pagination"]["total"] == 6
        assert len(body["items"]) == 6
        first = body["items"][0]
        # Default sort is -txn_date
        for key in ("id", "filing_id", "doc_id", "sequence", "asset_name",
                    "amount_min", "amount_max", "amount_raw", "owner",
                    "asset_type_code", "disclosure_lag_days"):
            assert key in first
        assert first["politician_id"] in {ADERHOLT_ID, PELOSI_ID}
        assert first["politician_name"] in {"Robert Aderholt", "Nancy Pelosi"}
        # Newest transaction sorts from the filing whose filing_date is null
        # in the fixture, so the field must be exposed and nullable.
        assert first["filing_date"] is None
        assert first["quality_flags"] == []
        assert first["verified_transaction_date"] is None
        assert first["verification_method"] is None
        assert first["verification_source_doc_exists"] is False

    def test_disclosure_lag_calculation_and_missing_filing_date(self, api_client):
        import psycopg

        # The seeded 20032062 filing has no filing date.
        initial = api_client.get(
            "/transactions", params={"ticker": "GSK"}
        ).json()["items"]
        assert initial[0]["disclosure_lag_days"] is None

        with psycopg.connect(api_client.app.state.database_url, autocommit=True) as conn:
            conn.execute(
                "UPDATE filings SET filing_date = '2025-07-28' "
                "WHERE doc_id = '20032062'"
            )
        same_day_gsk = api_client.get(
            "/transactions", params={"ticker": "GSK"}
        ).json()["items"][0]
        same_day_aapl = api_client.get(
            "/transactions", params={"ticker": "AAPL"}
        ).json()["items"][0]
        assert same_day_gsk["disclosure_lag_days"] == 0
        assert same_day_aapl["disclosure_lag_days"] == 8

        with psycopg.connect(api_client.app.state.database_url, autocommit=True) as conn:
            conn.execute(
                "UPDATE filings SET filing_date = '2025-08-31' "
                "WHERE doc_id = '20032062'"
            )
        multi_day_gsk = api_client.get(
            "/transactions", params={"ticker": "GSK"}
        ).json()["items"][0]
        multi_day_aapl = api_client.get(
            "/transactions", params={"ticker": "AAPL"}
        ).json()["items"][0]
        assert multi_day_gsk["disclosure_lag_days"] == 34
        assert multi_day_aapl["disclosure_lag_days"] == 42

    def test_asset_type_field_and_existing_filter(self, api_client):
        body = api_client.get(
            "/transactions", params={"asset_type_code": "ST"}
        ).json()
        assert body["pagination"]["total"] == 6
        assert all(item["asset_type_code"] == "ST" for item in body["items"])

    def test_negative_disclosure_lag_is_preserved(self, api_client):
        import psycopg

        # The official 20033889 source and parser regression tests preserve
        # 2026-12-26; this pins the serializer's signed date difference too.
        with psycopg.connect(api_client.app.state.database_url, autocommit=True) as conn:
            conn.execute(
                "UPDATE filings SET filing_date = '2026-02-09' "
                "WHERE doc_id = '20032062'"
            )
            conn.execute(
                "UPDATE transactions SET txn_date = '2026-12-26', "
                "notification_date = '2026-01-21' "
                "WHERE filing_id = (SELECT id FROM filings "
                "WHERE doc_id = '20032062') AND ticker = 'GSK'"
            )

        transaction = api_client.get(
            "/transactions", params={"ticker": "GSK"}
        ).json()["items"][0]
        assert transaction["disclosure_lag_days"] == -320

    def test_quality_flags_exposed(self, api_client):
        resp = api_client.get("/transactions", params={"doc_id": "20032062"})
        assert resp.status_code == 200
        items = resp.json()["items"]
        by_ticker = {t["ticker"]: t for t in items}
        assert by_ticker["AAPL"]["quality_flags"] == [
            "transaction_date_after_notification",
        ]
        assert by_ticker["GSK"]["quality_flags"] == []

    def test_filter_politician(self, api_client):
        resp = api_client.get("/transactions", params={"politician_id": PELOSI_ID})
        items = resp.json()["items"]
        assert len(items) == 2
        assert all(t["politician_id"] == PELOSI_ID for t in items)
        assert all(t["politician_name"] == "Nancy Pelosi" for t in items)
        assert all(t["filing_date"] == "2024-03-10" for t in items)

    def test_filter_politician_name_case_insensitive_partial(self, api_client):
        body = api_client.get(
            "/transactions", params={"politician_name": "nAnCy pEl"}
        ).json()
        assert body["pagination"]["total"] == 2
        assert len(body["items"]) == 2
        assert all(t["politician_name"] == "Nancy Pelosi" for t in body["items"])

    def test_filter_politician_name_combines_with_ticker(self, api_client):
        body = api_client.get(
            "/transactions",
            params={"politician_name": "Nancy", "ticker": "nvda"},
        ).json()
        assert body["pagination"]["total"] == 1
        assert len(body["items"]) == 1
        assert body["items"][0]["politician_name"] == "Nancy Pelosi"
        assert body["items"][0]["ticker"] == "NVDA"

    def test_filter_politician_name_before_pagination(self, api_client):
        body = api_client.get(
            "/transactions",
            params={"politician_name": "Nancy", "limit": 1, "offset": 1},
        ).json()
        assert body["pagination"]["total"] == 2
        assert body["pagination"]["offset"] == 1
        assert len(body["items"]) == 1
        assert body["items"][0]["politician_name"] == "Nancy Pelosi"
        assert body["items"][0]["ticker"] == "NVDA"

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

    def test_all_recent_trade_sort_fields_are_supported(self, api_client):
        for sort in (
            "txn_date", "politician_name", "asset_name", "ticker", "txn_type",
            "owner", "asset_type_code", "amount_min", "disclosure_lag_days",
            "doc_id",
        ):
            response = api_client.get("/transactions", params={"sort": sort})
            assert response.status_code == 200, sort
            assert response.json()["pagination"]["total"] == 6

    def test_asset_type_sorts_by_display_label_before_pagination(self, api_client):
        import psycopg

        with psycopg.connect(api_client.app.state.database_url, autocommit=True) as conn:
            conn.execute(
                "UPDATE transactions SET asset_type_code = CASE ticker "
                "WHEN 'GSK' THEN 'ST' WHEN 'AAPL' THEN 'BA' "
                "WHEN 'VTI' THEN 'CS' WHEN 'MSFT' THEN NULL "
                "WHEN 'CSCO' THEN 'GS' WHEN 'NVDA' THEN 'ST' END"
            )

        labels = {
            "BA": "Bank Accounts, Money Market Accounts and CDs",
            "CS": "Corporate Securities (Bonds and Notes)",
            "GS": "Government Securities and Agency Debt",
            "ST": "Stocks (including ADRs)",
        }
        for sort, expected in (
            ("asset_type_code", ["BA", "CS", "GS", "ST", "ST", None]),
            ("-asset_type_code", ["ST", "ST", "GS", "CS", "BA", None]),
        ):
            items = api_client.get(
                "/transactions", params={"sort": sort}
            ).json()["items"]
            codes = [item["asset_type_code"] for item in items]
            assert codes == expected

            # Check the visible labels, rather than code order, for the known
            # asset types represented in this fixture.
            display_labels = [labels[code] for code in codes if code is not None]
            assert display_labels == sorted(
                display_labels, reverse=sort.startswith("-")
            )

            paged = [
                item["asset_type_code"]
                for offset in (0, 2, 4)
                for item in api_client.get(
                    "/transactions",
                    params={"sort": sort, "limit": 2, "offset": offset},
                ).json()["items"]
            ]
            assert paged == expected

    def test_disclosure_lag_sorts_before_pagination_and_keeps_nulls_last(
        self, api_client
    ):
        import psycopg

        with psycopg.connect(api_client.app.state.database_url, autocommit=True) as conn:
            conn.execute(
                "UPDATE filings SET filing_date = CASE doc_id "
                "WHEN '20032062' THEN NULL "
                "WHEN '20026537' THEN DATE '2023-04-11' "
                "WHEN '20026727' THEN DATE '2024-02-12' END"
            )

        for sort, reverse in (
            ("disclosure_lag_days", False),
            ("-disclosure_lag_days", True),
        ):
            items = api_client.get(
                "/transactions", params={"sort": sort}
            ).json()["items"]
            lags = [item["disclosure_lag_days"] for item in items]
            present = [lag for lag in lags if lag is not None]
            assert present == sorted(present, reverse=reverse)
            assert lags[-2:] == [None, None]

            paged = [
                item["id"]
                for offset in (0, 2, 4)
                for item in api_client.get(
                    "/transactions",
                    params={"sort": sort, "limit": 2, "offset": offset},
                ).json()["items"]
            ]
            assert paged == [item["id"] for item in items]
    def test_recent_trade_text_and_date_sorting(self, api_client):
        for sort, key in (
            ("politician_name", "politician_name"),
            ("asset_name", "asset_name"),
            ("ticker", "ticker"),
            ("txn_type", "txn_type"),
            ("owner", "owner"),
            ("doc_id", "doc_id"),
        ):
            items = api_client.get(
                "/transactions", params={"sort": sort}
            ).json()["items"]
            values = [item[key] for item in items]
            present = [value.lower() for value in values if value is not None]
            assert present == sorted(present), sort
            assert values == [value for value in values if value is not None] + [
                value for value in values if value is None
            ], sort

        dates = api_client.get(
            "/transactions", params={"sort": "txn_date"}
        ).json()["items"]
        assert [item["txn_date"] for item in dates] == sorted(
            item["txn_date"] for item in dates
        )
        descending_dates = api_client.get(
            "/transactions", params={"sort": "-txn_date"}
        ).json()["items"]
        assert [item["txn_date"] for item in descending_dates] == sorted(
            (item["txn_date"] for item in descending_dates), reverse=True
        )

    def test_amount_sort_uses_numeric_bounds_and_paginates_after_sort(self, api_client):
        all_items = api_client.get(
            "/transactions", params={"sort": "amount_min"}
        ).json()["items"]
        expected = sorted(
            all_items, key=lambda item: (item["amount_min"], item["amount_max"], item["id"])
        )
        assert [item["id"] for item in all_items] == [item["id"] for item in expected]

        page = api_client.get(
            "/transactions", params={"sort": "amount_min", "limit": 2, "offset": 2}
        ).json()
        assert [item["id"] for item in page["items"]] == [
            item["id"] for item in expected[2:4]
        ]
        assert page["pagination"]["total"] == len(expected)

    def test_open_ended_amount_serializes_amount_max_as_null(self, api_client):
        """An eFD "Over $X" amount must serialize as (min, null).

        float(None) would raise inside the serializer, so this also guards
        against a 500 on the transaction list.
        """
        import psycopg

        with psycopg.connect(api_client.app.state.database_url, autocommit=True) as conn:
            conn.execute(
                "UPDATE transactions SET amount_max = NULL, "
                "amount_min = 50000000, amount_raw = 'Over $50,000,000' "
                "WHERE id = (SELECT min(id) FROM transactions)"
            )

        items = api_client.get("/transactions").json()["items"]
        open_ended = [i for i in items if i["amount_raw"] == "Over $50,000,000"]
        assert len(open_ended) == 1
        assert open_ended[0]["amount_min"] == 50000000
        assert open_ended[0]["amount_max"] is None

    def test_null_tickers_sort_last_in_both_directions(self, api_client):
        import psycopg

        with psycopg.connect(api_client.app.state.database_url, autocommit=True) as conn:
            conn.execute("UPDATE transactions SET ticker = NULL WHERE ticker = 'GSK'")

        for sort in ("ticker", "-ticker"):
            items = api_client.get("/transactions", params={"sort": sort}).json()["items"]
            assert items[-1]["ticker"] is None


class TestErrorHandling:
    def test_invalid_date_400(self, api_client):
        resp = api_client.get("/filings", params={"filing_date_min": "not-a-date"})
        assert resp.status_code == 400

    def test_unknown_endpoint_404(self, api_client):
        assert api_client.get("/nope").status_code == 404
