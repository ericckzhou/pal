"""S8: in-process guard that refuses any network connection or DNS lookup that isn't loopback.

Guarded: TCP connect/connect_ex, UDP sendto/sendmsg, and name lookups (getaddrinfo, gethostbyname[_ex]).

Two narrow exceptions, both decided at `pal serve` time:
- `extra_hosts`: named internet hosts (only api.anthropic.com, and only when a Claude key is stored). Sockets
  connect to addresses, not names, so the addresses those names resolve to are remembered and allowed.
- `local_services`: pal's own database and Ollama when they run as Docker services ("db", "ollama"). Their names
  may be looked up, and connections are allowed only to the *private* addresses those names resolved to, so a
  misconfigured name can never open a path to the internet.

Limit: this guards pal's own Python process. Ollama, Docker, subprocesses and native code that opens sockets
without going through Python's socket module are not covered.
"""

import ipaddress
import socket

_LOOPBACK_NAMES = {"localhost"}
_allowed_hosts: set[str] = set()
_allowed_ips: set[str] = set()
_service_names: set[str] = set()
_service_ips: set[str] = set()
_installed = False
_real_getaddrinfo = socket.getaddrinfo
_real_gethostbyname = socket.gethostbyname
_real_gethostbyname_ex = socket.gethostbyname_ex


class EgressBlocked(OSError):
    pass


def _name(host: object) -> str:
    return (host.decode() if isinstance(host, bytes) else str(host)).lower()


def _bare(name: str) -> str:
    return name.split("%")[0]


def _is_loopback_ip(name: str) -> bool:
    try:
        return ipaddress.ip_address(_bare(name)).is_loopback
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
        or _bare(name) in _allowed_ips
        or _bare(name) in _service_ips
        or _is_loopback_ip(name)
    )


def _addresses(results: list) -> list[str]:
    return [
        _bare(str(sockaddr[0]))
        for family, _type, _proto, _canon, sockaddr in results
        if family in (socket.AF_INET, socket.AF_INET6)
    ]


def _remember_service_ips(results: list) -> None:
    """Record the private addresses a local service name resolved to; public ones are never allowed."""
    _service_ips.update(ip for ip in _addresses(results) if ipaddress.ip_address(ip).is_private)


def _check_address(sock: socket.socket, address: object, verb: str) -> None:
    if sock.family in (socket.AF_INET, socket.AF_INET6) and isinstance(address, tuple) and not _is_allowed(address[0]):
        raise EgressBlocked(f"outbound {verb} to {address[0]} blocked by pal egress guard")


def _check_lookup(host: object) -> None:
    if not _is_allowed(host):
        raise EgressBlocked(f"DNS lookup of {host} blocked by pal egress guard")


def install(extra_hosts: tuple[str, ...] = (), local_services: tuple[str, ...] = ()) -> None:
    """Patch socket so non-loopback connects, sends and lookups raise. Idempotent."""
    global _installed
    _allowed_hosts.update(h.lower() for h in extra_hosts)
    _service_names.update(h.lower() for h in local_services)
    if _installed:
        return

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_sendto = socket.socket.sendto
    real_sendmsg = getattr(socket.socket, "sendmsg", None)  # not on Windows

    def connect(self, address):
        _check_address(self, address, "connection")
        return real_connect(self, address)

    def connect_ex(self, address):
        _check_address(self, address, "connection")
        return real_connect_ex(self, address)

    def sendto(self, data, *args):  # sendto(data, address) or sendto(data, flags, address)
        if args:
            _check_address(self, args[-1], "datagram")
        return real_sendto(self, data, *args)

    def sendmsg(self, buffers, *args):  # sendmsg(buffers[, ancdata[, flags[, address]]])
        if len(args) >= 3:
            _check_address(self, args[2], "datagram")
        return real_sendmsg(self, buffers, *args)  # type: ignore[misc]

    def getaddrinfo(host, *args, **kwargs):
        _check_lookup(host)
        results = _real_getaddrinfo(host, *args, **kwargs)
        if host is not None:
            name = _name(host)
            if name in _service_names:
                _remember_service_ips(results)
            elif name in _allowed_hosts:
                _allowed_ips.update(_addresses(results))
        return results

    def gethostbyname(host):
        _check_lookup(host)
        return _real_gethostbyname(host)

    def gethostbyname_ex(host):
        _check_lookup(host)
        return _real_gethostbyname_ex(host)

    socket.socket.connect = connect  # type: ignore[method-assign]
    socket.socket.connect_ex = connect_ex  # type: ignore[method-assign]
    socket.socket.sendto = sendto  # type: ignore[method-assign]
    if real_sendmsg is not None:
        setattr(socket.socket, "sendmsg", sendmsg)  # noqa: B010 (absent from Windows type stubs)
    socket.getaddrinfo = getaddrinfo
    socket.gethostbyname = gethostbyname
    socket.gethostbyname_ex = gethostbyname_ex
    _installed = True
