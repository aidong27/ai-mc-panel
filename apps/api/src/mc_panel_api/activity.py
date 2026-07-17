from __future__ import annotations

import logging
import re
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from .database import Database

if TYPE_CHECKING:
    from .adapters.base import MinecraftAdapter

LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")
PLAYER_NAME = re.compile(r"[A-Za-z0-9_]{1,16}")
REFRESH_SECONDS = 600
FRESHNESS_GRACE_SECONDS = 1_200
RETENTION_DAYS = 400
logger = logging.getLogger(__name__)


def _datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def _utc_iso(value: Any) -> str | None:
    parsed = _datetime(value)
    return parsed.astimezone(UTC).isoformat() if parsed is not None else None


def summarize_activity(
    events: list[dict[str, Any]],
    *,
    days: int,
    now: datetime,
    coverage_start: datetime | None,
    coverage_end: datetime | None,
    coverage_complete: bool,
    truncated: bool,
    source: str,
    last_imported_at: str | None = None,
    collector_healthy: bool = True,
) -> dict[str, Any]:
    if not 1 <= days <= 30:
        raise ValueError("activity days must be between 1 and 30")
    local_now = now.astimezone(LOCAL_TIMEZONE)
    first_day = local_now.date() - timedelta(days=days - 1)
    requested_start = datetime.combine(first_day, datetime.min.time(), LOCAL_TIMEZONE)
    unique_events: dict[str, tuple[datetime, str]] = {}
    for event in events:
        event_id = event.get("id") or event.get("event_id")
        player = event.get("player") or event.get("player_name")
        joined_at = _datetime(event.get("joined_at"))
        if (
            not isinstance(event_id, str)
            or not isinstance(player, str)
            or PLAYER_NAME.fullmatch(player) is None
            or joined_at is None
            or joined_at < requested_start
            or joined_at > local_now + timedelta(minutes=5)
        ):
            continue
        unique_events[event_id] = (joined_at.astimezone(LOCAL_TIMEZONE), player)

    daily: dict[str, dict[str, Any]] = {}
    for offset in range(days):
        date = first_day + timedelta(days=offset)
        daily[date.isoformat()] = {"date": date.isoformat(), "sessions": 0, "players": set()}
    player_summary: dict[str, dict[str, Any]] = {}
    for joined_at, player in sorted(unique_events.values()):
        day = joined_at.date().isoformat()
        if day not in daily:
            continue
        daily[day]["sessions"] += 1
        daily[day]["players"].add(player)
        item = player_summary.setdefault(
            player,
            {"name": player, "sessions": 0, "last_joined_at": joined_at.isoformat()},
        )
        item["sessions"] += 1
        item["last_joined_at"] = max(str(item["last_joined_at"]), joined_at.isoformat())

    daily_result = []
    for item in daily.values():
        names = sorted(item["players"])
        daily_result.append(
            {
                "date": item["date"],
                "sessions": item["sessions"],
                "unique_players": len(names),
                "players": names,
            }
        )
    recent_players = sorted(
        player_summary.values(), key=lambda item: str(item["last_joined_at"]), reverse=True
    )[:50]
    return {
        "days": days,
        "requested_start": requested_start.isoformat(),
        "generated_at": local_now.isoformat(),
        "coverage_start": coverage_start.astimezone(LOCAL_TIMEZONE).isoformat()
        if coverage_start
        else None,
        "coverage_end": coverage_end.astimezone(LOCAL_TIMEZONE).isoformat()
        if coverage_end
        else None,
        "coverage_complete": coverage_complete,
        "truncated": truncated,
        "sessions": len(unique_events),
        "unique_players": len(player_summary),
        "daily": daily_result,
        "recent_players": recent_players,
        "source": source,
        "history": {
            "persisted": source == "persisted_minecraft_login_history",
            "last_imported_at": last_imported_at,
            "collector_healthy": collector_healthy,
        },
    }


class PlayerActivityService:
    def __init__(self, database: Database, adapter: MinecraftAdapter) -> None:
        self.database = database
        self.adapter = adapter
        self._refresh_lock = threading.Lock()
        self._last_refresh = 0.0

    def refresh_if_stale(self, *, force: bool = False) -> bool:
        with self._refresh_lock:
            now = time.monotonic()
            if not force and now - self._last_refresh < REFRESH_SECONDS:
                return False
            self._last_refresh = now
            self._refresh()
            return True

    def _refresh(self) -> None:
        attempted_at = datetime.now(UTC).isoformat()
        try:
            scan = self.adapter.scan_player_activity(30)
            events = scan.get("events")
            if not isinstance(events, list):
                raise ValueError("activity scan returned invalid events")
            scan_complete = scan.get("scan_complete") is True
            truncated = scan.get("truncated") is True
            coverage_start = _utc_iso(scan.get("coverage_start"))
            generated_at = _utc_iso(scan.get("generated_at"))
            if generated_at is None:
                raise ValueError("activity scan returned invalid generated_at")
            valid_events: list[tuple[str, str, str, str]] = []
            for event in events:
                if not isinstance(event, dict):
                    continue
                event_id = event.get("id")
                player = event.get("player")
                joined_at = _utc_iso(event.get("joined_at"))
                if (
                    isinstance(event_id, str)
                    and re.fullmatch(r"activity_[a-f0-9]{32,64}", event_id)
                    and isinstance(player, str)
                    and PLAYER_NAME.fullmatch(player)
                    and joined_at is not None
                ):
                    valid_events.append((event_id, player, joined_at, attempted_at))
            with self.database.transaction() as connection:
                connection.executemany(
                    """
                    INSERT OR IGNORE INTO player_activity_events(
                        event_id, player_name, joined_at, imported_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    valid_events,
                )
                cutoff = (datetime.now(UTC) - timedelta(days=RETENTION_DAYS)).isoformat()
                connection.execute(
                    "DELETE FROM player_activity_events WHERE joined_at < ?", (cutoff,)
                )
                if scan_complete and not truncated and coverage_start is not None:
                    overlapping = connection.execute(
                        """
                        SELECT id, start_at, end_at FROM player_activity_coverage
                        WHERE end_at >= ? AND start_at <= ?
                        """,
                        (coverage_start, generated_at),
                    ).fetchall()
                    merged_start = min(
                        [coverage_start, *(str(row["start_at"]) for row in overlapping)]
                    )
                    merged_end = max([generated_at, *(str(row["end_at"]) for row in overlapping)])
                    if overlapping:
                        connection.executemany(
                            "DELETE FROM player_activity_coverage WHERE id = ?",
                            [(int(row["id"]),) for row in overlapping],
                        )
                    connection.execute(
                        """
                        INSERT INTO player_activity_coverage(start_at, end_at, updated_at)
                        VALUES (?, ?, ?)
                        """,
                        (merged_start, merged_end, attempted_at),
                    )
                connection.execute(
                    """
                    INSERT INTO player_activity_state(
                        id, last_attempt_at, last_success_at, last_scan_complete,
                        last_scan_truncated, last_error_code
                    ) VALUES (1, ?, ?, ?, ?, NULL)
                    ON CONFLICT(id) DO UPDATE SET
                        last_attempt_at = excluded.last_attempt_at,
                        last_success_at = excluded.last_success_at,
                        last_scan_complete = excluded.last_scan_complete,
                        last_scan_truncated = excluded.last_scan_truncated,
                        last_error_code = NULL
                    """,
                    (attempted_at, attempted_at, int(scan_complete), int(truncated)),
                )
        except Exception as exc:
            self.database.execute(
                """
                INSERT INTO player_activity_state(
                    id, last_attempt_at, last_scan_complete, last_scan_truncated, last_error_code
                ) VALUES (1, ?, 0, 0, ?)
                ON CONFLICT(id) DO UPDATE SET
                    last_attempt_at = excluded.last_attempt_at,
                    last_scan_complete = 0,
                    last_error_code = excluded.last_error_code
                """,
                (attempted_at, type(exc).__name__[:80]),
            )
            raise

    def get_activity(self, days: int = 7) -> dict[str, Any]:
        if not 1 <= days <= 30:
            raise ValueError("activity days must be between 1 and 30")
        refresh_failed = False
        try:
            self.refresh_if_stale()
        except Exception:
            refresh_failed = True
            logger.exception("player activity collection failed")

        now = datetime.now(LOCAL_TIMEZONE)
        first_day = now.date() - timedelta(days=days - 1)
        requested_start = datetime.combine(first_day, datetime.min.time(), LOCAL_TIMEZONE)
        start_utc = requested_start.astimezone(UTC).isoformat()
        rows = self.database.fetch_all(
            """
            SELECT event_id, player_name, joined_at
            FROM player_activity_events
            WHERE joined_at >= ?
            ORDER BY joined_at
            """,
            (start_utc,),
        )
        ranges = self.database.fetch_all(
            "SELECT start_at, end_at FROM player_activity_coverage ORDER BY start_at"
        )
        state = self.database.fetch_one("SELECT * FROM player_activity_state WHERE id = 1") or {}
        requested_utc = requested_start.astimezone(UTC)
        freshness_target = datetime.now(UTC) - timedelta(seconds=FRESHNESS_GRACE_SECONDS)
        parsed_ranges = [
            (start, end)
            for item in ranges
            if (start := _datetime(item.get("start_at"))) is not None
            and (end := _datetime(item.get("end_at"))) is not None
        ]
        coverage_complete = any(
            start <= requested_utc and end >= freshness_target for start, end in parsed_ranges
        )
        coverage_start = min((start for start, _ in parsed_ranges), default=None)
        coverage_end = max((end for _, end in parsed_ranges), default=None)
        events = [
            {
                "id": row["event_id"],
                "player": row["player_name"],
                "joined_at": row["joined_at"],
            }
            for row in rows
        ]
        collector_healthy = (
            not refresh_failed
            and bool(state.get("last_scan_complete"))
            and not bool(state.get("last_error_code"))
        )
        return summarize_activity(
            events,
            days=days,
            now=now,
            coverage_start=coverage_start,
            coverage_end=coverage_end,
            coverage_complete=coverage_complete,
            truncated=bool(state.get("last_scan_truncated")),
            source="persisted_minecraft_login_history",
            last_imported_at=str(state["last_success_at"])
            if state.get("last_success_at")
            else None,
            collector_healthy=collector_healthy,
        )
