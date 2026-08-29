"""Execution module for the AVI Controller (NSX Advanced Load Balancer).

MVP surface: version discovery and a reachability probe. Full config-plane
operations (cloud onboarding, service engines, virtual services) are handled
by future per-resource modules mirroring the ``mops-config-modules`` AVI
controller layout.
"""

import logging

from saltext.vcf.clients import avi_controller as c

log = logging.getLogger(__name__)

__virtualname__ = "vcf_avi"


def __virtual__():
    return __virtualname__


def get_version(profile=None):
    """Return the AVI Controller version banner.

    Hits ``GET /api/initial-data`` and returns its ``version`` block
    (``{"Version": "...", "build": ..., ...}``), or the ``/api/cluster/version``
    payload on older controllers.

    CLI Example:

    .. code-block:: bash

        salt '*' vcf_avi.get_version
    """
    return c.get_version(__opts__, profile=profile)


def ping(profile=None):
    """Return ``True`` iff the configured AVI Controller answers ``get_version``.

    CLI Example:

    .. code-block:: bash

        salt '*' vcf_avi.ping
    """
    return c.ping(__opts__, profile=profile)


def installed(name=None, profile=None):
    """Verify the AVI Controller is present and reachable.

    Verify-only wrapper suitable for the ``vcf_avi.installed`` state to call.
    Returns a dict::

        {
            "installed": bool,
            "version": <version-block dict or None>,
            "error": <str or None>,
        }

    :param str name: Optional friendly name (unused; matches state signature).
    :param str profile: Optional pillar profile name.

    CLI Example:

    .. code-block:: bash

        salt '*' vcf_avi.installed
    """
    _ = name  # signature parity with the state; nothing to gate on
    try:
        version = c.get_version(__opts__, profile=profile)
        return {"installed": True, "version": version, "error": None}
    except Exception as exc:  # pylint: disable=broad-except
        log.debug("AVI Controller probe failed: %s", exc)
        return {"installed": False, "version": None, "error": str(exc)}


def deploy(spec, profile=None):
    """Push the AVI Controller OVA, wait for first-boot, run the wizard.

    Drives the full deploy-on-absence flow:

    1. OVA push via :func:`saltext.vcf.clients.avi_controller.deploy_ova`
       (backend chosen by ``spec['deployment_backend']``).
    2. Poll ``/api/initial-data`` until the wizard becomes reachable.
    3. POST the first-boot wizard with pillar-supplied admin password,
       DNS/NTP servers, and backup passphrase.
    4. Optional ``PUT /api/cluster`` when ``spec['cluster_nodes']`` is set.
    5. One final ``get_version`` to confirm the Controller is healthy.

    *spec* is the same dict shape consumed by
    :func:`~saltext.vcf.clients.avi_controller.deploy_ova`, plus:

    - ``admin_password`` — required, sent to the wizard.
    - ``dns_servers`` — list, optional (defaults to ``[]``).
    - ``ntp_servers`` — list, optional (defaults to ``[]``).
    - ``backup_passphrase`` — required by the wizard.
    - ``cluster_nodes`` — optional list of ``{name, ip}`` for 3-node HA.
    - ``cluster_ip`` — optional virtual IP for HA cluster.
    - ``ready_timeout`` — seconds to wait for the wizard (default 1800).
    - ``ready_poll_interval`` — seconds between wizard polls (default 15).

    Returns a dict::

        {"deployed": True, "version": <version-block>, "vm_name": ..., ...}

    CLI Example:

    .. code-block:: bash

        salt '*' vcf_avi.deploy spec="{...}"
    """
    from saltext.vcf.clients import ovf_deploy as _ovf

    _existing = _ovf.find_vm(
        target_host=spec["target_host"],
        target_user=spec["target_user"],
        target_password=spec["target_password"],
        vm_name=spec["vm_name"],
        target_port=int(spec.get("target_port", 443)),
        verify_ssl=spec.get("verify_ssl", False),
    )
    if _existing is not None:
        log.info(
            "AVI VM %r already exists on %s (moid=%s); skipping OVA push",
            spec["vm_name"],
            spec["target_host"],
            _existing["vm_moid"],
        )
        deploy_result = {**_existing, "skipped_ova_push": True}
    else:
        deploy_result = c.deploy_ova(spec)
    c.wait_for_setup_ready(
        __opts__,
        timeout=int(spec.get("ready_timeout", 1800)),
        poll_interval=int(spec.get("ready_poll_interval", 15)),
        profile=profile,
    )
    # AVI 22.x+ has an 8-step wizard (see avi_controller.bootstrap_wizard docstring).
    # Default admin password is baked into the OVA at /opt/avi/bootstrap/default_password
    # and is the same across every VM built from that OVA — see project-avi-bootstrap
    # memory. Caller supplies via spec['default_password'] (populated by the gobuild
    # catalog step for the pushed OVA build).
    wizard = c.bootstrap_wizard(
        __opts__,
        default_password=spec["default_password"],
        new_password=spec["admin_password"],
        dns_servers=spec.get("dns_servers") or [],
        ntp_servers=spec.get("ntp_servers") or [],
        backup_passphrase=spec["backup_passphrase"],
        dns_search_domain=spec.get("dns_search_domain"),
        profile=profile,
    )
    cluster_nodes = spec.get("cluster_nodes")
    cluster_result = None
    if cluster_nodes:
        cluster_result = c.configure_cluster(
            __opts__,
            nodes=cluster_nodes,
            cluster_ip=spec.get("cluster_ip"),
            profile=profile,
        )
    version = c.get_version(__opts__, profile=profile)
    return {
        "deployed": True,
        "version": version,
        "wizard": wizard,
        "cluster": cluster_result,
        **(deploy_result if isinstance(deploy_result, dict) else {}),
    }
