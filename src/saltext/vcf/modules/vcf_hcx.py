"""Execution module for HCX Manager.

Thin wrapper around :mod:`saltext.vcf.clients.hcx_manager`.
"""

import logging

import requests

from saltext.vcf.clients import hcx_manager as c

log = logging.getLogger(__name__)

__virtualname__ = "vcf_hcx"


def __virtual__():
    return __virtualname__


def get_version(profile=None):
    """Return the ``/hybridity/api/about`` payload from HCX Manager.

    CLI Example:

    .. code-block:: bash

        salt '*' vcf_hcx.get_version
    """
    return c.get_version(__opts__, profile=profile)


def list_sites(profile=None):
    """Return HCX Manager's registered cloud sites payload.

    CLI Example:

    .. code-block:: bash

        salt '*' vcf_hcx.list_sites
    """
    return c.list_sites(__opts__, profile=profile)


def installed(name, profile=None):
    """Check whether an HCX Manager is installed and reachable.

    *name* is the expected HCX Manager FQDN and is currently informational --
    the actual probe uses the pillar-configured ``saltext.vcf.hcx`` host.
    Returns a dict with keys ``installed`` (bool), ``version`` (str or
    ``None``), and ``about`` (raw payload or ``None``).

    CLI Example:

    .. code-block:: bash

        salt '*' vcf_hcx.installed hcx.example.test
    """
    result = {"name": name, "installed": False, "version": None, "about": None}
    try:
        about = c.get_version(__opts__, profile=profile)
    except (requests.RequestException, RuntimeError):
        return result
    result["installed"] = True
    result["about"] = about
    if isinstance(about, dict):
        result["version"] = about.get("buildVersion") or about.get("version")
    return result


def deploy(spec, profile=None):
    """Deploy an HCX Manager OVA + drive first-boot activation and vCenter register.

    *spec* is a dict shape (typically resolved from pillar
    ``saltext.vcf:hcx:deploy_spec``) with at minimum:

    - ``ova_url`` -- local path or http(s) URL to the HCX Manager OVA.
    - ``vm_name`` -- name to give the VM on the target host.
    - ``target_host`` -- FQDN/IP of the target ESXi/vCenter host.
    - ``target_user``, ``target_password`` -- credentials for the target.
    - ``activation_key`` -- Broadcom-issued HCX subscription key.
    - ``vcenter_url``, ``vcenter_username``, ``vcenter_password``
      -- vCenter registration details.

    Optional:

    - ``sso_url`` -- Lookup Service URL for SSO registration.
    - ``deployment_backend`` -- ``pyvmomi`` (default) or ``ovftool``.
    - ``target_port``, ``datastore``, ``network_map``, ``ovf_properties``,
      ``disk_provisioning``, ``deployment_option``, ``verify_ssl``,
      ``upload_timeout``, ``ovftool_path``, ``ovftool_extra_args``.
    - ``setup_timeout`` (default 1800), ``setup_poll_interval`` (default 15).

    Returns a dict with keys ``deploy``, ``about``, ``activate``, ``vcenter``.

    CLI Example:

    .. code-block:: bash

        salt '*' vcf_hcx.deploy '{"ova_url": ..., ...}'
    """
    backend_name = str(spec.get("deployment_backend", "pyvmomi")).lower()
    deploy_backend = _ova_backend(backend_name)
    ova_kwargs = {
        "ova_source": spec["ova_url"],
        "target_host": spec["target_host"],
        "target_user": spec["target_user"],
        "target_password": spec["target_password"],
        "target_port": int(spec.get("target_port", 443)),
        "vm_name": spec["vm_name"],
        "datastore": spec.get("datastore"),
        "network_map": spec.get("network_map"),
        "ovf_properties": spec.get("ovf_properties"),
        "disk_provisioning": spec.get("disk_provisioning", "thin"),
        "deployment_option": spec.get("deployment_option"),
        "power_on": spec.get("power_on", True),
        "verify_ssl": spec.get("verify_ssl", False),
        "upload_timeout": spec.get("upload_timeout", 3600),
    }
    if backend_name == "ovftool":
        ova_kwargs["ovftool_path"] = spec.get("ovftool_path") or "ovftool"
        ova_kwargs["extra_args"] = spec.get("ovftool_extra_args")

    from saltext.vcf.clients import ovf_deploy as _ovf

    _existing = _ovf.find_vm(
        target_host=ova_kwargs["target_host"],
        target_user=ova_kwargs["target_user"],
        target_password=ova_kwargs["target_password"],
        vm_name=ova_kwargs["vm_name"],
        target_port=ova_kwargs["target_port"],
        verify_ssl=ova_kwargs["verify_ssl"],
    )
    if _existing is not None:
        log.info(
            "HCX VM %r already exists on %s (moid=%s); skipping OVA push",
            ova_kwargs["vm_name"],
            ova_kwargs["target_host"],
            _existing["vm_moid"],
        )
        deploy_result = {**_existing, "skipped_ova_push": True}
    else:
        deploy_result = deploy_backend(**ova_kwargs)
    about = c.wait_for_setup_ready(
        __opts__,
        timeout=int(spec.get("setup_timeout", 1800)),
        poll_interval=int(spec.get("setup_poll_interval", 15)),
        profile=profile,
    )
    # HCX 9.x admin-plane operations need the admin bearer token, which is
    # minted from admin_password (separate from the /hybridity session auth).
    # Read from spec or fall back to pillar saltext.vcf:hcx:password.
    admin_pw = spec.get("admin_password") or __opts__.get("pillar", {}).get(  # noqa: F821
        "saltext.vcf", {}
    ).get("hcx", {}).get("password")
    activate_result = c.activate(
        __opts__,
        activation_key=spec.get("activation_key"),
        profile=profile,
        admin_password=admin_pw,
    )
    vcenter_result = c.configure_vcenter(
        __opts__,
        vcenter_url=spec["vcenter_url"],
        vcenter_username=spec["vcenter_username"],
        vcenter_password=spec["vcenter_password"],
        sso_url=spec.get("sso_url"),
        profile=profile,
        admin_password=admin_pw,
    )
    return {
        "deploy": deploy_result,
        "about": about,
        "activate": activate_result,
        "vcenter": vcenter_result,
    }


def _ova_backend(name):
    """Return the OVA deploy backend callable for *name*.

    Selects between the pyVmomi (``ovf_deploy.deploy_ova``) primary backend
    and the ovftool subprocess (``ovftool_deploy.deploy_ova``) fallback for
    standalone Nimbus ESXi environments.
    """
    from saltext.vcf.clients import ovf_deploy
    from saltext.vcf.clients import ovftool_deploy

    if name == "pyvmomi":
        return ovf_deploy.deploy_ova
    if name == "ovftool":
        return ovftool_deploy.deploy_ova
    raise ValueError(f"unsupported hcx deploy_spec deployment_backend={name!r}")
