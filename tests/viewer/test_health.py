"""Tests for the ``/api/health`` endpoint (viewer app skeleton)."""

from __future__ import annotations

from fastapi.testclient import TestClient


class TestHealth:
    def test_health_ok(self, client: TestClient) -> None:
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}
