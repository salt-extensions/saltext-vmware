"""State module for the NSX Manager node's own local CLI user accounts."""

from saltext.vcf.clients import nsx_localos_user as c

__virtualname__ = "vcf_nsx_localos_user"


def __virtual__():
    return __virtualname__


def _ret(name):
    return {"name": name, "changes": {}, "result": True, "comment": ""}


def present(name, password, role, profile=None, **spec):
    """Ensure local CLI user *name* exists with *role*.

    Password rotation is intentionally not reconciled here: the Node API
    has no idempotent "set password to X" contract without knowing whether
    it's already that value, and resetting it on every run would clobber
    any out-of-band rotation. Set it at creation; rotate it out-of-band (or
    via ``vcf_nsx_localos_user.update``) when needed.
    """
    ret = _ret(name)
    existing = c.get_or_none(__opts__, name, profile=profile)

    if existing is None:
        if __opts__["test"]:
            ret["result"] = None
            ret["comment"] = f"local CLI user {name} would be created"
            return ret
        c.create(__opts__, name, password, role, profile=profile, **spec)
        ret["changes"] = {"new": name}
        ret["comment"] = f"local CLI user {name} created"
        return ret

    if existing.get("role") == role:
        ret["comment"] = f"local CLI user {name} already matches"
        return ret
    if __opts__["test"]:
        ret["result"] = None
        ret["comment"] = f"local CLI user {name} would change role"
        return ret
    c.update(__opts__, existing["userid"], {"role": role}, profile=profile)
    ret["changes"] = {"role": {"old": existing.get("role"), "new": role}}
    ret["comment"] = f"local CLI user {name} updated"
    return ret


def absent(name, profile=None):
    """Ensure no local CLI user named *name* exists."""
    ret = _ret(name)
    existing = c.get_or_none(__opts__, name, profile=profile)
    if existing is None:
        ret["comment"] = f"local CLI user {name} is already absent"
        return ret
    if __opts__["test"]:
        ret["result"] = None
        ret["comment"] = f"local CLI user {name} would be deleted"
        return ret
    c.delete(__opts__, existing["userid"], profile=profile)
    ret["changes"] = {"deleted": name}
    ret["comment"] = f"local CLI user {name} deleted"
    return ret
