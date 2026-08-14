"""State module for the NSX Manager node's own NTP config."""

from saltext.vcf.clients import nsx_ntp_servers as c

__virtualname__ = "vcf_nsx_ntp_servers"


def __virtual__():
    return __virtualname__


def _ret(name):
    return {"name": name, "changes": {}, "result": True, "comment": ""}


def ntp_servers(name, servers, profile=None):
    """Ensure the NSX Manager's NTP server list matches *servers*.

    *name* is descriptive.
    """
    ret = _ret(name)
    current = c.ntp_get(__opts__, profile=profile) or {}
    current_servers = sorted(current.get("ntp_servers") or [])
    desired_servers = sorted(servers)

    if current_servers == desired_servers:
        ret["comment"] = "NTP already configured"
        return ret
    if __opts__["test"]:
        ret["result"] = None
        ret["comment"] = "NTP would change: servers"
        return ret
    c.ntp_set(__opts__, servers, profile=profile)
    ret["changes"] = {"servers": {"old": current_servers, "new": desired_servers}}
    ret["comment"] = "NTP updated: servers"
    return ret
