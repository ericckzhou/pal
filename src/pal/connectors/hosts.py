"""The outside hosts each connected service needs. The egress guard allows only these, and only for services that
are connected and switched on when pal starts."""

import psycopg

GOOGLE_TOKEN_HOST = "oauth2.googleapis.com"  # refreshing and revoking sign-in tokens
HOSTS: dict[tuple[str, str], tuple[str, ...]] = {
    ("google", "calendar"): (GOOGLE_TOKEN_HOST, "www.googleapis.com"),
    ("google", "drive"): (GOOGLE_TOKEN_HOST, "www.googleapis.com"),
    ("google", "gmail"): (GOOGLE_TOKEN_HOST, "gmail.googleapis.com"),
}


def for_services(services: list[tuple[str, str]]) -> tuple[str, ...]:
    hosts: set[str] = set()
    for key in services:
        hosts.update(HOSTS.get(key, ()))
    return tuple(sorted(hosts))


def enabled_hosts(conn: psycopg.Connection) -> tuple[str, ...]:
    rows = conn.execute("SELECT provider, service FROM connections WHERE enabled").fetchall()
    return for_services([(p, s) for p, s in rows])
