"""NSX Manager node's own NTP config (Node API, ``/api/v1/node/...``).

Appliance-level time sync for the NSX Manager itself — analogous to
:mod:`nsx_dns_servers`.
"""

from saltext.vcf.utils import nsx

_NTP_SERVERS = "/api/v1/node/network/ntp-servers"


def ntp_get(opts, profile=None):
    """Return ``{"ntp_servers": [...]}``."""
    return nsx.api_get(opts, _NTP_SERVERS, profile=profile)


def ntp_set(opts, servers, profile=None):
    return nsx.api_put(opts, _NTP_SERVERS, body={"ntp_servers": list(servers)}, profile=profile)
