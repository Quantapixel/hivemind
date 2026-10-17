"""Tests never touch a developer's real setup: no personal config (which may
point at a shared cloud database), only the local FalkorDB."""

import pytest


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    for var in ("FALKORDB_URL", "FALKORDB_PASSWORD", "FALKORDB_USERNAME", "HIVEMIND_TEAM", "HIVEMIND_ROOT", "CODEX_HOME"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("FALKORDB_HOST", "localhost")
    monkeypatch.setenv("FALKORDB_PORT", "6379")
