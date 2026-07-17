# AI MC Panel

AI MC Panel is a safety-first, AI-native control panel for a single Minecraft friends server. Users describe an intent in plain language; the model can only call typed tools, and medium/high-risk actions pass through explicit confirmation gates.

The project is currently `0.4.0-alpha`. It runs on a real modded server, but public deployment still requires a read-only audit and an explicit runtime profile.

![AI MC Panel dashboard](docs/images/dashboard.jpg)

## Highlights

- FastAPI, React, TypeScript and SQLite.
- Revocable sessions, Argon2id, CSRF protection and authenticated WebSockets.
- Bounded status, player, log, crash, mod and backup readers.
- Forge, NeoForge, Fabric, Quilt and Vanilla identity detection.
- OpenAI-compatible provider integration with redaction and usage limits.
- No model-generated shell commands and no root web process.
- A root-owned helper that accepts only allowlisted JSON operations.
- Confirmation, recovery points, fingerprint checks and audit events.
- Responsive Simplified Chinese UI with light and dark themes.

## Local development

The default mock adapter never connects to a real Minecraft server.

```bash
cp .env.example .env
set -a && source .env && set +a
uv sync --project apps/api --all-extras --locked
npm --prefix apps/web ci
npm --prefix apps/web run build
uv run --project apps/api mc-panel-admin create-user admin
uv run --project apps/api mc-panel-api
```

Run `npm --prefix apps/web run dev` in a second terminal and open `http://127.0.0.1:5173`.

Read [SECURITY.md](SECURITY.md), [DEPLOYMENT.md](DEPLOYMENT.md), and [CONTRIBUTING.md](CONTRIBUTING.md) before enabling the systemd adapter or privileged writes.

Licensed under Apache-2.0.
