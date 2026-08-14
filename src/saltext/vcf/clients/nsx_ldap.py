"""NSX Management API — LDAP identity sources (``/api/v1/aaa/ldap/identity-sources``).

Configures an external LDAP/Active-Directory identity source NSX Manager
authenticates users against — distinct from :mod:`nsx_role_binding` (which
assigns a role to a principal already known to NSX, not a directory source).

Field names below (``ldap_servers``, ``base_dn``) follow the standard NSX-T
shape but haven't been exercised against a live NSX Manager — verify
against your version before real use.
"""

from saltext.vcf.utils import nsx

PATH = "/api/v1/aaa/ldap/identity-sources"


def list_(opts, profile=None):
    return nsx.api_get(opts, PATH, profile=profile)


def create(opts, name, ldap_servers, base_dn, profile=None, **spec):
    """Create an LDAP identity source named *name*.

    *ldap_servers* is a list of dicts like
    ``[{"url": "ldaps://ldap.corp.example.test:636", "bind_identity": "...",
    "password": "...", "use_starttls": False}]``. *base_dn* scopes the
    directory search. Extra fields (``identity_source_type``, ``domain_name``,
    ...) pass through via *spec*. The id is server-generated; there's no
    named-resource PUT for this endpoint.
    """
    body = {"display_name": name, "ldap_servers": list(ldap_servers), "base_dn": base_dn}
    body.update(spec)
    return nsx.api_post(opts, PATH, body=body, profile=profile)


def update(opts, source_id, body, profile=None):
    return nsx.api_put(opts, f"{PATH}/{source_id}", body=body, profile=profile)


def delete(opts, source_id, profile=None):
    return nsx.api_delete(opts, f"{PATH}/{source_id}", profile=profile)
