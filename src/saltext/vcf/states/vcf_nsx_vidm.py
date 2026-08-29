"""State module for NSX Manager's vIDM integration."""

from saltext.vcf.clients import nsx_vidm as c

__virtualname__ = "vcf_nsx_vidm"


def __virtual__():
    return __virtualname__


def _ret(name):
    return {"name": name, "changes": {}, "result": True, "comment": ""}


def enabled(name, vidm_enable=True, profile=None, **spec):
    """Ensure vIDM integration matches *vidm_enable* (and any extra *spec*
    fields, e.g. ``vidm_hostname``, ``client_id``, ``client_secret``,
    ``vidm_domain``), preserving other fields from the current config.

    *name* is a label for the state and is not sent to NSX. Note: if the
    API masks secret-valued fields (``client_secret``) on read (returning
    a redacted placeholder rather than the real value), comparing a
    caller-supplied plaintext secret against that placeholder will always
    show drift and re-PUT every run — harmless (idempotent on the NSX
    side) but worth knowing if you see "changed" every time.
    """
    ret = _ret(name)
    current = c.get(__opts__, profile=profile) or {}
    wanted = {"vidm_enable": bool(vidm_enable), **spec}
    diff = {k: {"old": current.get(k), "new": v} for k, v in wanted.items() if current.get(k) != v}

    if not diff:
        ret["comment"] = "vIDM integration already matches"
        return ret
    if __opts__["test"]:
        ret["result"] = None
        ret["comment"] = f"vIDM integration would change: {sorted(diff)}"
        return ret
    body = dict(current)
    body.update({k: v["new"] for k, v in diff.items()})
    c.update(__opts__, profile=profile, **body)
    ret["changes"] = diff
    ret["comment"] = "vIDM integration updated"
    return ret
