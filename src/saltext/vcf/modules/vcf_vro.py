"""Execution module for VCF Orchestrator (VRO).

Two surfaces:

* :func:`get_version` / :func:`installed` — verify-only probes against
  ``/vco/api/about``.
* :func:`deploy` — push the VRO OVA to a target ESXi/vCenter, wait for
  first-boot, join the vCenter SSO / Lookup Service, and optionally
  install a license. See :mod:`saltext.vcf.states.vcf_vro` for the
  idempotent state that wraps this.
"""

import logging

import requests

from saltext.vcf.clients import installer_appliance as ia_client
from saltext.vcf.clients import vro_orchestrator as c

log = logging.getLogger(__name__)

__virtualname__ = "vcf_vro"


def __virtual__():
    return __virtualname__


def get_version(profile=None):
    """Return VRO's ``/vco/api/about`` payload.

    CLI Example:

    .. code-block:: bash

        salt '*' vcf_vro.get_version
    """
    return c.get_version(__opts__, profile=profile)


def installed(name, version=None, profile=None):
    """Verify that VCF Orchestrator is installed and reachable.

    Contacts the configured VRO appliance and returns::

        {
            "installed": True/False,
            "version": <version-string or None>,
            "api_version": <api-version-string or None>,
            "reason": <short human explanation on failure>,
        }

    When *version* is supplied and does not match the reported version,
    ``installed`` is reported ``False`` with ``reason`` describing the
    mismatch.

    :param str name: Descriptive VRO instance name (informational only).
    :param str version: Optional required version string.
    :param str profile: Pillar profile name for multi-target setups.

    CLI Example:

    .. code-block:: bash

        salt '*' vcf_vro.installed vro-prod version=9.0.0
    """
    result = {
        "installed": False,
        "version": None,
        "api_version": None,
        "reason": None,
    }
    try:
        about = c.get_version(__opts__, profile=profile)
    except requests.exceptions.RequestException as exc:
        result["reason"] = f"VRO API at {name!r} unreachable: {exc}"
        return result
    result["version"] = about.get("version")
    result["api_version"] = about.get("api-version") or about.get("apiVersion")
    if version is not None and result["version"] != version:
        result["reason"] = (
            f"VRO version mismatch: expected {version!r}, " f"reported {result['version']!r}"
        )
        return result
    result["installed"] = True
    return result


def deploy(
    spec,
    profile=None,
    post_deploy_timeout=1800,
    post_sso_timeout=1800,
    poll_interval=20,
):
    """Deploy a fresh VRO appliance and drive it to a serving state.

    *spec* mirrors the pillar shape used by
    :mod:`saltext.vcf.clients.installer_appliance` (``installer_ova_url``,
    ``installer_vm_name``, ``installer_deploy_esxi``, ``esxi_hosts``,
    plus optional ``deployment_backend``/``datastore``/``network_map``/
    ``ovf_properties``/``disk_provisioning``/``deployment_option``) and
    additionally accepts VRO-specific keys:

    * ``sso.lookup_service_url`` — e.g.
      ``https://vc.example.test/lookupservice/sdk``
    * ``sso.admin_user`` — vCenter SSO administrator (e.g.
      ``administrator@vsphere.local``)
    * ``sso.admin_password`` — that account's password
    * ``license_key`` — *optional*; installed after the SSO restart

    Sequence:

    1. Push the OVA via :func:`installer_appliance.deploy_installer`
       (pyVmomi by default, ovftool when ``deployment_backend`` says
       so).
    2. :func:`wait_for_setup_ready` — poll ``/vco/api/about`` for
       first-boot to complete (up to ``post_deploy_timeout`` s).
    3. :func:`sso_join` — POST to the Control Center; the appliance
       restarts.
    4. :func:`wait_for_setup_ready` again — bridge the restart (up to
       ``post_sso_timeout`` s).
    5. If ``spec['license_key']`` is set, :func:`install_license`.
    6. Re-fetch ``/vco/api/about`` and return the version dict.

    Returns::

        {
            "deployed": True,
            "ova": <deploy_installer return dict>,
            "sso_joined": True,
            "license_installed": <bool>,
            "version": <about.version>,
            "about": <full /vco/api/about payload>,
        }

    Raises whatever the underlying step raised (``TimeoutError`` from
    the wait helpers, ``requests.HTTPError`` from the Control Center
    endpoints, ``RuntimeError`` / ``LookupError`` from the OVA
    backends).

    CLI Example:

    .. code-block:: bash

        salt '*' vcf_vro.deploy '{...}'
    """
    from saltext.vcf.clients import ovf_deploy as _ovf

    _vc_creds = (spec.get("esxi_hosts") or [{}])[0]
    _existing = _ovf.find_vm(
        target_host=spec["installer_deploy_esxi"],
        target_user=_vc_creds.get("username", ""),
        target_password=_vc_creds.get("password", ""),
        vm_name=spec["installer_vm_name"],
        target_port=int(spec.get("installer_port", 443)),
        verify_ssl=spec.get("verify_ssl", False),
    )
    if _existing is not None:
        log.info(
            "vRO VM %r already exists on %s (moid=%s); skipping OVA push",
            spec["installer_vm_name"],
            spec["installer_deploy_esxi"],
            _existing["vm_moid"],
        )
        ova_result = {**_existing, "skipped_ova_push": True}
    else:
        ova_result = ia_client.deploy_installer(spec)
    log.info("vcf_vro.deploy: OVA push complete: %s", ova_result)

    c.wait_for_setup_ready(
        __opts__,
        timeout=int(post_deploy_timeout),
        poll_interval=int(poll_interval),
        profile=profile,
    )

    sso_cfg = spec.get("sso") or {}
    lookup_url = sso_cfg.get("lookup_service_url")
    admin_user = sso_cfg.get("admin_user")
    admin_password = sso_cfg.get("admin_password")
    if not (lookup_url and admin_user and admin_password):
        raise KeyError(
            "vro deploy spec missing sso.lookup_service_url / sso.admin_user / "
            "sso.admin_password — cannot join VRO to vCenter SSO"
        )
    c.sso_join(
        __opts__,
        lookup_service_url=lookup_url,
        admin_user=admin_user,
        admin_password=admin_password,
        profile=profile,
    )
    log.info("vcf_vro.deploy: SSO join posted; waiting for restart to complete")

    c.wait_for_setup_ready(
        __opts__,
        timeout=int(post_sso_timeout),
        poll_interval=int(poll_interval),
        profile=profile,
    )

    license_installed = False
    license_key = spec.get("license_key")
    if license_key:
        c.install_license(__opts__, license_key, profile=profile)
        license_installed = True

    about = c.get_version(__opts__, profile=profile)
    return {
        "deployed": True,
        "ova": ova_result,
        "sso_joined": True,
        "license_installed": license_installed,
        "version": about.get("version"),
        "about": about,
    }
