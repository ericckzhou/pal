"""Google connections: saving a sign-in, and disconnecting (purge, then revoke once no Google service is left).

pal connects one Google account. Signing in with a different account while another is connected is refused, and
the stray grant is revoked straight away so pal holds no access it won't use.
"""

import httpx
import psycopg

from .. import secret_store
from . import google_auth, store

ONE_ACCOUNT_NOTE = "pal connects one Google account at a time."
STILL_GRANTED_NOTE = ("Google keeps listing pal's access until the last Google service is disconnected (Google can't "
                      "take back one permission alone). pal no longer reads this service.")


def connected_account(conn: psycopg.Connection) -> str | None:
    row = conn.execute("SELECT account FROM connections WHERE provider = 'google' AND account IS NOT NULL LIMIT 1").fetchone()
    return row[0] if row else None


def save_grant(conn: psycopg.Connection, service: str, grant: google_auth.Grant, http: httpx.Client) -> dict:
    """Record a successful sign-in for `service`. The refresh token replaces the stored one (it covers all scopes)."""
    current = connected_account(conn)
    if current and grant.account and current.lower() != grant.account.lower():
        if grant.refresh_token:
            google_auth.revoke(http, grant.refresh_token)
        raise google_auth.GoogleAuthError(
            f"{ONE_ACCOUNT_NOTE} It's connected to {current}; you signed in as {grant.account}. "
            "Disconnect every Google service first to switch accounts."
        )
    if grant.refresh_token:
        secret_store.put(google_auth.TOKEN_ACCOUNT, grant.refresh_token)
    scopes = sorted(grant.scopes)
    row = store.create(conn, "google", service, grant.account, scopes)
    # Every Google row shares one grant, so they all show what Google granted as of now.
    conn.execute("UPDATE connections SET scopes = %s WHERE provider = 'google'", (scopes,))
    return {**row, "scopes": scopes}


def disconnect(conn: psycopg.Connection, connection_id, http: httpx.Client) -> dict:
    """Remove a connection and everything it brought in. Revokes and deletes the token when it was the last one."""
    row = store.get(conn, connection_id)
    if row is None:
        return {"documents_removed": 0, "revoked": None, "note": None}
    removed = store.remove(conn, connection_id)
    if row["provider"] != "google":
        return {"documents_removed": removed, "revoked": None, "note": None}
    left = (conn.execute("SELECT count(*) FROM connections WHERE provider = 'google'").fetchone() or (0,))[0]
    if left:
        return {"documents_removed": removed, "revoked": False, "note": STILL_GRANTED_NOTE}
    token = secret_store.get(google_auth.TOKEN_ACCOUNT)
    revoked = google_auth.revoke(http, token) if token else True
    secret_store.delete(google_auth.TOKEN_ACCOUNT)
    note = None if revoked else ("Couldn't reach Google to end pal's access; the token is deleted here. Remove pal at "
                                 "myaccount.google.com/permissions to be sure.")
    return {"documents_removed": removed, "revoked": revoked, "note": note}
