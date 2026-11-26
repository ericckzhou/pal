## What and why

<!-- What does this change, and why? Link the issue if there is one (e.g. "Fixes #12"). -->

## How I tested it

<!-- Tests added or changed, and anything you checked by hand. -->

## Checklist

- [ ] `uv run ruff check src tests eval scripts` and `uv run mypy` pass
- [ ] `uv run pytest --cov=pal` passes, with coverage at 80% or above
- [ ] `cd web && pnpm lint && pnpm build` pass (if the web app changed)
- [ ] No new outbound connections, telemetry or remote assets
- [ ] No personal data, secrets or real documents in the diff
- [ ] README updated if a command, setting or user-facing behaviour changed
