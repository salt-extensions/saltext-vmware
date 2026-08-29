"""State module for NSX DHCP server and relay profiles.

Covers both sides of :mod:`nsx_dhcp`: the DHCP Server Profile
(``dhcp-server-configs``) and the DHCP Relay Profile (``dhcp-relay-configs``),
each attached to segments to provide or forward DHCP for workloads.
"""

from saltext.vcf.clients import nsx_dhcp as c

__virtualname__ = "vcf_nsx_dhcp"


def __virtual__():
    return __virtualname__


def _ret(name):
    return {"name": name, "changes": {}, "result": True, "comment": ""}


def server_present(name, server_addresses, profile=None, **spec):
    """Ensure DHCP server profile *name* exists with *server_addresses*.

    *server_addresses* is a list of CIDRs (e.g. ``["10.0.0.2/24"]``) the DHCP
    server itself answers on. Extra keyword args (``lease_time``,
    ``edge_cluster_path``, ...) are passed straight through to the create
    call. Policy API PUT is idempotent, so this only checks presence — it
    does not diff/update fields on an already-existing profile.
    """
    ret = _ret(name)
    if c.server_get_or_none(__opts__, name, profile=profile) is not None:
        ret["comment"] = f"DHCP server profile {name} is already present"
        return ret
    if __opts__["test"]:
        ret["result"] = None
        ret["comment"] = f"DHCP server profile {name} would be created"
        return ret
    c.server_create(
        __opts__, name, profile=profile, server_addresses=list(server_addresses), **spec
    )
    ret["changes"] = {"new": name}
    ret["comment"] = f"DHCP server profile {name} created"
    return ret


def server_absent(name, profile=None):
    """Ensure DHCP server profile *name* does not exist."""
    ret = _ret(name)
    if c.server_get_or_none(__opts__, name, profile=profile) is None:
        ret["comment"] = f"DHCP server profile {name} is already absent"
        return ret
    if __opts__["test"]:
        ret["result"] = None
        ret["comment"] = f"DHCP server profile {name} would be deleted"
        return ret
    c.server_delete(__opts__, name, profile=profile)
    ret["changes"] = {"deleted": name}
    ret["comment"] = f"DHCP server profile {name} deleted"
    return ret


def relay_present(name, server_addresses, profile=None, **spec):
    """Ensure DHCP relay profile *name* exists, forwarding to *server_addresses*.

    *server_addresses* is a list of upstream DHCP server IPs to relay
    requests to. Extra keyword args are passed straight through to the
    create call. Policy API PUT is idempotent, so this only checks
    presence — it does not diff/update fields on an already-existing
    profile.
    """
    ret = _ret(name)
    if c.relay_get_or_none(__opts__, name, profile=profile) is not None:
        ret["comment"] = f"DHCP relay profile {name} is already present"
        return ret
    if __opts__["test"]:
        ret["result"] = None
        ret["comment"] = f"DHCP relay profile {name} would be created"
        return ret
    c.relay_create(__opts__, name, list(server_addresses), profile=profile, **spec)
    ret["changes"] = {"new": name}
    ret["comment"] = f"DHCP relay profile {name} created"
    return ret


def relay_absent(name, profile=None):
    """Ensure DHCP relay profile *name* does not exist."""
    ret = _ret(name)
    if c.relay_get_or_none(__opts__, name, profile=profile) is None:
        ret["comment"] = f"DHCP relay profile {name} is already absent"
        return ret
    if __opts__["test"]:
        ret["result"] = None
        ret["comment"] = f"DHCP relay profile {name} would be deleted"
        return ret
    c.relay_delete(__opts__, name, profile=profile)
    ret["changes"] = {"deleted": name}
    ret["comment"] = f"DHCP relay profile {name} deleted"
    return ret
