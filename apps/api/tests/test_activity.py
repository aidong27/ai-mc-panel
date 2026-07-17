from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from mc_panel_api.activity import PlayerActivityService
from mc_panel_api.database import Database


class ActivityAdapter:
    def __init__(self, scans: list[dict[str, Any] | Exception]) -> None:
        self.scans = scans

    def scan_player_activity(self, days: int = 30) -> dict[str, Any]:
        assert days == 30
        result = self.scans.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _event(player: str, joined_at: datetime) -> dict[str, str]:
    digest = hashlib.sha256(f"{player}|{joined_at.isoformat()}".encode()).hexdigest()
    return {
        "id": f"activity_{digest}",
        "player": player,
        "joined_at": joined_at.isoformat(),
    }


def _scan(now: datetime, events: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "events": events,
        "generated_at": now.isoformat(),
        "coverage_start": (now - timedelta(days=30)).isoformat(),
        "coverage_end": now.isoformat(),
        "scan_complete": True,
        "truncated": False,
    }


def test_schema_two_adds_activity_tables(tmp_path: Path) -> None:
    database = Database(tmp_path / "panel.db")
    database.migrate()

    assert database.fetch_one("SELECT version FROM schema_meta") == {"version": 2}
    tables = {
        row["name"]
        for row in database.fetch_all(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'player_activity_%'"
        )
    }
    assert tables == {
        "player_activity_events",
        "player_activity_coverage",
        "player_activity_state",
    }


def test_activity_history_is_deduplicated_and_survives_log_rotation(tmp_path: Path) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    alice = _event("Alice", now - timedelta(days=2))
    bob = _event("Bob", now - timedelta(hours=1))
    adapter = ActivityAdapter(
        [
            _scan(now, [alice, bob, bob]),
            _scan(now + timedelta(minutes=10), []),
        ]
    )
    database = Database(tmp_path / "panel.db")
    database.migrate()
    service = PlayerActivityService(database, adapter)  # type: ignore[arg-type]

    service.refresh_if_stale(force=True)
    first = service.get_activity(7)
    service.refresh_if_stale(force=True)
    after_rotation = service.get_activity(7)

    assert first["sessions"] == 2
    assert after_rotation["sessions"] == 2
    assert after_rotation["unique_players"] == 2
    assert after_rotation["coverage_complete"] is True
    assert after_rotation["history"]["persisted"] is True
    assert database.fetch_one("SELECT count(*) AS count FROM player_activity_events") == {
        "count": 2
    }


def test_collection_failure_keeps_history_and_marks_collector_unhealthy(tmp_path: Path) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    adapter = ActivityAdapter([_scan(now, [_event("Alice", now)]), OSError("offline")])
    database = Database(tmp_path / "panel.db")
    database.migrate()
    service = PlayerActivityService(database, adapter)  # type: ignore[arg-type]
    service.refresh_if_stale(force=True)

    with pytest.raises(OSError):
        service.refresh_if_stale(force=True)
    result = service.get_activity(7)

    assert result["sessions"] == 1
    assert result["history"]["collector_healthy"] is False
    state = database.fetch_one("SELECT last_error_code FROM player_activity_state WHERE id = 1")
    assert state == {"last_error_code": "OSError"}
