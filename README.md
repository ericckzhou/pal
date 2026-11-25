# pal

A private, local-first personal assistant. pal runs entirely on one Windows PC: a local LLM through Ollama, PostgreSQL with pgvector for storage and search, and a FastAPI server that also serves the React web UI. Nothing leaves the machine. Your phone or laptop reaches it through Tailscale.

## Features

- **Chat** with a local model (default `qwen3.5:4b`). Answers can cite memories, documents, notes and past conversations.
- **Memory**: say "remember that ..." to store a fact. pal also proposes memories after a conversation goes quiet, and nothing is saved until you confirm it.
- **Documents**: add PDF, Markdown and text files by upload or with `pal ingest`. They're indexed for hybrid search (full-text plus vectors, merged with reciprocal-rank fusion).
- **Work**: projects, tasks, notes and decisions. Each project has an overview page with its pending tasks, memories, documents and scheduled reminders.
- **Reminders and notifications**: one-off or repeating. Repeats follow local wall-clock time, so they stay put across daylight-saving changes.
- **Proposals**: the model can only *suggest* tasks and reminders. They're created when you confirm them or type a slash command yourself (`/task`, `/remind`, `/note`, `/decision`, `/project`).
- **Dashboard**: status, sessions, schedule, analytics, logs and settings.
- **Devices**: pair a phone or laptop with a one-time code and sign in with a passkey (WebAuthn).

## Security model

pal assumes the machine is trusted and everything else is not.

- **Loopback only.** The server binds to `127.0.0.1:8765` and won't start on any other address. Remote access goes through Tailscale Serve, which provides HTTPS. pal ignores Tailscale identity headers, so every request still needs a pal session.
- **No outbound network.** At startup, an egress guard patches `socket` so any non-loopback connection or DNS lookup fails.
- **No secrets in files.** The database password is kept in Windows Credential Manager. The owner password is hashed with Argon2.
- **Strict request checks.** Host and Origin allowlists, a required CSRF header (`x-pal-request`), and a strict CSP.
- **Model output is never trusted.** Memories and actions the model produces stay proposals until you confirm them. Document text is passed to the model as data and never interpreted as instructions.
- **Encrypted nightly backups.** `pg_dump` output is encrypted with AES-256-GCM before it's written to `%LOCALAPPDATA%\pal\backups` (outside the repo). The key is kept in Credential Manager. The 14 most recent are kept, and a restore check is available. Run `pal backup-key --show` once and store the key offline: without it, backups can't be read on a reinstalled PC.

## Requirements

- Windows 10/11 (the scripts and credential storage are Windows-specific)
- Python 3.13 and [uv](https://docs.astral.sh/uv/)
- Docker Desktop
- [Ollama](https://ollama.com/) with `qwen3.5:4b` and `nomic-embed-text` pulled
- Node.js and pnpm (only needed to build the web UI)
- Tailscale (optional, for remote access)

## Setup

```sh
# 1. Models
ollama pull qwen3.5:4b
ollama pull nomic-embed-text

# 2. Database (generates a password and stores it in Credential Manager on first run)
uv run scripts/db.py up

# 3. Python dependencies and schema
uv sync
uv run pal migrate

# 4. Web UI
cd web && pnpm install && pnpm build && cd ..

# 5. Owner password, then run
uv run pal set-password
uv run pal serve
```

Open http://127.0.0.1:8765.

### Remote access

```sh
tailscale serve --bg http://127.0.0.1:8765
```

`pal serve` finds the tailnet name and accepts requests for it. To pair a device, use the Devices page, or `uv run pal pair --name Phone` if you're locked out of the UI.

### Start at logon

```powershell
powershell -File scripts/install-autostart.ps1
```

This registers a per-user scheduled task. At logon it starts Docker Desktop and Ollama, waits for the database to be healthy, then runs `pal serve`. Logs go to `%LOCALAPPDATA%\pal\logs\pal.log`. To remove it: `Unregister-ScheduledTask -TaskName pal -Confirm:$false`.

## CLI

| Command | Purpose |
|---|---|
| `pal serve` | Run the server on 127.0.0.1:8765 (applies migrations first) |
| `pal migrate` | Apply database migrations |
| `pal set-password` | Set the owner password (signs out all sessions) |
| `pal ingest <path>` | Add or refresh a file or folder of PDF/Markdown/text |
| `pal backup [--check]` | Write a backup now; `--check` restores it into a scratch DB and compares row counts |
| `pal backup-key [--show\|--set]` | Check, print (to store offline) or restore the backup encryption key |
| `pal claude-key [--set\|--clear]` | Check, store or remove the Anthropic API key (Credential Manager). Restart pal after changing it. A chat uses Claude only when you pick a Claude model for it; only that chat's text is sent, never memories, documents or notes |
| `pal decrypt-backup <file> <out>` | Write a plain dump from an encrypted backup, for a manual `pg_restore` |
| `pal pair [--name] [--kind]` | Make a one-time pairing code for a device |
| `pal devices` | List paired devices |
| `pal revoke-device <name\|id>` | Revoke a device and end its sessions |
| `pal revoke-sessions` | Sign out every device immediately |

Run each command as `uv run pal <command>`.

## Configuration

Defaults are in [src/pal/config.py](src/pal/config.py) (port, Ollama URL, model, context size, session lifetime, backup retention, memory idle time). You can add other locally installed models on the Settings page. pal still only talks to Ollama on loopback.

## Project layout

```
src/pal/        FastAPI app, CLI, and domain modules (chat, memory, retrieval, work, reminders, devices, ...)
web/            React 19 + Vite UI, built to web/dist and served by the app
migrations/     Ordered SQL migrations (applied by `pal migrate` / `pal serve`)
scripts/        db.py (Postgres container), autostart PowerShell scripts
eval/           Model comparison harness; see eval/RESULTS.md
tests/          pytest suite (uses a real Postgres test database and a fake LLM)
compose.yaml    pgvector/pgvector Postgres 18, published on 127.0.0.1 only
```

## Development

```sh
uv run pytest                      # needs the database running; uses database "pal_test" (override with PAL_TEST_DB)
uv run pytest --cov=pal
cd web && pnpm watch               # rebuild the UI on change
uv run eval/run.py --local         # local model evaluation, no network
```
