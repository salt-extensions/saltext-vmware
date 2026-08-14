"""State module for NSX Manager syslog exporters."""

from saltext.vcf.clients import nsx_syslog_exporter as c

__virtualname__ = "vcf_nsx_syslog_exporter"


def __virtual__():
    return __virtualname__


def _ret(name):
    return {"name": name, "changes": {}, "result": True, "comment": ""}


def present(name, server, port, protocol, profile=None, **spec):
    """Ensure syslog exporter *name* exists, forwarding to *server*:*port* via *protocol*.

    Presence-only: an existing exporter's fields are not diffed/updated
    here.
    """
    ret = _ret(name)
    if c.get_or_none(__opts__, name, profile=profile) is not None:
        ret["comment"] = f"Syslog exporter {name} is already present"
        return ret
    if __opts__["test"]:
        ret["result"] = None
        ret["comment"] = f"Syslog exporter {name} would be created"
        return ret
    c.create(__opts__, name, server, port, protocol, profile=profile, **spec)
    ret["changes"] = {"new": name}
    ret["comment"] = f"Syslog exporter {name} created"
    return ret


def absent(name, profile=None):
    """Ensure no syslog exporter named *name* exists."""
    ret = _ret(name)
    if c.get_or_none(__opts__, name, profile=profile) is None:
        ret["comment"] = f"Syslog exporter {name} is already absent"
        return ret
    if __opts__["test"]:
        ret["result"] = None
        ret["comment"] = f"Syslog exporter {name} would be deleted"
        return ret
    c.delete(__opts__, name, profile=profile)
    ret["changes"] = {"deleted": name}
    ret["comment"] = f"Syslog exporter {name} deleted"
    return ret
