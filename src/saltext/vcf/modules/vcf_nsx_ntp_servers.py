"""Execution module for the NSX Manager node's own NTP config."""

from saltext.vcf.clients import nsx_ntp_servers as c

__virtualname__ = "vcf_nsx_ntp_servers"


def __virtual__():
    return __virtualname__


def ntp_get(profile=None):
    """Ntp get.

    CLI Example:

    .. code-block:: bash

        salt '*' vcf_nsx_ntp_servers.ntp_get

    """
    return c.ntp_get(__opts__, profile=profile)


def ntp_set(servers, profile=None):
    """Ntp set.

    CLI Example:

    .. code-block:: bash

        salt '*' vcf_nsx_ntp_servers.ntp_set <servers>

    """
    return c.ntp_set(__opts__, servers, profile=profile)
