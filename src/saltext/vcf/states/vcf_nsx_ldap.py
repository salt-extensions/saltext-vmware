"""State module for NSX LDAP identity sources."""

from saltext.vcf.clients import nsx_ldap as c

__virtualname__ = "vcf_nsx_ldap"


def __virtual__():
    return __virtualname__


def _ret(name):
    return {"name": name, "changes": {}, "result": True, "comment": ""}


def _find_by_name(opts, name, profile=None):
    listed = c.list_(opts, profile=profile) or {}
    for entry in listed.get("results") or []:
        if entry.get("display_name") == name:
            return entry
    return None


def present(name, ldap_servers, base_dn, profile=None, **spec):
    """Ensure LDAP identity source *name* exists.

    Presence-only: an existing source's fields are not diffed/updated here
    (the id is server-generated, so there's no named-resource PUT contract
    to reconcile a single field against).
    """
    ret = _ret(name)
    if _find_by_name(__opts__, name, profile=profile) is not None:
        ret["comment"] = f"LDAP identity source {name} is already present"
        return ret
    if __opts__["test"]:
        ret["result"] = None
        ret["comment"] = f"LDAP identity source {name} would be created"
        return ret
    c.create(__opts__, name, ldap_servers, base_dn, profile=profile, **spec)
    ret["changes"] = {"new": name}
    ret["comment"] = f"LDAP identity source {name} created"
    return ret


def absent(name, profile=None):
    """Ensure no LDAP identity source named *name* exists."""
    ret = _ret(name)
    current = _find_by_name(__opts__, name, profile=profile)
    if current is None:
        ret["comment"] = f"LDAP identity source {name} is already absent"
        return ret
    if __opts__["test"]:
        ret["result"] = None
        ret["comment"] = f"LDAP identity source {name} would be deleted"
        return ret
    c.delete(__opts__, current["id"], profile=profile)
    ret["changes"] = {"deleted": name}
    ret["comment"] = f"LDAP identity source {name} deleted"
    return ret
