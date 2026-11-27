"""Command-line side of Google connections: google-client, connect, disconnect, connections.

Signing in happens here rather than in the web UI: it needs a browser on this PC and a short-lived local port, and
keeping it on the command line means a phone session can never start or change a Google connection.
"""

import argparse
import webbrowser
from pathlib import Path

import httpx
import psycopg

from .. import secret_store
from ..config import Settings, dsn, in_container
from . import google, google_auth, store

SERVICES = tuple(google_auth.SCOPES)
DISCLOSURE = {
    "calendar": "read your Google Calendar events (read-only)",
    "drive": "read every file in your Google Drive (read-only; pal only syncs the folders you pick, but Google's "
             "permission covers the whole Drive)",
    "gmail": "read your Gmail (read-only; pal searches it only when you ask and never stores mail)",
}


def add_parsers(sub: argparse._SubParsersAction) -> None:
    gc = sub.add_parser("google-client", help="check, set or remove your Google OAuth client (needed to connect Google)")
    gc_mode = gc.add_mutually_exclusive_group()
    gc_mode.add_argument("--set", dest="client_file", metavar="FILE", help="the client JSON downloaded from Google Cloud")
    gc_mode.add_argument("--clear", action="store_true", help="remove the stored client")
    con = sub.add_parser("connect", help="sign in to Google and connect one service (calendar, drive or gmail)")
    con.add_argument("provider", choices=["google"])
    con.add_argument("service", choices=SERVICES)
    con.add_argument("--no-browser", action="store_true", help="print the sign-in link instead of opening a browser")
    con.add_argument("--port", type=int, default=0, help="fixed local port for Google's answer (Docker needs one)")
    dis = sub.add_parser("disconnect", help="remove a connected service and everything pal synced from it")
    dis.add_argument("provider", choices=["google"])
    dis.add_argument("service", choices=(*SERVICES, "all"))
    sub.add_parser("connections", help="list connected services")


def run(args: argparse.Namespace, settings: Settings) -> bool:
    """Handle the Google commands. False if `args.cmd` isn't one of them."""
    if args.cmd == "google-client":
        cmd_google_client(args.client_file, args.clear)
    elif args.cmd == "connect":
        cmd_connect(settings, args.service, args.no_browser, args.port)
    elif args.cmd == "disconnect":
        cmd_disconnect(settings, args.service)
    elif args.cmd == "connections":
        cmd_connections(settings)
    else:
        return False
    return True


def cmd_google_client(client_file: str | None, clear: bool) -> None:
    if client_file:
        try:
            client = google_auth.store_client(Path(client_file).read_text(encoding="utf-8"))
        except (OSError, google_auth.GoogleAuthError) as exc:
            raise SystemExit(str(exc)) from exc
        print(f"Saved client {client.client_id[:24]}… to {secret_store.where()}. You can delete the downloaded file.")
        print("Next: pal connect google calendar")
    elif clear:
        print("Removed." if secret_store.delete(google_auth.CLIENT_ACCOUNT) else "No Google client was stored.")
    else:
        stored = google_auth.load_client()
        print(f"A Google OAuth client is stored ({stored.client_id[:24]}…)." if stored else
              "No Google OAuth client is stored. See the README's \"Connect Google\" section.")


def cmd_connect(settings: Settings, service: str, no_browser: bool, port: int) -> None:
    client = google_auth.load_client()
    if client is None:
        raise SystemExit("Store your Google OAuth client first: pal google-client --set client.json")
    print(f"pal will ask Google to {DISCLOSURE[service]}.")

    def open_browser(url: str) -> None:
        if no_browser or not webbrowser.open(url):
            print("Open this link in a browser on this computer and sign in:\n\n" + url + "\n")
        else:
            print("A browser window opened for Google sign-in. Waiting for you to finish…")

    host = "0.0.0.0" if in_container() else "127.0.0.1"  # in Docker the published port must reach the container
    with httpx.Client() as http:
        try:
            grant = google_auth.sign_in(client, service, http, open_browser, host=host, port=port)
            with psycopg.connect(dsn(settings), autocommit=True) as conn:
                row = google.save_grant(conn, service, grant, http)
        except google_auth.GoogleAuthError as exc:
            raise SystemExit(str(exc)) from exc
    granted = sorted(s.rsplit("/", 1)[-1] for s in row["scopes"] if s.startswith("https://"))
    print(f"Connected {service} for {row['account'] or 'your Google account'}. Granted: {', '.join(granted)}.")
    print("pal starts syncing within a few minutes if it's running; no restart needed.")


def cmd_disconnect(settings: Settings, service: str) -> None:
    with psycopg.connect(dsn(settings), autocommit=True) as conn, httpx.Client() as http:
        rows = [r for r in store.list_all(conn) if r["provider"] == "google" and service in (r["service"], "all")]
        if not rows:
            raise SystemExit(f"Google {service} isn't connected.")
        for r in rows:
            result = google.disconnect(conn, r["id"], http)
            print(f"Disconnected {r['service']}: removed {result['documents_removed']} synced document(s).")
            if result["note"]:
                print("  " + result["note"])
            elif result["revoked"]:
                print("  Google access ended and the sign-in token deleted.")


def cmd_connections(settings: Settings) -> None:
    with psycopg.connect(dsn(settings), autocommit=True) as conn:
        rows = store.list_all(conn)
    if not rows:
        print("Nothing connected. Start with: pal connect google calendar")
    for r in rows:
        state = "on" if r["enabled"] else "paused"
        last = r["last_ok_at"].strftime("%Y-%m-%d %H:%M") if r["last_ok_at"] else "never"
        err = f"  last error: {r['last_error']}" if r["last_error"] else ""
        print(f"{r['provider']}/{r['service']:<9} {state:<7} {r['account'] or '-':<30} {r['items']:>5} items  "
              f"last synced {last}{err}")
