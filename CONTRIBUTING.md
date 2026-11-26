# Contributing to pal

Thanks for your interest. pal is a small personal project, so the bar for changes is "does this keep pal private, simple and working on one PC?" Bug reports, fixes and small improvements are very welcome. For anything bigger, please open an issue first so we can agree on the approach before you spend time on it.

## Ground rules

pal's main promise is that your data stays on your machine. Changes must keep it that way:

- **No new outbound connections.** Don't add telemetry, analytics, update checks, remote fonts or CDN assets. The only exceptions are Claude's API (only for chats that opt in) and the one-time `pal pdf-models` download. The egress guard (`src/pal/egress.py`) enforces this, and tests check it.
- **No personal data in the repo.** Don't commit real conversations, documents, database dumps, logs, `.env` files or keys. Use made-up data in tests and in the demo (`web/src/demo/`).
- **Secrets go in the secret store** (`src/pal/secret_store.py`): the system keyring, or files in `PAL_SECRETS_DIR` for Docker and CI. Never put them in code or settings.
- **Hosts, ports and secrets stay out of the web UI.** They're set from the command line on the PC.

## Getting set up

Follow [Getting started](README.md#getting-started) in the README, then the [For developers](README.md#for-developers) section for the layout of the code. Tests need the PostgreSQL container running (`uv run scripts/db.py up`).

## Before you open a pull request

Run the same checks as CI:

```
uv run ruff check src tests eval scripts
uv run mypy
uv run pytest --cov=pal --cov-fail-under=80
cd web && pnpm lint && pnpm build
```

- **Add tests** for new behaviour and for bug fixes (a test that fails before your fix). Coverage must stay at 80% or above.
- **Schema changes** go in a new numbered file in `migrations/`. Never edit a migration that has already been released.
- **Keep pull requests focused:** one change per pull request, with a short description of what changed and why.
- **Commit messages** use the [Conventional Commits](https://www.conventionalcommits.org/) style: `feat: …`, `fix: …`, `docs: …`, `test: …`, `refactor: …`, `chore: …`.
- **Update the README** if you change a command, a setting or anything user-facing.

## Reporting bugs and asking for features

Use the [issue templates](https://github.com/ericckzhou/pal/issues/new/choose). Please remove anything personal (names, file paths, conversation text) from logs and screenshots before posting.

**Security problems:** don't open a public issue. See [SECURITY.md](SECURITY.md).

## Code of conduct

By taking part you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## License

By contributing, you agree that your contributions are licensed under the [MIT License](LICENSE).
