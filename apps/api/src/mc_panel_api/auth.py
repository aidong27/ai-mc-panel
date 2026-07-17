from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from pwdlib import PasswordHash

from .config import Settings
from .database import Database, utc_now
from .models import Role

password_hash = PasswordHash.recommended()


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    id: int
    username: str
    role: Role
    session_token: str


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class AuthService:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    def create_user(self, username: str, password: str, role: Role = Role.OWNER) -> int:
        normalized = username.strip().lower()
        self._validate_new_password(password)
        if not normalized.replace("_", "").replace("-", "").isalnum():
            raise ValueError("用户名格式不正确")
        digest = password_hash.hash(password)
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO users(username, password_hash, role, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (normalized, digest, role.value, utc_now()),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("database did not return a user id")
            return int(cursor.lastrowid)

    @staticmethod
    def _validate_new_password(password: str) -> None:
        if len(password) < 12:
            raise ValueError("密码至少需要 12 个字符")
        if len(password) > 256 or any(ord(character) < 32 for character in password):
            raise ValueError("密码格式不正确")

    def authenticate(self, username: str, password: str) -> dict[str, Any] | None:
        user = self.database.fetch_one(
            "SELECT * FROM users WHERE username = ? AND disabled = 0",
            (username.strip().lower(),),
        )
        if user is None or not password_hash.verify(password, str(user["password_hash"])):
            return None
        return user

    def create_session(self, user_id: int) -> str:
        token = secrets.token_urlsafe(48)
        now = datetime.now(UTC)
        expires = now + timedelta(hours=self.settings.session_absolute_hours)
        self.database.execute(
            """
            INSERT INTO sessions(token_hash, user_id, created_at, last_seen_at, expires_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (token_hash(token), user_id, now.isoformat(), now.isoformat(), expires.isoformat()),
        )
        return token

    def change_password(self, user_id: int, current_password: str, new_password: str) -> str | None:
        user = self.database.fetch_one(
            "SELECT id, password_hash FROM users WHERE id = ? AND disabled = 0",
            (user_id,),
        )
        if user is None or not password_hash.verify(current_password, str(user["password_hash"])):
            return None
        self._validate_new_password(new_password)
        if password_hash.verify(new_password, str(user["password_hash"])):
            raise ValueError("新密码不能与当前密码相同")

        token = secrets.token_urlsafe(48)
        now = datetime.now(UTC)
        expires = now + timedelta(hours=self.settings.session_absolute_hours)
        digest = password_hash.hash(new_password)
        with self.database.connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "UPDATE users SET password_hash = ? WHERE id = ?",
                    (digest, user_id),
                )
                connection.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
                connection.execute("DELETE FROM ws_tickets WHERE user_id = ?", (user_id,))
                connection.execute(
                    """
                    INSERT INTO sessions(token_hash, user_id, created_at, last_seen_at, expires_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        token_hash(token),
                        user_id,
                        now.isoformat(),
                        now.isoformat(),
                        expires.isoformat(),
                    ),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return token

    def reset_password(self, username: str, new_password: str) -> bool:
        normalized = username.strip().lower()
        user = self.database.fetch_one(
            "SELECT id FROM users WHERE username = ? AND disabled = 0", (normalized,)
        )
        if user is None:
            return False
        self._validate_new_password(new_password)
        digest = password_hash.hash(new_password)
        user_id = int(user["id"])
        with self.database.connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "UPDATE users SET password_hash = ? WHERE id = ?", (digest, user_id)
                )
                connection.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
                connection.execute("DELETE FROM ws_tickets WHERE user_id = ?", (user_id,))
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return True

    def resolve_session(self, token: str) -> AuthenticatedUser | None:
        if not token:
            return None
        session = self.database.fetch_one(
            """
            SELECT s.*, u.username, u.role, u.disabled
            FROM sessions s JOIN users u ON u.id = s.user_id
            WHERE s.token_hash = ?
            """,
            (token_hash(token),),
        )
        if session is None or int(session["disabled"]):
            return None

        now = datetime.now(UTC)
        expires = datetime.fromisoformat(str(session["expires_at"]))
        last_seen = datetime.fromisoformat(str(session["last_seen_at"]))
        idle_limit = timedelta(minutes=self.settings.session_idle_minutes)
        if now >= expires or now - last_seen > idle_limit:
            self.revoke_session(token)
            return None

        self.database.execute(
            "UPDATE sessions SET last_seen_at = ? WHERE token_hash = ?",
            (now.isoformat(), token_hash(token)),
        )
        return AuthenticatedUser(
            id=int(session["user_id"]),
            username=str(session["username"]),
            role=Role(str(session["role"])),
            session_token=token,
        )

    def revoke_session(self, token: str) -> None:
        self.database.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash(token),))

    def csrf_token(self, session_token: str) -> str:
        return hmac.new(
            self.settings.secret_key.encode("utf-8"),
            f"csrf:{session_token}".encode(),
            hashlib.sha256,
        ).hexdigest()

    def verify_csrf(self, session_token: str, provided: str) -> bool:
        expected = self.csrf_token(session_token)
        return bool(provided) and hmac.compare_digest(expected, provided)

    def create_ws_ticket(self, user_id: int) -> str:
        token = secrets.token_urlsafe(32)
        expires = datetime.now(UTC) + timedelta(seconds=30)
        self.database.execute(
            "INSERT INTO ws_tickets(token_hash, user_id, expires_at) VALUES (?, ?, ?)",
            (token_hash(token), user_id, expires.isoformat()),
        )
        return token

    def consume_ws_ticket(self, token: str) -> int | None:
        ticket = self.database.fetch_one(
            "SELECT * FROM ws_tickets WHERE token_hash = ? AND used = 0",
            (token_hash(token),),
        )
        if ticket is None or datetime.now(UTC) >= datetime.fromisoformat(str(ticket["expires_at"])):
            return None
        updated = self.database.execute(
            "UPDATE ws_tickets SET used = 1 WHERE token_hash = ? AND used = 0",
            (token_hash(token),),
        )
        return int(ticket["user_id"]) if updated == 1 else None
