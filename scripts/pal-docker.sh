#!/usr/bin/env bash
# Run pal on Linux or macOS with Docker (pal + PostgreSQL + Ollama).
#
#   bash scripts/pal-docker.sh setup          first run: secrets, build, start, download models, set password
#   bash scripts/pal-docker.sh up | down      start or stop (data is kept)
#   bash scripts/pal-docker.sh logs           follow pal's log
#   bash scripts/pal-docker.sh pal <command>  any pal command, e.g. `pal pair --name Phone`
#   bash scripts/pal-docker.sh google-client FILE   store your Google OAuth client JSON
#   bash scripts/pal-docker.sh connect SERVICE      sign in to Google: calendar, drive or gmail
#
# Secrets and data live in $PAL_HOME (default ~/.local/share/pal-docker), never in this repo.
# Set PAL_DOCS=/path/to/folder to make a folder of documents available to `pal ingest /docs`.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PAL_HOME="${PAL_HOME:-$HOME/.local/share/pal-docker}"
export PAL_UID="$(id -u)" PAL_GID="$(id -g)"
CHAT_MODEL="qwen3.5:4b"
EMBED_MODEL="nomic-embed-text"

compose() { docker compose -f "$ROOT/compose.docker.yaml" "$@"; }

prepare() {
  mkdir -p "$PAL_HOME/data" "$PAL_HOME/secrets"
  chmod 700 "$PAL_HOME/secrets"
  if [ ! -s "$PAL_HOME/secrets/postgres" ]; then
    head -c 32 /dev/urandom | base64 | tr -d '/+=\n' > "$PAL_HOME/secrets/postgres"
    echo "Generated a database password in $PAL_HOME/secrets/postgres"
  fi
  # Readable inside the database container (it runs as its own user); the folder above stays private to you.
  chmod 644 "$PAL_HOME/secrets/postgres"
  mkdir -p "$ROOT/.no-docs"
}

case "${1:-}" in
  setup)
    prepare
    compose up -d --build --wait db ollama
    compose exec ollama ollama pull "$CHAT_MODEL"
    compose exec ollama ollama pull "$EMBED_MODEL"
    compose up -d --wait pal
    compose exec pal pal set-password
    echo
    echo "pal is running at http://127.0.0.1:8765"
    echo "Optional: better PDF reading (one-time download): bash scripts/pal-docker.sh pal pdf-models"
    echo "Store this backup key somewhere safe and offline:  bash scripts/pal-docker.sh pal backup-key --show"
    ;;
  up)   prepare; compose up -d --build --wait ;;
  down) compose down ;;  # never -v: the data volumes are kept
  logs) compose logs -f pal ;;
  pal)  shift; compose exec pal pal "$@" ;;
  google-client)
    [ -f "${2:-}" ] || { echo "usage: $0 google-client <client JSON downloaded from Google Cloud>"; exit 2; }
    compose exec -T pal pal google-client --set /dev/stdin < "$2"  # the file never has to be inside the container
    ;;
  connect)
    [ -n "${2:-}" ] || { echo "usage: $0 connect calendar|drive|gmail"; exit 2; }
    # The container can't open your browser: pal prints the link. Open it on this computer.
    compose exec pal pal connect google "$2" --no-browser --port "${PAL_OAUTH_PORT:-8766}"
    ;;
  *)    sed -n '2,12p' "$0"; exit 2 ;;
esac
