"""Execution module for the NSX Manager node's own DNS resolver config."""

from saltext.vcf.clients import nsx_dns_servers as c

__virtualname__ = "vcf_nsx_dns_servers"


def __virtual__():
    return __virtualname__


def dns_get(profile=None):
    """Dns get.

    CLI Example:

    .. code-block:: bash

        salt '*' vcf_nsx_dns_servers.dns_get

    """
    return c.dns_get(__opts__, profile=profile)


def dns_set(servers, profile=None):
    """Dns set.

    CLI Example:

    .. code-block:: bash

        salt '*' vcf_nsx_dns_servers.dns_set <servers>

    """
    return c.dns_set(__opts__, servers, profile=profile)
