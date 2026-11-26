"""S8: in-process guard that refuses any network connection or DNS lookup that isn't loopback.

Two narrow exceptions, both decided at `pal serve` time:
- `extra_hosts`: named internet hosts (only api.anthropic.com, and only when a Claude key is stored).
- `local_services`: pal's own database and Ollama when they run as Docker services ("db", "ollama"). Their names
  may be looked up, and connections are allowed only to the *private* addresses those names resolved to, so a
  misconfigured name can never open a path to the internet.

Limit: this guards pal's own Python process. Ollama, Docker and other programs on the PC are not covered.
"""

import ipaddress
import socket

_LOOPBACK_NAMES = {"localhost"}
_allowed_hosts: set[str] = set()
_service_names: set[str] = set()
_service_ips: set[str] = set()
_installed = False


class EgressBlocked(OSError):
    pass


def _name(host: object) -> str:
    return (host.decode() if isinstance(host, bytes) else str(host)).lower()


def _is_loopback_ip(name: str) -> bool:
    try:
        return ipaddress.ip_address(name.split("%")[0]).is_loopback
    except ValueError:
        return False


def _is_allowed(host: object) -> bool:
    if host is None:
        return True
    name = _name(host)
    return (
        name in _LOOPBACK_NAMES
        or name in _allowed_hosts
        or name in _service_names
        or name.split("%")[0] in _service_ips
        or _is_loopback_ip(name)
    )


def _remember_service_ips(results: list) -> None:
    """Record the private addresses a local service name resolved to; public ones are never allowed."""
    for family, _type, _proto, _canon, sockaddr in results:
        if family in (socket.AF_INET, socket.AF_INET6):
            ip = str(sockaddr[0]).split("%")[0]
            if ipaddress.ip_address(ip).is_private:
                _service_ips.add(ip)


def install(extra_hosts: tuple[str, ...] = (), local_services: tuple[str, ...] = ()) -> None:
    """Patch socket so non-loopback connects/lookups raise. Idempotent."""
    global _installed
    _allowed_hosts.update(h.lower() for h in extra_hosts)
    _service_names.update(h.lower() for h in local_services)
    if _installed:
        return

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    def check(sock: socket.socket, address: object) -> None:
        if sock.family in (socket.AF_INET, socket.AF_INET6) and isinstance(address, tuple) and not _is_allowed(address[0]):
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
        results = real_getaddrinfo(host, *args, **kwargs)
        if host is not None and _name(host) in _service_names:
            _remember_service_ips(results)
        return results

    socket.socket.connect = connect  # type: ignore[method-assign]
    socket.socket.connect_ex = connect_ex  # type: ignore[method-assign]
    socket.getaddrinfo = getaddrinfo
    _installed = True
