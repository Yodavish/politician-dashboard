"""Tests for homepage highlights when the database has insufficient data."""

from fastapi.testclient import TestClient

from politician_dashboard.api import create_app


def test_empty_database_returns_empty_highlight_sections(temp_database_url):
    with TestClient(create_app(database_url=temp_database_url)) as client:
        response = client.get("/highlights")

    assert response.status_code == 200
    body = response.json()
    assert body["generated_at"]
    assert body["recent_cluster_activity"] == []
    assert body["largest_disclosed_transactions"] == []
