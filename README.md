# pal

pal is a personal assistant that lives on your own Windows PC. You chat with it, and it remembers things for you, reads your documents, keeps track of your projects and tasks, and reminds you about things.

Everything runs on your machine: the AI model, the database and the web app. Your conversations, memories and files are not sent anywhere. The one exception is a Claude model, which you can choose for a single chat if you want; see [Privacy and security](#privacy-and-security).

You use pal in a web browser, either on the PC itself or on your phone or laptop through Tailscale, a private network between your own devices.

**[Try the demo →](https://pal-assistant-demo.vercel.app)** It's the real web app filled with made-up data. Nothing you type is saved, and no AI is running behind it.

## What it does

**Chat.** Talk to a local AI model. When it answers, it can draw on what it remembers about you, your documents, your notes and earlier conversations, and it tells you where the information came from. You can set how much each chat "thinks" before it answers (off, low, medium or high).

**Memory.** Say "remember that…" and pal saves the fact. After a conversation goes quiet, pal may also suggest a few things to remember. Nothing is saved until you say yes.

**Documents.** Upload PDFs, Markdown or text files, or point pal at a folder. It reads them, including tables and scanned pages in PDFs, so you can ask questions about them later.

**Projects and tasks.** Keep projects, tasks, notes and decisions in one place. Each project has its own page with its open tasks, related memories and documents, and upcoming reminders.

**Reminders.** Set one-off or repeating reminders. A reminder set for 9 a.m. stays at 9 a.m. when the clocks change.

**You stay in charge.** The AI can *suggest* a task or a reminder, but it is only created once you confirm it. You can also create them yourself with `/task`, `/remind`, `/note`, `/decision` and `/project`.

**Dashboard.** See status, sessions, your schedule, usage, logs and settings.

**Your devices.** Pair a phone or laptop with a one-time code, then sign in with a passkey instead of typing a password.

## Privacy and security

pal treats the PC it runs on as trusted, and everything else as untrusted.

- **Only reachable from your PC.** pal only accepts connections from the PC itself. Your other devices reach it through Tailscale, which adds encryption (HTTPS). Every device still has to sign in to pal.
- **No internet access.** When pal starts, it blocks itself from connecting to the internet. There are two exceptions, and both are off unless you turn them on:
  - **Claude chats.** If you save an Anthropic API key, you can pick a Claude model for a chat. Only that chat's messages are sent to Anthropic. Your memories, documents and notes are never sent.
  - **PDF reader setup.** `pal pdf-models` downloads the PDF reader's models once. After that, reading PDFs works offline.
- **No passwords in files.** The database password and API keys are kept in Windows Credential Manager. Your pal password is stored only as a secure hash.
- **Protected against common web attacks.** pal checks where every request comes from and refuses anything unexpected.
- **The AI can't act on its own.** Anything the AI produces (memories, tasks, reminders) waits for your approval. Text inside your documents is treated as content to read, never as instructions to follow.
- **Encrypted nightly backups.** pal backs up its database every night, encrypts the backup and keeps the 14 most recent in `%LOCALAPPDATA%\pal\backups`.

> **Important:** run `uv run pal backup-key --show` once and keep the key somewhere safe and offline. If you reinstall Windows without it, your backups can't be opened.

## What you need

- Windows 10 or 11
- [Python 3.13](https://www.python.org/) and [uv](https://docs.astral.sh/uv/)
- [Docker Desktop](https://www.docker.com/products/docker-desktop/), for the database
- [Ollama](https://ollama.com/), to run the AI models
- [Node.js](https://nodejs.org/) and [pnpm](https://pnpm.io/), to build the web app
- [Tailscale](https://tailscale.com/), only if you want to use pal from other devices

## Getting started

Run these from the project folder.

**1. Download the AI models**

```sh
ollama pull qwen3.5:4b          # the chat model
ollama pull nomic-embed-text    # used to search your documents and memories
```

**2. Start the database.** The first time, this creates a database password and saves it in Credential Manager.

```sh
uv run scripts/db.py up
```

**3. Install pal and set up the database**

```sh
uv sync
uv run pal migrate
```

**4. Build the web app**

```sh
cd web
pnpm install
pnpm build
cd ..
```

**5. Optional: better PDF reading.** This downloads the PDF reader's models once. Without them, pal uses a simpler reader.

```sh
uv run pal pdf-models
```

**6. Choose your password and start pal**

```sh
uv run pal set-password
uv run pal serve
```

Then open **http://127.0.0.1:8765** in your browser.

### Using pal from your phone or laptop

With Tailscale installed and signed in on the PC, run:

```sh
tailscale serve --bg http://127.0.0.1:8765
```

Then pair your device from the **Devices** page. If you can't get into the web app, you can make a pairing code from the command line instead:

```sh
uv run pal pair --name Phone
```

### Starting pal automatically

To have pal start every time you sign in to Windows:

```powershell
powershell -File scripts/install-autostart.ps1
```

This starts Docker and Ollama, waits for the database, then starts pal. Logs are written to `%LOCALAPPDATA%\pal\logs\pal.log`.

To turn this off again:

```powershell
Unregister-ScheduledTask -TaskName pal -Confirm:$false
```

### Using Claude (optional)

```sh
uv run pal claude-key --set
```

Restart pal, and the Claude models show up in the model picker above the chat box. Chats that use a local model stay fully offline. To remove the key, run `uv run pal claude-key --clear`.

## Commands

Run each one as `uv run pal <command>`.

| Command | What it does |
|---|---|
| `serve` | Start pal (it updates the database first if needed) |
| `set-password` | Change your password. This signs out every device. |
| `ingest <path>` | Add a file or folder of documents. Run it again to pick up changes. |
| `pdf-models` | Download the PDF reader's models (once) |
| `pair --name <name>` | Make a one-time code to pair a new device |
| `devices` | List your paired devices |
| `revoke-device <name>` | Remove a device and sign it out |
| `revoke-sessions` | Sign out every device right now |
| `backup` | Make a backup now. Add `--check` to also test that it restores correctly. |
| `backup-key --show` | Show the backup key so you can store it safely. Use `--set` to restore it on a new install. |
| `decrypt-backup <file> <out>` | Turn an encrypted backup back into a normal database dump |
| `claude-key --set` / `--clear` | Save or remove your Anthropic API key |
| `migrate` | Update the database structure (`serve` already does this) |

## Settings

You can change most things on the **Settings** page, such as which models are available and the default thinking level. Lower-level defaults like the port and backup retention are in [src/pal/config.py](src/pal/config.py).

## For developers

<details>
<summary>How it's built, project layout, and running tests</summary>

**Stack:** FastAPI (Python 3.13), PostgreSQL 18 with pgvector in Docker, Ollama for local models, and a React 19 + Vite web app. Search combines keyword search and vector search, merged with reciprocal-rank fusion. PDFs are read with Docling (layout, tables, OCR), with pypdf as a fallback.

**Security details:** the server binds to `127.0.0.1:8765` only and ignores Tailscale identity headers. At startup an egress guard patches `socket` so that connections and DNS lookups to anything other than loopback fail; `api.anthropic.com` is allowed only when a Claude key is stored. Requests must pass Host and Origin allowlists and carry the `x-pal-request` CSRF header, and pages are served with a strict CSP. Passwords are hashed with Argon2, and backups are `pg_dump` output encrypted with AES-256-GCM.

**Project layout**

```
src/pal/        Server, CLI, and features (chat, memory, retrieval, work, reminders, devices, ...)
web/            The web app, built to web/dist and served by pal
migrations/     Database migrations, applied in order
scripts/        Database container helper and autostart scripts
eval/           Model comparison tests; results in eval/RESULTS.md
tests/          pytest suite (real Postgres test database, fake LLM)
compose.yaml    The Postgres container, published on 127.0.0.1 only
```

**Tests and tools**

```sh
uv run pytest                  # needs the database running; uses "pal_test" (override with PAL_TEST_DB)
uv run pytest --cov=pal        # with coverage
cd web && pnpm watch           # rebuild the web app when files change
cd web && pnpm build:demo      # a demo build of the web app with made-up data, in web/dist-demo
bash scripts/deploy_demo.sh    # build the demo and publish it to pal-assistant-demo.vercel.app (needs the Vercel CLI)
uv run eval/run.py --local     # compare local models, offline
```

</details>
