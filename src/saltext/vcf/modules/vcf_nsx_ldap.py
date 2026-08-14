"""Execution module for NSX LDAP identity sources."""

from saltext.vcf.clients import nsx_ldap as c

__virtualname__ = "vcf_nsx_ldap"


def __virtual__():
    return __virtualname__


def list_(profile=None):
    """List.

    CLI Example:

    .. code-block:: bash

        salt '*' vcf_nsx_ldap.list_

    """
    return c.list_(__opts__, profile=profile)


def create(name, ldap_servers, base_dn, profile=None, **spec):
    """Create.

    CLI Example:

    .. code-block:: bash

        salt '*' vcf_nsx_ldap.create <name> <ldap_servers> <base_dn>

    """
    return c.create(__opts__, name, ldap_servers, base_dn, profile=profile, **spec)


def update(source_id, body, profile=None):
    """Update.

    CLI Example:

    .. code-block:: bash

        salt '*' vcf_nsx_ldap.update <source_id> <body>

    """
    return c.update(__opts__, source_id, body, profile=profile)


def delete(source_id, profile=None):
    """Delete.

    CLI Example:

    .. code-block:: bash

        salt '*' vcf_nsx_ldap.delete <source_id>

    """
    return c.delete(__opts__, source_id, profile=profile)
