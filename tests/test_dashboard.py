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


def test_join_hands_over_the_connection_only_with_the_code(team):
    s = Settings(team=team, url="redis://default:dbpass@db.example:1234")
    c = TestClient(create_app(s, access_key="k" * 10, allowed_teams=[team], allow_join=True))
    assert c.get("/api/join").status_code == 401
    r = c.get("/api/join", headers={"authorization": "Bearer " + "k" * 10})
    assert r.json() == {"url": "redis://default:dbpass@db.example:1234", "team": team, "teams": [team]}


def test_join_is_off_for_local_dashboards(team):
    c = TestClient(create_app(Settings(team=team)))
    assert c.get("/api/join").status_code == 404


def test_public_simulation_dashboard(team, monkeypatch):
    from pathlib import Path

    from hivemind.ui import app_from_env

    monkeypatch.setenv("HIVEMIND_PUBLIC", "1")
    monkeypatch.setenv("HIVEMIND_TEAMS", team)
    monkeypatch.setenv("HIVEMIND_SIM_TEAM", team)
    monkeypatch.setenv("HIVEMIND_SIM_PROJECT", str(Path(__file__).resolve().parents[1] / "examples" / "shopapp"))
    monkeypatch.delenv("HIVEMIND_DASHBOARD_KEY", raising=False)
    c = TestClient(app_from_env())
    sim = c.get("/api/sim").json()  # no access code needed
    assert sim["enabled"] and sim["step"] == 0 and sim["total"] == 12
    for i in range(12):
        sim = c.post(f"/api/sim/step?expect={i}").json()
    assert sim["step"] == 12
    assert c.post("/api/sim/step?expect=3").json()["step"] == 12  # stale clicks are ignored
    state = c.get(f"/api/state?team={team}").json()
    assert {d["name"] for d in state["devs"]} == {"Priya", "Marco", "Aisha", "Ken", "Lena"}
    assert c.get("/api/state?team=someone_else").status_code == 403
    assert c.get("/api/join").status_code == 404  # never hands out the database
    assert c.post("/api/sim/reset").json()["step"] == 0


def test_public_dashboard_requires_a_team_list(monkeypatch):
    from hivemind.ui import app_from_env

    monkeypatch.setenv("HIVEMIND_PUBLIC", "1")
    monkeypatch.delenv("HIVEMIND_TEAMS", raising=False)
    with pytest.raises(RuntimeError):
        app_from_env()
