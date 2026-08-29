"""Execution module for NSX Manager syslog exporters."""

from saltext.vcf.clients import nsx_syslog_exporter as c

__virtualname__ = "vcf_nsx_syslog_exporter"


def __virtual__():
    return __virtualname__


def list_(profile=None):
    """List.

    CLI Example:

    .. code-block:: bash

        salt '*' vcf_nsx_syslog_exporter.list_

    """
    return c.list_(__opts__, profile=profile)


def get(exporter_name, profile=None):
    """Get.

    CLI Example:

    .. code-block:: bash

        salt '*' vcf_nsx_syslog_exporter.get <exporter_name>

    """
    return c.get(__opts__, exporter_name, profile=profile)


def create(exporter_name, server, port, protocol, profile=None, **spec):
    """Create.

    CLI Example:

    .. code-block:: bash

        salt '*' vcf_nsx_syslog_exporter.create <exporter_name> <server> <port> <protocol>

    """
    return c.create(__opts__, exporter_name, server, port, protocol, profile=profile, **spec)


def delete(exporter_name, profile=None):
    """Delete.

    CLI Example:

    .. code-block:: bash

        salt '*' vcf_nsx_syslog_exporter.delete <exporter_name>

    """
    return c.delete(__opts__, exporter_name, profile=profile)
