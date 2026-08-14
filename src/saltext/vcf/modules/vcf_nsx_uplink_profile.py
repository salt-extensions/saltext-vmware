"""Execution module for NSX uplink (host-switch) profiles."""

from saltext.vcf.clients import nsx_uplink_profile as c

__virtualname__ = "vcf_nsx_uplink_profile"


def __virtual__():
    return __virtualname__


def list_(profile=None):
    """List.

    CLI Example:

    .. code-block:: bash

        salt '*' vcf_nsx_uplink_profile.list_

    """
    return c.list_(__opts__, profile=profile)


def get(profile_id, profile=None):
    """Get.

    CLI Example:

    .. code-block:: bash

        salt '*' vcf_nsx_uplink_profile.get <profile_id>

    """
    return c.get(__opts__, profile_id, profile=profile)


def create(profile_id, teaming, profile=None, **spec):
    """Create.

    CLI Example:

    .. code-block:: bash

        salt '*' vcf_nsx_uplink_profile.create <profile_id> <teaming>

    """
    return c.create(__opts__, profile_id, teaming, profile=profile, **spec)


def delete(profile_id, profile=None):
    """Delete.

    CLI Example:

    .. code-block:: bash

        salt '*' vcf_nsx_uplink_profile.delete <profile_id>

    """
    return c.delete(__opts__, profile_id, profile=profile)
