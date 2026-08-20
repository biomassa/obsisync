"""Restricting outbound connections to IPv4.

A network can advertise IPv6 and route none of it: the router sends a router
advertisement, the host takes a global address and installs a default route, and
every connection over it then hangs until it times out. Home routers whose
upstream has no IPv6 transit do this, and the host cannot tell the difference
from a working network.

``requests`` has no defence against that. It walks ``getaddrinfo`` in order, and
that order puts IPv6 first, so every connection stalls on a dead address before
it ever reaches a working one. Browsers and curl survive because they implement
Happy Eyeballs (RFC 8305) and race the two families; ``requests`` does not.

Forcing IPv4 sidesteps the whole problem. iCloud is reachable over IPv4
everywhere, so the cost is nothing on a normal network and the gain is that a
broken IPv6 route cannot stall the daemon.

urllib3 chooses the address family in ``allowed_gai_family``, which it calls per
connection, so replacing it takes effect immediately and for every pool.
"""
import socket

import urllib3.util.connection as _urllib3_connection

_original_allowed_gai_family = _urllib3_connection.allowed_gai_family
_forced = False


def _ipv4_only():
    return socket.AF_INET


def force_ipv4(enabled=True):
    """Restrict every urllib3 connection to IPv4, or restore the default.

    This is process-wide, because urllib3 resolves the family through one
    module-level function. obsisync makes no other outbound connection, so the
    scope is the whole of what the setting means.
    """
    global _forced
    _urllib3_connection.allowed_gai_family = (
        _ipv4_only if enabled else _original_allowed_gai_family)
    _forced = bool(enabled)
    return _forced


def ipv4_forced():
    """True when outbound connections are restricted to IPv4."""
    return _forced
