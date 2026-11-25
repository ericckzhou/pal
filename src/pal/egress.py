"""S8: in-process guard that refuses any network connection or DNS lookup that isn't loopback."""

import ipaddress
import socket

_LOOPBACK_NAMES = {"localhost"}
_allowed_hosts: set[str] = set()
_installed = False


class EgressBlocked(OSError):
    pass


def _is_allowed(host: object) -> bool:
    if host is None:
        return True
    name = host.decode() if isinstance(host, bytes) else str(host)
    if name.lower() in _LOOPBACK_NAMES or name.lower() in _allowed_hosts:
        return True
    try:
        return ipaddress.ip_address(name.split("%")[0]).is_loopback
    except ValueError:
        return False


def install(extra_hosts: tuple[str, ...] = ()) -> None:
    """Patch socket so non-loopback connects/lookups raise. Idempotent."""
    global _installed
    _allowed_hosts.update(h.lower() for h in extra_hosts)
    if _installed:
        return

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    def check(sock: socket.socket, address: object) -> None:
        if sock.family in (socket.AF_INET, socket.AF_INET6) and isinstance(address, tuple):
            if not _is_allowed(address[0]):
                raise EgressBlocked(f"outbound connection to {address[0]} blocked by pal egress guard")

    def connect(self, address):
        check(self, address)
        return real_connect(self, address)

    def connect_ex(self, address):
        check(self, address)
        return real_connect_ex(self, address)

    def getaddrinfo(host, *args, **kwargs):
        if not _is_allowed(host):
            raise EgressBlocked(f"DNS lookup of {host} blocked by pal egress guard")
        return real_getaddrinfo(host, *args, **kwargs)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.getaddrinfo = getaddrinfo
    _installed = True
