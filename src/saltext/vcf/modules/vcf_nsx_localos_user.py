"""Execution module for the NSX Manager node's own local CLI user accounts."""

from saltext.vcf.clients import nsx_localos_user as c

__virtualname__ = "vcf_nsx_localos_user"


def __virtual__():
    return __virtualname__


def list_(profile=None):
    """List.

    CLI Example:

    .. code-block:: bash

        salt '*' vcf_nsx_localos_user.list_

    """
    return c.list_(__opts__, profile=profile)


def get_or_none(username, profile=None):
    """Get or none.

    CLI Example:

    .. code-block:: bash

        salt '*' vcf_nsx_localos_user.get_or_none <username>

    """
    return c.get_or_none(__opts__, username, profile=profile)


def create(username, password, role, profile=None, **spec):
    """Create.

    CLI Example:

    .. code-block:: bash

        salt '*' vcf_nsx_localos_user.create <username> <password> <role>

    """
    return c.create(__opts__, username, password, role, profile=profile, **spec)


def update(userid, body, profile=None):
    """Update.

    CLI Example:

    .. code-block:: bash

        salt '*' vcf_nsx_localos_user.update <userid> <body>

    """
    return c.update(__opts__, userid, body, profile=profile)


def delete(userid, profile=None):
    """Delete.

    CLI Example:

    .. code-block:: bash

        salt '*' vcf_nsx_localos_user.delete <userid>

    """
    return c.delete(__opts__, userid, profile=profile)
