from __future__ import annotations

import atexit
import os
import secrets
import shutil
import sys
import tempfile
from pathlib import Path

import uvicorn

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / "apps" / "api" / "src"))

STATE_ROOT = Path(tempfile.mkdtemp(prefix="mc-panel-e2e-"))
atexit.register(shutil.rmtree, STATE_ROOT, True)
HOST = os.environ.get("MC_PANEL_E2E_HOST", "127.0.0.1")
PORT = int(os.environ.get("MC_PANEL_E2E_PORT", "18173"))
PASSWORD = os.environ["MC_PANEL_E2E_PASSWORD"]

os.environ.update(
    {
        "MC_PANEL_ENV": "test",
        "MC_PANEL_BIND": HOST,
        "MC_PANEL_PORT": str(PORT),
        "MC_PANEL_DATABASE_PATH": str(STATE_ROOT / "panel.db"),
        "MC_PANEL_WEB_DIST_PATH": str(REPOSITORY / "apps" / "web" / "dist"),
        "MC_PANEL_ADAPTER": "mock",
        "MC_PANEL_MOCK_ROOT": str(REPOSITORY / "tests" / "fixtures" / "mock-server"),
        "MC_PANEL_SECRET_KEY": secrets.token_urlsafe(48),
        "MC_PANEL_SESSION_SECURE": "false",
        "MC_PANEL_PUBLIC_ORIGIN": f"http://{HOST}:{PORT}",
        "MC_PANEL_SERVER_TIMEZONE": "Asia/Shanghai",
        "MC_PANEL_SERVER_NAME": "星光好友服",
        "MC_PANEL_PUBLIC_ADDRESS": "play.example.test:25565",
        "MC_PANEL_AI_ENABLED": "false",
    }
)

from mc_panel_api.auth import AuthService  # noqa: E402
from mc_panel_api.config import Settings  # noqa: E402
from mc_panel_api.database import Database  # noqa: E402
from mc_panel_api.main import create_app  # noqa: E402


def main() -> None:
    settings = Settings.from_env()
    database = Database(settings.database_path)
    database.migrate()
    AuthService(database, settings).create_user("admin", PASSWORD)
    uvicorn.run(create_app(settings), host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
