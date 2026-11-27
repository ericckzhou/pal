"""pal command line: serve, set-password, migrate, backup, backup-key, decrypt-backup, revoke-sessions, pair, devices, revoke-device, ingest, pdf-models, google-client, connect, disconnect, connections."""

import argparse
import getpass
import ipaddress
import logging
import sys
from dataclasses import replace

import psycopg

from . import secret_store
from .config import Settings, dsn


def check_loopback(host: str) -> None:
    from .config import in_container

    if in_container() and host == "0.0.0.0":
        # Inside pal's container the port must be reachable from Docker's network; compose publishes it on the
        # host's 127.0.0.1 only, so pal is still unreachable from other machines.
        return
    try:
        ok = ipaddress.ip_address(host).is_loopback
    except ValueError:
        ok = host == "localhost"
    if not ok:
        raise SystemExit(f"Refusing to listen on {host}: pal only binds to loopback. Use Tailscale Serve for remote access.")


def _local_services(settings: Settings) -> tuple[str, ...]:
    """Hosts pal's own services run on when they aren't loopback (Docker service names such as "db", "ollama")."""
    from urllib.parse import urlparse

    hosts = {settings.db_host, urlparse(settings.ollama_url).hostname or ""}
    return tuple(h for h in hosts if h and h not in ("127.0.0.1", "localhost", "::1"))


def _connector_hosts(settings: Settings) -> tuple[str, ...]:
    """Outside hosts for connected services that are switched on now; none if the database can't say."""
    from .connectors import hosts

    try:
        with psycopg.connect(dsn(settings)) as conn:
            return hosts.enabled_hosts(conn)
    except Exception:
        logging.getLogger("pal").exception("could not read connected services; none are allowed out this run")
        return ()


def cmd_serve(settings: Settings) -> None:
    import uvicorn

    from . import egress
    from .app import create_app
    from .migrate import migrate
    from .tailnet import detect_public_host, log_serve_state

    check_loopback(settings.host)
    public_host = detect_public_host()
    if public_host:
        settings = replace(settings, public_host=public_host)
        logging.getLogger("pal").info("remote access via https://%s (Tailscale Serve)", public_host)
        log_serve_state()
    migrate(dsn(settings))
    from . import claude

    try:
        has_key = bool(claude.load_key())
    except Exception:
        has_key = False
    egress.install(extra_hosts=((claude.API_HOST,) if has_key else ()) + _connector_hosts(settings),
                   local_services=_local_services(settings))
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="info", proxy_headers=False)


def cmd_pdf_models() -> None:
    """The one network step for Docling: fetch its models once. Converting PDFs afterwards is fully offline."""
    try:
        from .pdf import download_models

        folder = download_models()
    except ImportError as exc:
        raise SystemExit("Docling isn't installed. Run: uv sync") from exc
    print(f"Docling models saved to {folder}. Restart pal; PDFs from added folders are re-read on the next rescan.")


def cmd_set_password(settings: Settings) -> None:
    from .auth import set_password

    first = getpass.getpass("New pal password (8+ chars): ")
    if first != getpass.getpass("Repeat: "):
        raise SystemExit("Passwords didn't match.")
    with psycopg.connect(dsn(settings), autocommit=True) as conn:
        set_password(conn, first)
    print("Password set. All existing sessions were signed out.")


def cmd_backup(settings: Settings, check: bool) -> None:
    from .backup import restore_check, run_backup
    from .backup_crypto import load_key

    key = load_key()
    path = run_backup(settings.backup_dir, key, settings.db_name, settings.backup_keep)
    print(f"Encrypted backup written: {path}")
    if check:
        report = restore_check(path, key, settings.db_name)
        for table, (src, restored) in sorted(report["tables"].items()):
            print(f"  {table:20} source={src:<6} restored={restored}")
        print("Restore check:", "OK" if report["ok"] else "MISMATCH")
        if not report["ok"]:
            sys.exit(1)


def cmd_backup_key(show: bool, set_key: bool) -> None:
    from .backup_crypto import BackupKeyError, decode_key, encode_key, load_key, store_key

    if set_key:
        try:
            key = decode_key(getpass.getpass("Paste the backup key: "))
        except BackupKeyError as exc:
            raise SystemExit(str(exc)) from exc
        store_key(key)
        print(f"Backup key saved to {secret_store.where()}.")
    elif show:
        print("Keep this somewhere safe and offline (a password manager or paper).")
        print("Without it, pal's backups can't be read on a reinstalled or different PC.")
        print()
        print(encode_key(load_key()))
    else:
        load_key(create=False)
        print(f"A backup key is stored in {secret_store.where()}. Use --show to copy it somewhere safe.")


def cmd_claude_key(set_key: bool, clear: bool) -> None:
    from . import claude

    if set_key:
        try:
            claude.store_key(getpass.getpass("Paste the Anthropic API key: "))
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        print(f"Saved to {secret_store.where()}. Restart pal, then pick a Claude model for a chat to use it.")
    elif clear:
        print("Removed. Restart pal." if claude.clear_key() else "No key was stored.")
    else:
        print("An Anthropic API key is stored." if claude.load_key() else "No Anthropic API key is stored.")


def cmd_decrypt_backup(src: str, out: str) -> None:
    from pathlib import Path

    from .backup_crypto import BackupKeyError, load_key, read_dump

    target = Path(out)
    if target.exists():
        raise SystemExit(f"{target} already exists.")
    try:
        target.write_bytes(read_dump(Path(src), load_key(create=False)))
    except BackupKeyError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Plain pg_dump written to {target}. Delete it once you've restored from it.")


def cmd_pair(settings: Settings, name: str, kind: str) -> None:
    from .devices import DeviceError, create_pairing_code
    from .tailnet import detect_public_host

    try:
        with psycopg.connect(dsn(settings), autocommit=True) as conn:
            made = create_pairing_code(conn, name, kind)
    except DeviceError as exc:
        raise SystemExit(str(exc)) from exc
    host = detect_public_host()
    print(f"Pairing code for {made['name']}: {made['code']}  (works once, for 10 minutes)")
    if host:
        print(f"On the device, open: https://{host}/?pair={made['raw']}")


def cmd_devices(settings: Settings) -> None:
    from .devices import list_devices

    with psycopg.connect(dsn(settings), autocommit=True) as conn:
        rows = list_devices(conn)
    if not rows:
        print("No paired devices.")
    for d in rows:
        state = f"revoked {d['revoked_at'][:10]}" if d["revoked_at"] else f"last seen {(d['last_seen_at'] or '-')[:16]}"
        print(f"{d['id']}  {d['name']:20} {d['kind']:7} {state}")


def cmd_revoke_device(settings: Settings, which: str) -> None:
    from uuid import UUID

    from .devices import list_devices, revoke_device

    with psycopg.connect(dsn(settings), autocommit=True) as conn:
        live = [d for d in list_devices(conn) if not d["revoked_at"]]
        matches = [d for d in live if d["id"] == which or d["name"].lower() == which.lower()]
        if len(matches) != 1:
            raise SystemExit(f"{'No' if not matches else 'More than one'} active device matches {which!r}. See: pal devices")
        revoke_device(conn, UUID(matches[0]["id"]))
    print(f"Revoked {matches[0]['name']}. Its sessions ended and its passkey no longer works.")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="pal")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="run pal on 127.0.0.1:8765")
    sub.add_parser("set-password", help="set the owner password (signs out all sessions)")
    sub.add_parser("migrate", help="apply database migrations")
    backup = sub.add_parser("backup", help="write a database backup now")
    backup.add_argument("--check", action="store_true", help="also restore it into a scratch DB and compare")
    bk = sub.add_parser("backup-key", help="check, show or restore the key that encrypts backups")
    bk_mode = bk.add_mutually_exclusive_group()
    bk_mode.add_argument("--show", action="store_true", help="print the key so you can store it offline")
    bk_mode.add_argument("--set", dest="set_key", action="store_true", help="save a key you stored earlier")
    ck = sub.add_parser("claude-key", help="check, set or remove the Anthropic API key (Claude chats)")
    ck_mode = ck.add_mutually_exclusive_group()
    ck_mode.add_argument("--set", dest="set_key", action="store_true", help="paste a key (stored in the OS credential store)")
    ck_mode.add_argument("--clear", action="store_true", help="remove the stored key")
    dec = sub.add_parser("decrypt-backup", help="write a plain pg_dump from an encrypted backup (for pg_restore)")
    dec.add_argument("file")
    dec.add_argument("out")
    sub.add_parser("revoke-sessions", help="sign out every device immediately")
    pair = sub.add_parser("pair", help="make a one-time code to pair a phone or laptop (use when locked out of the UI)")
    pair.add_argument("--name", default="Phone")
    pair.add_argument("--kind", default="phone", choices=["phone", "laptop", "tablet", "other"])
    sub.add_parser("devices", help="list paired devices")
    rd = sub.add_parser("revoke-device", help="revoke a paired device by name or id")
    rd.add_argument("device")
    ing = sub.add_parser("ingest", help="add a file or folder (PDF, Markdown, text); re-run to refresh changed files")
    ing.add_argument("path")
    sub.add_parser("pdf-models", help="download Docling's layout/table/OCR models (one-time; better PDF reading)")
    from .connectors import google_cli

    google_cli.add_parsers(sub)
    args = parser.parse_args()
    settings = Settings.from_env()
    if google_cli.run(args, settings):
        return

    if args.cmd == "serve":
        cmd_serve(settings)
    elif args.cmd == "set-password":
        cmd_set_password(settings)
    elif args.cmd == "migrate":
        from .migrate import migrate

        print("Applied:", migrate(dsn(settings)) or "nothing (up to date)")
    elif args.cmd == "backup":
        cmd_backup(settings, args.check)
    elif args.cmd == "backup-key":
        cmd_backup_key(args.show, args.set_key)
    elif args.cmd == "claude-key":
        cmd_claude_key(args.set_key, args.clear)
    elif args.cmd == "decrypt-backup":
        cmd_decrypt_backup(args.file, args.out)
    elif args.cmd == "revoke-sessions":
        from .auth import revoke_all

        with psycopg.connect(dsn(settings), autocommit=True) as conn:
            print(f"Revoked {revoke_all(conn)} session(s).")
    elif args.cmd == "pair":
        cmd_pair(settings, args.name, args.kind)
    elif args.cmd == "devices":
        cmd_devices(settings)
    elif args.cmd == "revoke-device":
        cmd_revoke_device(settings, args.device)
    elif args.cmd == "pdf-models":
        cmd_pdf_models()
    elif args.cmd == "ingest":
        from pathlib import Path

        from .ingest import IngestError, ingest_path

        try:
            with psycopg.connect(dsn(settings), autocommit=True) as conn:
                s = ingest_path(conn, str(Path(args.path).resolve()))
        except IngestError as exc:
            raise SystemExit(str(exc)) from exc
        print(f"Added {s['added']}, updated {s['updated']}, unchanged {s['unchanged']}, skipped {len(s['skipped'])}.")
        for item in s["skipped"]:
            print(f"  skipped {item['path']}: {item['reason']}")
        print("pal indexes them for meaning-based search in the background while it's running.")
