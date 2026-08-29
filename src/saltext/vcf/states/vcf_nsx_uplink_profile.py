"""State module for NSX uplink (host-switch) profiles."""

from saltext.vcf.clients import nsx_uplink_profile as c

__virtualname__ = "vcf_nsx_uplink_profile"


def __virtual__():
    return __virtualname__


def _ret(name):
    return {"name": name, "changes": {}, "result": True, "comment": ""}


def present(name, teaming, profile=None, **spec):
    """Ensure uplink profile *name* exists with *teaming*.

    Policy API PUT is idempotent, so this only checks presence — it does
    not diff/update fields on an already-existing profile.
    """
    ret = _ret(name)
    if c.get_or_none(__opts__, name, profile=profile) is not None:
        ret["comment"] = f"Uplink profile {name} is already present"
        return ret
    if __opts__["test"]:
        ret["result"] = None
        ret["comment"] = f"Uplink profile {name} would be created"
        return ret
    c.create(__opts__, name, teaming, profile=profile, **spec)
    ret["changes"] = {"new": name}
    ret["comment"] = f"Uplink profile {name} created"
    return ret


def absent(name, profile=None):
    """Ensure uplink profile *name* does not exist."""
    ret = _ret(name)
    if c.get_or_none(__opts__, name, profile=profile) is None:
        ret["comment"] = f"Uplink profile {name} is already absent"
        return ret
    if __opts__["test"]:
        ret["result"] = None
        ret["comment"] = f"Uplink profile {name} would be deleted"
        return ret
    c.delete(__opts__, name, profile=profile)
    ret["changes"] = {"deleted": name}
    ret["comment"] = f"Uplink profile {name} deleted"
    return ret
