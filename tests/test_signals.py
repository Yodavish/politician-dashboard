"""Tests for the computed buy-cluster signal.

The signal is derived on read, so these tests seed a disposable database with
trades engineered to hit each branch of the rule: the three-politician
threshold, the 7-day gap, the 14-day span, ticker validation, and the
future-date exclusion. They also pin the signal id, which the detail route
resolves without any stored mapping.
"""

from __future__ import annotations

from datetime import date, timedelta

import psycopg
import pytest

from politician_dashboard.ingest.models import Filing, Transaction
from politician_dashboard.ingest.store import store_filing

# Four distinct people, one filing each, reused across scenarios.
PEOPLE = [
    ("AL04", "Robert", "Aderholt", "aderholt"),
    ("CA11", "Nancy", "Pelosi", "pelosi"),
    ("TX32", "Julie", "Johnson", "johnson"),
    ("GA14", "Marjorie Taylor", "Greene", "greene"),
]

Aderholt = PEOPLE[0][3]
Pelosi = PEOPLE[1][3]
Johnson = PEOPLE[2][3]
Greene = PEOPLE[3][3]

# Anchored well in the past so the fixtures never drift across a date
# boundary as the suite ages.
BASE = date(2025, 1, 6)
FUTURE = date.today() + timedelta(days=30)


def _txn(sequence: int, *, txn_date: date, ticker: str, **kwargs) -> Transaction:
    defaults = {
        "asset_name": f"{ticker} Corp Common Stock ({ticker})",
        "txn_type": "P",
        "notification_date": txn_date + timedelta(days=12),
        "amount_min": 1001,
        "amount_max": 15000,
        "amount_raw": "$1,001 - $15,000",
        "owner_token": "Self",
        "asset_type_code": "ST",
    }
    defaults.update(kwargs)
    return Transaction(sequence=sequence, ticker=ticker, txn_date=txn_date, **defaults)


def _seed(url: str) -> None:
    """Insert one filing per trade group, keyed by the person's doc id."""
    counter = 0

    def put(person, ticker, trades):
        nonlocal counter
        district, first, last, _key = person
        counter += 1
        doc_id = f"9000{counter:04d}"
        with psycopg.connect(url, autocommit=True) as conn:
            store_filing(
                conn,
                filing=Filing(
                    prefix="", suffix="", first=first, last=last,
                    filing_type="P", state_district=district, year=2025,
                    filing_date=max(t.txn_date for t in trades) + timedelta(days=30),
                    doc_id=doc_id,
                ),
                transactions=trades,
                raw_pdf=b"%PDF-1.4 fake",
                pdf_url=f"https://example.invalid/2025/{doc_id}.pdf",
                doc_kind="efiled",
            )
        return doc_id

    people = {key: (district, first, last, key)
              for district, first, last, key in PEOPLE}

    # Valid cluster: three politicians, four-day window.
    put(people[Aderholt], "ALPHA", [_txn(0, txn_date=BASE, ticker="ALPHA")])
    put(people[Pelosi], "ALPHA", [
        _txn(0, txn_date=BASE + timedelta(days=2), ticker="ALPHA")])
    put(people[Johnson], "ALPHA", [
        _txn(0, txn_date=BASE + timedelta(days=4), ticker="ALPHA")])

    # Only two politicians: below the threshold.
    put(people[Aderholt], "TWO", [_txn(0, txn_date=BASE, ticker="TWO")])
    put(people[Pelosi], "TWO", [_txn(0, txn_date=BASE, ticker="TWO")])

    # Gaps of exactly 7 days stay in one burst (span lands on 14).
    put(people[Aderholt], "GAP7", [_txn(0, txn_date=BASE, ticker="GAP7")])
    put(people[Pelosi], "GAP7", [
        _txn(0, txn_date=BASE + timedelta(days=7), ticker="GAP7")])
    put(people[Johnson], "GAP7", [
        _txn(0, txn_date=BASE + timedelta(days=14), ticker="GAP7")])

    # Gaps of 8 days split into three single-person bursts.
    put(people[Aderholt], "GAP8", [_txn(0, txn_date=BASE, ticker="GAP8")])
    put(people[Pelosi], "GAP8", [
        _txn(0, txn_date=BASE + timedelta(days=8), ticker="GAP8")])
    put(people[Johnson], "GAP8", [
        _txn(0, txn_date=BASE + timedelta(days=16), ticker="GAP8")])

    # Gaps of 7 across four people: one burst, but 21 days wide, so the span
    # rule rejects it.
    put(people[Aderholt], "LONG", [_txn(0, txn_date=BASE, ticker="LONG")])
    put(people[Pelosi], "LONG", [
        _txn(0, txn_date=BASE + timedelta(days=7), ticker="LONG")])
    put(people[Johnson], "LONG", [
        _txn(0, txn_date=BASE + timedelta(days=14), ticker="LONG")])
    put(people[Greene], "LONG", [
        _txn(0, txn_date=BASE + timedelta(days=21), ticker="LONG")])

    # One politician filing several purchases counts once.
    put(people[Aderholt], "REPT", [
        _txn(0, txn_date=BASE, ticker="REPT"),
        _txn(1, txn_date=BASE + timedelta(days=1), ticker="REPT"),
        _txn(2, txn_date=BASE + timedelta(days=2), ticker="REPT"),
    ])
    put(people[Pelosi], "REPT", [
        _txn(0, txn_date=BASE + timedelta(days=1), ticker="REPT")])
    put(people[Johnson], "REPT", [
        _txn(0, txn_date=BASE + timedelta(days=2), ticker="REPT")])

    # "--" is the parser's placeholder for assets with no ticker (municipal
    # bonds), so three bond purchases must not look like a cluster.
    put(people[Aderholt], "--", [_txn(0, txn_date=BASE, ticker="--")])
    put(people[Pelosi], "--", [_txn(0, txn_date=BASE, ticker="--")])
    put(people[Johnson], "--", [_txn(0, txn_date=BASE, ticker="--")])

    # Sales never qualify, however many politicians make them.
    put(people[Aderholt], "SELL", [
        _txn(0, txn_date=BASE, ticker="SELL", txn_type="S")])
    put(people[Pelosi], "SELL", [
        _txn(0, txn_date=BASE, ticker="SELL", txn_type="S")])
    put(people[Johnson], "SELL", [
        _txn(0, txn_date=BASE, ticker="SELL", txn_type="S")])

    # Three politicians buying on a future date is not evidence of anything yet.
    put(people[Aderholt], "FUTR", [_txn(0, txn_date=FUTURE, ticker="FUTR")])
    put(people[Pelosi], "FUTR", [
        _txn(0, txn_date=FUTURE, ticker="FUTR")])
    put(people[Johnson], "FUTR", [
        _txn(0, txn_date=FUTURE, ticker="FUTR")])

    # Distinct ranges, to prove the total stays a range.
    put(people[Aderholt], "MONEY", [
        _txn(0, txn_date=BASE, ticker="MONEY", amount_min=1001, amount_max=15000)])
    put(people[Pelosi], "MONEY", [
        _txn(0, txn_date=BASE + timedelta(days=1), ticker="MONEY",
             amount_min=15001, amount_max=50000)])
    put(people[Johnson], "MONEY", [
        _txn(0, txn_date=BASE + timedelta(days=2), ticker="MONEY",
             amount_min=50001, amount_max=100000)])

    # A cluster containing one open-ended amount ("Over $X"). The summed
    # upper bound is unknown, so total_max must be null rather than a sum that
    # silently omits the open-ended member.
    put(people[Aderholt], "OPEN", [
        _txn(0, txn_date=BASE, ticker="OPEN", amount_min=1001, amount_max=15000)])
    put(people[Pelosi], "OPEN", [
        _txn(0, txn_date=BASE + timedelta(days=1), ticker="OPEN",
             amount_min=15001, amount_max=50000)])
    put(people[Johnson], "OPEN", [
        _txn(0, txn_date=BASE + timedelta(days=2), ticker="OPEN",
             amount_min=50000000, amount_max=None,
             amount_raw="Over $50,000,000")])

    # --- Sell-cluster fixtures -------------------------------------------
    # Same mechanics as the buy side, read from 'S' instead of 'P'.

    put(people[Aderholt], "SELL3", [
        _txn(0, txn_date=BASE, ticker="SELL3", txn_type="S")])
    put(people[Pelosi], "SELL3", [
        _txn(0, txn_date=BASE + timedelta(days=2), ticker="SELL3", txn_type="S")])
    put(people[Johnson], "SELL3", [
        _txn(0, txn_date=BASE + timedelta(days=4), ticker="SELL3", txn_type="S")])

    put(people[Aderholt], "SELLTWO", [
        _txn(0, txn_date=BASE, ticker="SELLTWO", txn_type="S")])
    put(people[Pelosi], "SELLTWO", [
        _txn(0, txn_date=BASE + timedelta(days=2), ticker="SELLTWO", txn_type="S")])

    put(people[Aderholt], "SGAP7", [
        _txn(0, txn_date=BASE, ticker="SGAP7", txn_type="S")])
    put(people[Pelosi], "SGAP7", [
        _txn(0, txn_date=BASE + timedelta(days=7), ticker="SGAP7", txn_type="S")])
    put(people[Johnson], "SGAP7", [
        _txn(0, txn_date=BASE + timedelta(days=14), ticker="SGAP7", txn_type="S")])

    put(people[Aderholt], "SGAP8", [
        _txn(0, txn_date=BASE, ticker="SGAP8", txn_type="S")])
    put(people[Pelosi], "SGAP8", [
        _txn(0, txn_date=BASE + timedelta(days=8), ticker="SGAP8", txn_type="S")])
    put(people[Johnson], "SGAP8", [
        _txn(0, txn_date=BASE + timedelta(days=16), ticker="SGAP8", txn_type="S")])

    put(people[Aderholt], "SLONG", [
        _txn(0, txn_date=BASE, ticker="SLONG", txn_type="S")])
    put(people[Pelosi], "SLONG", [
        _txn(0, txn_date=BASE + timedelta(days=7), ticker="SLONG", txn_type="S")])
    put(people[Johnson], "SLONG", [
        _txn(0, txn_date=BASE + timedelta(days=14), ticker="SLONG", txn_type="S")])
    put(people[Greene], "SLONG", [
        _txn(0, txn_date=BASE + timedelta(days=21), ticker="SLONG", txn_type="S")])

    # One politician filing several sales counts once.
    put(people[Aderholt], "SREPT", [
        _txn(0, txn_date=BASE, ticker="SREPT", txn_type="S"),
        _txn(1, txn_date=BASE + timedelta(days=1), ticker="SREPT", txn_type="S"),
        _txn(2, txn_date=BASE + timedelta(days=2), ticker="SREPT", txn_type="S"),
    ])
    put(people[Pelosi], "SREPT", [
        _txn(0, txn_date=BASE + timedelta(days=1), ticker="SREPT", txn_type="S")])
    put(people[Johnson], "SREPT", [
        _txn(0, txn_date=BASE + timedelta(days=2), ticker="SREPT", txn_type="S")])

    # Partial sales are a separate disclosure category and must not be folded
    # into the sell signal, however many politicians make them.
    put(people[Aderholt], "SPART", [
        _txn(0, txn_date=BASE, ticker="SPART", txn_type="S (partial)")])
    put(people[Pelosi], "SPART", [
        _txn(0, txn_date=BASE + timedelta(days=2), ticker="SPART",
             txn_type="S (partial)")])
    put(people[Johnson], "SPART", [
        _txn(0, txn_date=BASE + timedelta(days=4), ticker="SPART",
             txn_type="S (partial)")])

    # Estate transfers are not open-market sales either.
    put(people[Aderholt], "SESTATE", [
        _txn(0, txn_date=BASE, ticker="SESTATE", txn_type="E")])
    put(people[Pelosi], "SESTATE", [
        _txn(0, txn_date=BASE + timedelta(days=2), ticker="SESTATE", txn_type="E")])
    put(people[Johnson], "SESTATE", [
        _txn(0, txn_date=BASE + timedelta(days=4), ticker="SESTATE", txn_type="E")])

    # Sales on a future date are not evidence of anything yet.
    put(people[Aderholt], "SFUTR", [
        _txn(0, txn_date=FUTURE, ticker="SFUTR", txn_type="S")])
    put(people[Pelosi], "SFUTR", [
        _txn(0, txn_date=FUTURE, ticker="SFUTR", txn_type="S")])
    put(people[Johnson], "SFUTR", [
        _txn(0, txn_date=FUTURE, ticker="SFUTR", txn_type="S")])


@pytest.fixture()
def signal_client(temp_database_url: str):
    from fastapi.testclient import TestClient

    from politician_dashboard.api import create_app

    _seed(temp_database_url)
    with TestClient(create_app(database_url=temp_database_url)) as client:
        yield client


def _signals(client, **params):
    resp = client.get("/signals", params=params)
    assert resp.status_code == 200, resp.text
    return {s["ticker"]: s for s in resp.json()["items"]}


def _sell_signals(client, **params):
    return _signals(client, type="sell_cluster", **params)


class TestBuyClusterRule:
    def test_three_politicians_over_four_days_is_a_signal(self, signal_client):
        got = _signals(signal_client)
        assert "ALPHA" in got
        cluster = got["ALPHA"]
        assert cluster["type"] == "buy_cluster"
        assert cluster["politician_count"] == 3
        assert cluster["transaction_count"] == 3
        assert cluster["start_date"] == BASE.isoformat()
        assert cluster["end_date"] == (BASE + timedelta(days=4)).isoformat()
        assert cluster["span_days"] == 4
        assert len(cluster["politicians"]) == 3

    def test_two_politicians_do_not_reach_the_threshold(self, signal_client):
        assert "TWO" not in _signals(signal_client)

    def test_gap_of_exactly_seven_days_stays_in_one_burst(self, signal_client):
        cluster = _signals(signal_client)["GAP7"]
        assert cluster["politician_count"] == 3
        assert cluster["transaction_count"] == 3
        assert cluster["span_days"] == 14

    def test_gap_of_eight_days_splits_bursts(self, signal_client):
        assert "GAP8" not in _signals(signal_client)

    def test_span_beyond_fourteen_days_is_rejected(self, signal_client):
        # Four politicians with 7-day gaps, but a 21-day span.
        assert "LONG" not in _signals(signal_client)

    def test_repeated_purchases_by_one_politician_count_once(self, signal_client):
        cluster = _signals(signal_client)["REPT"]
        assert cluster["politician_count"] == 3
        assert cluster["transaction_count"] == 5

    def test_placeholder_ticker_is_excluded(self, signal_client):
        assert "--" not in _signals(signal_client)

    def test_sales_are_excluded(self, signal_client):
        assert "SELL" not in _signals(signal_client)

    def test_future_transaction_dates_are_excluded(self, signal_client):
        assert "FUTR" not in _signals(signal_client)


class TestSellClusterRule:
    def test_three_selling_politicians_over_four_days_is_a_signal(
        self, signal_client,
    ):
        got = _sell_signals(signal_client)
        assert "SELL3" in got
        cluster = got["SELL3"]
        assert cluster["type"] == "sell_cluster"
        assert cluster["label"] == "Sell cluster"
        assert cluster["politician_count"] == 3
        assert cluster["transaction_count"] == 3
        assert cluster["span_days"] == 4
        assert len(cluster["politicians"]) == 3
        assert cluster["rule"]["txn_type"] == "S"
        assert cluster["id"].startswith("sc_")

    def test_two_selling_politicians_do_not_reach_the_threshold(
        self, signal_client,
    ):
        assert "SELLTWO" not in _sell_signals(signal_client)

    def test_gap_of_exactly_seven_days_stays_in_one_burst(self, signal_client):
        cluster = _sell_signals(signal_client)["SGAP7"]
        assert cluster["politician_count"] == 3
        assert cluster["span_days"] == 14

    def test_gap_of_eight_days_splits_bursts(self, signal_client):
        assert "SGAP8" not in _sell_signals(signal_client)

    def test_span_beyond_fourteen_days_is_rejected(self, signal_client):
        # Four politicians with 7-day gaps, but a 21-day span.
        assert "SLONG" not in _sell_signals(signal_client)

    def test_repeated_sales_by_one_politician_count_once(self, signal_client):
        cluster = _sell_signals(signal_client)["SREPT"]
        assert cluster["politician_count"] == 3
        assert cluster["transaction_count"] == 5

    def test_partial_sales_are_excluded(self, signal_client):
        assert "SPART" not in _sell_signals(signal_client)

    def test_estate_transfers_are_excluded(self, signal_client):
        assert "SESTATE" not in _sell_signals(signal_client)

    def test_future_sale_dates_are_excluded(self, signal_client):
        assert "SFUTR" not in _sell_signals(signal_client)

    def test_purchases_are_excluded(self, signal_client):
        # Every purchase fixture has three buyers, yet none may surface.
        got = _sell_signals(signal_client)
        for ticker in ("ALPHA", "GAP7", "REPT", "MONEY", "TWO"):
            assert ticker not in got

    def test_purchases_never_leak_into_the_sell_evidence(self, signal_client):
        cluster = _sell_signals(signal_client)["SELL3"]
        detail = signal_client.get(f"/signals/{cluster['id']}")
        assert detail.status_code == 200, detail.text
        assert {t["txn_type"] for t in detail.json()["transactions"]} == {"S"}


class TestSignalTypeSelection:
    def test_default_type_is_buy_cluster(self, signal_client):
        items = signal_client.get("/signals").json()["items"]
        assert {s["type"] for s in items} == {"buy_cluster"}
        assert "SELL3" not in {s["ticker"] for s in items}

    def test_explicit_buy_cluster_matches_the_default(self, signal_client):
        default = signal_client.get("/signals").json()
        explicit = signal_client.get(
            "/signals", params={"type": "buy_cluster"}).json()
        assert default["items"] == explicit["items"]

    def test_buy_ids_are_unchanged_by_the_type_parameter(self, signal_client):
        assert _signals(signal_client)["ALPHA"]["id"].startswith("bc_")

    def test_sell_and_buy_ids_are_disjoint(self, signal_client):
        buy = {s["id"] for s in signal_client.get("/signals").json()["items"]}
        sell = {
            s["id"] for s in
            signal_client.get("/signals", params={"type": "sell_cluster"}).json()["items"]
        }
        assert buy and sell
        assert not (buy & sell)

    def test_rejects_unknown_signal_type(self, signal_client):
        assert signal_client.get(
            "/signals", params={"type": "hold_cluster"}).status_code == 400


class TestSignalIdRouting:
    def test_sc_id_resolves_a_sell_signal(self, signal_client):
        cluster = _sell_signals(signal_client)["SELL3"]
        resp = signal_client.get(f"/signals/{cluster['id']}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["type"] == "sell_cluster"
        assert body["label"] == "Sell cluster"
        assert body["rule"]["txn_type"] == "S"
        assert len(body["transactions"]) == 3

    def test_bc_id_still_resolves_a_buy_signal(self, signal_client):
        cluster = _signals(signal_client)["ALPHA"]
        resp = signal_client.get(f"/signals/{cluster['id']}")
        assert resp.status_code == 200, resp.text
        assert resp.json()["type"] == "buy_cluster"

    def test_bc_prefix_does_not_resolve_a_sell_cluster(self, signal_client):
        cluster = _sell_signals(signal_client)["SELL3"]
        _, ticker, start = cluster["id"].split("_", 2)
        resp = signal_client.get(f"/signals/bc_{ticker}_{start}")
        assert resp.status_code == 404

    def test_sc_prefix_does_not_resolve_a_buy_cluster(self, signal_client):
        cluster = _signals(signal_client)["ALPHA"]
        _, ticker, start = cluster["id"].split("_", 2)
        resp = signal_client.get(f"/signals/sc_{ticker}_{start}")
        assert resp.status_code == 404

    def test_rejects_unknown_prefix(self, signal_client):
        assert signal_client.get(
            "/signals/xx_SELL3_2025-01-06").status_code == 404

    def test_detail_matches_the_type_requested_in_the_list(self, signal_client):
        # The id alone decides the type; a sell cluster's ticker and start date
        # must not resolve through the buy path or vice versa.
        for params, expected in (
            ({}, "buy_cluster"),
            ({"type": "buy_cluster"}, "buy_cluster"),
            ({"type": "sell_cluster"}, "sell_cluster"),
        ):
            for signal in signal_client.get(
                "/signals", params=params
            ).json()["items"]:
                body = signal_client.get(f"/signals/{signal['id']}").json()
                assert body["type"] == expected
                assert body["id"] == signal["id"]


class TestAmounts:
    def test_totals_sum_the_disclosure_ranges(self, signal_client):
        cluster = _signals(signal_client)["MONEY"]
        assert cluster["total_min"] == pytest.approx(1001 + 15001 + 50001)
        assert cluster["total_max"] == pytest.approx(15000 + 50000 + 100000)

    def test_amounts_are_not_collapsed_to_a_single_figure(self, signal_client):
        cluster = _signals(signal_client)["MONEY"]
        assert isinstance(cluster["total_min"], float)
        assert isinstance(cluster["total_max"], float)
        assert cluster["total_min"] < cluster["total_max"]
        assert "total" not in cluster

    def test_per_politician_amounts_are_preserved(self, signal_client):
        cluster = _signals(signal_client)["MONEY"]
        by_id = {p["id"]: p for p in cluster["politicians"]}
        assert by_id["ca11_nancy_pelosi"]["amount_min"] == 15001
        assert by_id["ca11_nancy_pelosi"]["amount_max"] == 50000

    def test_open_ended_member_makes_the_cluster_total_max_null(
        self, signal_client
    ):
        """A cluster with one "Over $X" member has no summed upper bound.

        sum() alone would skip the null and report a total that silently
        understates the cluster, so total_max must be null. total_min still
        sums the lower bounds, which are all known.
        """
        cluster = _signals(signal_client)["OPEN"]
        assert cluster["total_min"] == pytest.approx(1001 + 15001 + 50000000)
        assert cluster["total_max"] is None

        # The open-ended politician keeps a null amount_max; the others are
        # unaffected, so bounded members still serialize normally.
        maxes = [
            p["amount_max"] for p in cluster["politicians"]
            if p["amount_max"] is not None
        ]
        assert sorted(maxes) == [15000, 50000]
        assert sum(p["amount_max"] is None for p in cluster["politicians"]) == 1


class TestSignalId:
    def test_id_is_ticker_and_start_date(self, signal_client):
        cluster = _signals(signal_client)["ALPHA"]
        assert cluster["id"] == f"bc_alpha_{BASE.isoformat()}"

    def test_id_is_stable_across_requests(self, signal_client):
        first = _signals(signal_client)["ALPHA"]["id"]
        second = _signals(signal_client, sort="ticker")["ALPHA"]["id"]
        assert first == second

    def test_id_does_not_depend_on_sort(self, signal_client):
        by_people = {s["id"] for s in
                     signal_client.get("/signals", params={"sort": "-politician_count"}).json()["items"]}
        by_ticker = {s["id"] for s in
                     signal_client.get("/signals", params={"sort": "ticker"}).json()["items"]}
        assert by_people == by_ticker

    def test_pagination_yields_every_signal_exactly_once(self, signal_client):
        total = signal_client.get(
            "/signals", params={"limit": 1}).json()["pagination"]["total"]
        seen: list[str] = []
        for offset in range(0, total, 2):
            body = signal_client.get(
                "/signals", params={"limit": 2, "offset": offset}).json()
            seen += [s["id"] for s in body["items"]]
        assert len(seen) == total
        assert len(set(seen)) == total

    def test_id_resolves_to_the_detail_route(self, signal_client):
        cluster = _signals(signal_client)["ALPHA"]
        detail = signal_client.get(f"/signals/{cluster['id']}")
        assert detail.status_code == 200
        body = detail.json()
        assert body["id"] == cluster["id"]
        assert body["ticker"] == "ALPHA"
        assert body["transaction_count"] == 3
        assert len(body["transactions"]) == 3


class TestSignalDetail:
    def test_detail_matches_the_list_summary(self, signal_client):
        listed = _signals(signal_client)["REPT"]
        detail = signal_client.get(f"/signals/{listed['id']}").json()
        for key in ("ticker", "politician_count", "transaction_count",
                    "span_days", "total_min", "total_max", "politicians"):
            assert detail[key] == listed[key], key

    def test_detail_carries_the_underlying_transactions(self, signal_client):
        detail = signal_client.get(
            f"/signals/bc_rept_{BASE.isoformat()}").json()
        txns = detail["transactions"]
        assert len(txns) == 5
        assert all(t["txn_type"] == "P" for t in txns)
        assert {t["ticker"] for t in txns} == {"REPT"}
        # Each transaction links back to its source filing and person.
        for t in txns:
            assert t["doc_id"]
            assert t["politician_id"]
            assert t["amount_raw"]

    def test_detail_transactions_are_ordered_by_transaction_date(self, signal_client):
        detail = signal_client.get(
            f"/signals/bc_rept_{BASE.isoformat()}").json()
        dates = [t["txn_date"] for t in detail["transactions"]]
        assert dates == sorted(dates)

    def test_detail_reports_the_rule_and_limitations(self, signal_client):
        body = signal_client.get(f"/signals/bc_alpha_{BASE.isoformat()}").json()
        assert body["rule"]["min_politicians"] == 3
        assert body["rule"]["max_gap_days"] == 7
        assert body["rule"]["max_span_days"] == 14
        assert body["rule"]["txn_type"] == "P"
        assert body["rule"]["materialized"] is False
        assert body["limitations"]

    def test_unknown_signal_is_not_found(self, signal_client):
        assert signal_client.get("/signals/bc_zzzz_2025-01-06").status_code == 404

    def test_malformed_id_is_not_found(self, signal_client):
        for bad in ("nonsense", "bc_alpha", "bc_alpha_2025-13-45x", "bc__2025-01-06"):
            assert signal_client.get(f"/signals/{bad}").status_code == 404


class TestSignalFilters:
    def test_filters_by_ticker(self, signal_client):
        resp = signal_client.get("/signals", params={"ticker": "ALPHA"})
        body = resp.json()
        assert body["pagination"]["total"] == 1
        assert body["items"][0]["ticker"] == "ALPHA"

    def test_ticker_filter_is_case_insensitive(self, signal_client):
        assert signal_client.get(
            "/signals", params={"ticker": "alpha"}).json()["pagination"]["total"] == 1

    def test_filters_by_politician(self, signal_client):
        body = signal_client.get(
            "/signals", params={"politician_id": "tx32_julie_johnson"}).json()
        tickers = {s["ticker"] for s in body["items"]}
        assert "ALPHA" in tickers
        assert "TWO" not in tickers  # Johnson never bought TWO
        assert all(
            any(p["id"] == "tx32_julie_johnson" for p in s["politicians"])
            for s in body["items"]
        )

    def test_unknown_politician_is_not_found(self, signal_client):
        assert signal_client.get(
            "/signals", params={"politician_id": "zz99_nobody_here"}
        ).status_code == 404

    def test_date_range_keeps_overlapping_clusters(self, signal_client):
        # Overlap, not containment: a cluster that starts before the range but
        # ends inside it is still returned.
        body = signal_client.get("/signals", params={
            "start_date": (BASE + timedelta(days=1)).isoformat(),
            "end_date": (BASE + timedelta(days=3)).isoformat(),
        }).json()
        tickers = {s["ticker"] for s in body["items"]}
        assert "ALPHA" in tickers
        assert "MONEY" in tickers

    def test_date_range_can_exclude_everything(self, signal_client):
        body = signal_client.get("/signals", params={
            "start_date": "2020-01-01", "end_date": "2020-12-31",
        }).json()
        assert body["items"] == []
        assert body["pagination"]["total"] == 0

    def test_filters_do_not_change_signal_ids(self, signal_client):
        unfiltered = _signals(signal_client)["ALPHA"]["id"]
        filtered = _signals(signal_client, politician_id="tx32_julie_johnson")
        assert "ALPHA" in filtered
        assert filtered["ALPHA"]["id"] == unfiltered

    def test_span_ceiling_narrows_computed_clusters(self, signal_client):
        # ALPHA spans 4 days, MONEY spans 2, GAP7 spans 14.
        body = signal_client.get("/signals", params={"span_days_max": 7}).json()
        tickers = {s["ticker"] for s in body["items"]}
        assert "ALPHA" in tickers
        assert "MONEY" in tickers
        assert "GAP7" not in tickers  # exactly 14 days wide
        assert all(s["span_days"] <= 7 for s in body["items"])

    def test_span_ceiling_of_zero_keeps_only_same_day_clusters(self, signal_client):
        body = signal_client.get("/signals", params={"span_days_max": 0}).json()
        assert body["items"] == []
        assert body["pagination"]["total"] == 0

    def test_span_ceiling_above_the_rule_cannot_exclude_anything(self, signal_client):
        # The clustering rule caps bursts at 14 days, so a 30-day ceiling is
        # accepted but matches the same set as no ceiling at all.
        unfiltered = signal_client.get("/signals").json()["pagination"]["total"]
        wide = signal_client.get("/signals", params={"span_days_max": 30}).json()
        assert wide["pagination"]["total"] == unfiltered

    def test_span_ceiling_does_not_change_signal_ids(self, signal_client):
        baseline = _signals(signal_client)["ALPHA"]["id"]
        filtered = _signals(signal_client, span_days_max="7")
        assert filtered["ALPHA"]["id"] == baseline

    def test_rejects_negative_span_ceiling(self, signal_client):
        assert signal_client.get(
            "/signals", params={"span_days_max": -1}).status_code == 422

    def test_rejects_non_numeric_span_ceiling(self, signal_client):
        assert signal_client.get(
            "/signals", params={"span_days_max": "abc"}).status_code == 422

    def test_rejects_invalid_date(self, signal_client):
        assert signal_client.get(
            "/signals", params={"start_date": "not-a-date"}).status_code == 400

    def test_rejects_inverted_date_range(self, signal_client):
        assert signal_client.get("/signals", params={
            "start_date": "2025-05-01", "end_date": "2025-01-01",
        }).status_code == 400


class TestSignalPaginationAndSorting:
    def test_sorts_by_politician_count_descending_by_default(self, signal_client):
        counts = [s["politician_count"] for s in
                  signal_client.get("/signals").json()["items"]]
        assert counts == sorted(counts, reverse=True)

    def test_rejects_unknown_sort_key(self, signal_client):
        assert signal_client.get(
            "/signals", params={"sort": "confidence"}).status_code == 400

    def test_paginates_with_a_stable_total(self, signal_client):
        first = signal_client.get("/signals", params={"limit": 1}).json()
        second = signal_client.get("/signals", params={"limit": 1, "offset": 1}).json()
        assert first["pagination"]["total"] == second["pagination"]["total"] > 1
        assert first["items"][0]["id"] != second["items"][0]["id"]

    def test_rejects_out_of_range_limit(self, signal_client):
        assert signal_client.get("/signals", params={"limit": 0}).status_code == 422
        assert signal_client.get("/signals", params={"limit": 101}).status_code == 422
        assert signal_client.get("/signals", params={"offset": -1}).status_code == 422


class TestHomepageHighlights:
    def test_returns_one_recent_cluster_of_each_existing_type(self, signal_client):
        response = signal_client.get("/highlights")
        assert response.status_code == 200
        body = response.json()
        assert body["generated_at"]
        clusters = body["recent_cluster_activity"]
        assert len(clusters) == 2
        assert {item["type"] for item in clusters} == {
            "buy_cluster", "sell_cluster"
        }
        assert {item["ticker"] for item in clusters} == {"GAP7", "SGAP7"}
        assert len({item["signal_id"] for item in clusters}) == 2
        for item in clusters:
            detail = signal_client.get(item["detail_url"])
            assert detail.status_code == 200
            assert detail.json()["type"] == item["type"]
            assert detail.json()["id"] == item["signal_id"]
            assert item["date_start"] <= item["date_end"]
            assert "disclosed" in item["reason"]

    def test_largest_purchase_preserves_open_ended_amount(self, signal_client):
        body = signal_client.get("/highlights").json()
        transactions = {
            item["type"]: item
            for item in body["largest_disclosed_transactions"]
        }
        purchase = transactions["largest_disclosed_purchase"]
        assert purchase["amount_min"] == 50_000_000
        assert purchase["amount_max"] is None
        assert purchase["amount_raw"] == "Over $50,000,000"
        assert "minimum amount" in purchase["title"]
        assert "ranges overlap" in purchase["reason"]
        assert signal_client.get(purchase["detail_url"]).status_code == 200

    def test_largest_transaction_selection_has_no_duplicate_ids(self, signal_client):
        items = signal_client.get("/highlights").json()[
            "largest_disclosed_transactions"
        ]
        assert len(items) == 2
        assert len({item["transaction_id"] for item in items}) == len(items)
