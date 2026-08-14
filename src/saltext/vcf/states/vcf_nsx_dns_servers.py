"""State module for the NSX Manager node's own DNS resolver config."""

from saltext.vcf.clients import nsx_dns_servers as c

__virtualname__ = "vcf_nsx_dns_servers"


def __virtual__():
    return __virtualname__


def _ret(name):
    return {"name": name, "changes": {}, "result": True, "comment": ""}


def dns_servers(name, servers, profile=None):
    """Ensure the NSX Manager's DNS server list matches *servers*.

    *name* is descriptive.
    """
    ret = _ret(name)
    current = c.dns_get(__opts__, profile=profile) or {}
    current_servers = sorted(current.get("name_servers") or [])
    desired_servers = sorted(servers)

    if current_servers == desired_servers:
        ret["comment"] = "DNS already configured"
        return ret
    if __opts__["test"]:
        ret["result"] = None
        ret["comment"] = "DNS would change: servers"
        return ret
    c.dns_set(__opts__, servers, profile=profile)
    ret["changes"] = {"servers": {"old": current_servers, "new": desired_servers}}
    ret["comment"] = "DNS updated: servers"
    return ret
