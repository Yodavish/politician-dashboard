"""Tests for homepage highlights when the database has insufficient data."""

from datetime import date, datetime

from fastapi.testclient import TestClient

from politician_dashboard.api import create_app
from politician_dashboard.api.queries import six_month_cutoff
from politician_dashboard.api.routes import highlights as highlights_route


def test_six_month_cutoff_is_dynamic_calendar_month_arithmetic():
    assert six_month_cutoff(date(2026, 10, 2)) == date(2026, 4, 2)
    assert six_month_cutoff(date(2027, 1, 15)) == date(2026, 7, 15)
    assert six_month_cutoff(date(2025, 3, 31)) == date(2024, 9, 30)


def test_activity_window_end_date_matches_generated_utc_date(monkeypatch):
    monkeypatch.setattr(highlights_route.queries, "list_recent_cluster_highlights", lambda *args, **kwargs: [])
    monkeypatch.setattr(highlights_route.queries, "list_largest_disclosed_transactions", lambda *args, **kwargs: [])

    body = highlights_route.highlights_list(conn=object())

    assert body["activity_window"]["end_date"].isoformat() == body["generated_at"].date().isoformat()


def test_empty_database_returns_empty_highlight_sections(temp_database_url):
    with TestClient(create_app(database_url=temp_database_url)) as client:
        response = client.get("/highlights")

    assert response.status_code == 200
    body = response.json()
    generated_at = datetime.fromisoformat(body["generated_at"])
    assert body["activity_window"]["end_date"]
    assert body["activity_window"]["end_date"] == generated_at.date().isoformat()
    assert body["activity_window"]["start_date"]
    assert body["recent_cluster_activity"] == []
    assert body["largest_disclosed_transactions"] == []
