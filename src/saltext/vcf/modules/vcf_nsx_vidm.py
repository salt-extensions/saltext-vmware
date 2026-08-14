"""Execution module for NSX Manager's vIDM integration."""

from saltext.vcf.clients import nsx_vidm as c

__virtualname__ = "vcf_nsx_vidm"


def __virtual__():
    return __virtualname__


def get(profile=None):
    """Get.

    CLI Example:

    .. code-block:: bash

        salt '*' vcf_nsx_vidm.get

    """
    return c.get(__opts__, profile=profile)


def update(profile=None, **body):
    """Update.

    CLI Example:

    .. code-block:: bash

        salt '*' vcf_nsx_vidm.update vidm_enable=True

    """
    return c.update(__opts__, profile=profile, **body)
