#!/usr/bin/env bash
# Build the public demo (web UI + made-up data, no server) and publish it to Vercel.
# Needs the Vercel CLI signed in. Usage: bash scripts/deploy_demo.sh
set -euo pipefail

PROJECT=pal-demo
ALIAS=pal-assistant-demo.vercel.app
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

(cd "$ROOT/web" && pnpm build:demo)
cp -r "$ROOT/web/dist-demo/." "$STAGE/"
cd "$STAGE"
vercel link --yes --project "$PROJECT" >/dev/null
URL="$(vercel deploy --prod --yes 2>/dev/null | grep -Eo 'https://[^ ]+\.vercel\.app' | head -1)"
if [ -z "$URL" ]; then
  URL="$(vercel ls "$PROJECT" 2>/dev/null | grep -Eo 'https://[^ ]+\.vercel\.app' | head -1)"
fi
vercel alias set "$URL" "$ALIAS"
echo "Demo live at https://$ALIAS"
