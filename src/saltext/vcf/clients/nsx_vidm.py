"""NSX Manager's VMware Identity Manager (vIDM) integration (``/api/v1/aaa/vidm``).

Singleton config enabling OAuth-based SSO login against vIDM/Workspace ONE
Access — distinct from :mod:`nsx_ldap` (a directory-backed identity source)
and :mod:`nsx_role_binding` (assigning a role once a principal is known).

Field names below follow the standard NSX-T shape but haven't been
exercised against a live NSX Manager — verify before real use.
"""

from saltext.vcf.utils import nsx

PATH = "/api/v1/aaa/vidm"


def get(opts, profile=None):
    """Return the current vIDM integration config."""
    return nsx.api_get(opts, PATH, profile=profile)


def update(opts, profile=None, **body):
    """Replace the vIDM config with *body* via PUT.

    Callers should include the full config document as returned by
    :func:`get` (with any fields modified) since the endpoint is a
    PUT-style replace.
    """
    return nsx.api_put(opts, PATH, body=body, profile=profile)
