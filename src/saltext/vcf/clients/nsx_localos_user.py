"""NSX Manager node's own local CLI/UI user accounts (``/api/v1/node/users``).

OS-level accounts on the NSX Manager appliance itself (used for SSH/CLI
login) — distinct from :mod:`nsx_role_binding` (which assigns a role to a
remote/external principal already known to NSX, not a local account).

Field/path names below follow the standard NSX-T Node API shape but haven't
been exercised against a live NSX Manager — verify before real use.
"""

from saltext.vcf.utils import nsx

PATH = "/api/v1/node/users"


def list_(opts, profile=None):
    return nsx.api_get(opts, PATH, profile=profile)


def get_or_none(opts, username, profile=None):
    """Return the user entry for *username*, or ``None`` if not found."""
    listed = list_(opts, profile=profile) or {}
    for entry in listed.get("results") or []:
        if entry.get("username") == username:
            return entry
    return None


def create(opts, username, password, role, profile=None, **spec):
    """Create a local CLI user. *role* is e.g. ``"admin"`` or ``"auditor"``."""
    body = {"username": username, "password": password, "role": role}
    body.update(spec)
    return nsx.api_post(opts, PATH, body=body, profile=profile)


def update(opts, userid, body, profile=None):
    return nsx.api_put(opts, f"{PATH}/{userid}", body=body, profile=profile)


def delete(opts, userid, profile=None):
    return nsx.api_delete(opts, f"{PATH}/{userid}", profile=profile)
