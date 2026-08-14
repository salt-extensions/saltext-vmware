"""NSX Manager node's own DNS resolver config (Node API, ``/api/v1/node/...``).

Appliance-level name resolution for the NSX Manager itself — analogous to
:mod:`vcenter_appliances`'s ``dns_get``/``dns_set``, not the workload-facing
DNS Forwarder Zone service.
"""

from saltext.vcf.utils import nsx

_NAME_SERVERS = "/api/v1/node/network/name-servers"


def dns_get(opts, profile=None):
    """Return ``{"name_servers": [...]}``."""
    return nsx.api_get(opts, _NAME_SERVERS, profile=profile)


def dns_set(opts, servers, profile=None):
    return nsx.api_put(opts, _NAME_SERVERS, body={"name_servers": list(servers)}, profile=profile)
