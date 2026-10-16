"""Hosted dashboard: access code, team allowlist, database outages."""

from __future__ import annotations

import uuid

import pytest
from starlette.testclient import TestClient

from hivemind.db import Settings, connect
from hivemind.ui import create_app


@pytest.fixture()
def team():
    name = f"test_{uuid.uuid4().hex[:8]}"
    try:
        g = connect(Settings(team=name))
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"FalkorDB not reachable: {exc}")
    yield name
    g.delete()


def test_api_needs_the_access_code(team):
    c = TestClient(create_app(Settings(team=team), access_key="right-code", allowed_teams=[team]))
    assert c.get("/").status_code == 200  # the page itself loads and asks for the code
    assert c.get("/api/teams").status_code == 401
    assert c.get("/api/teams", headers={"authorization": "Bearer wrong"}).status_code == 401
    r = c.get("/api/teams", headers={"authorization": "Bearer right-code"})
    assert r.status_code == 200 and r.json()["teams"] == [team]


def test_only_allowed_teams_are_visible(team):
    c = TestClient(create_app(Settings(team=team), access_key="k" * 10, allowed_teams=[team]))
    r = c.get("/api/state?team=someone_else", headers={"authorization": "Bearer " + "k" * 10})
    assert r.status_code == 403


def test_database_outage_is_a_503_not_a_crash():
    c = TestClient(create_app(Settings(url="redis://default:x@127.0.0.1:1"), access_key="k" * 10))
    assert c.get("/").status_code == 200
    r = c.get("/api/teams", headers={"authorization": "Bearer " + "k" * 10})
    assert r.status_code == 503 and "unreachable" in r.json()["error"]
