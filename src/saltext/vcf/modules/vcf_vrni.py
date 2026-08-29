"""Execution module for VCF Network Insight (VRNI).

Two surfaces:

* Reachability + install verification (``get_version``, ``installed``)
  — the MVP verify-only surface consumed by ``states/vcf_vrni.py``.
* Deploy (``deploy_platform``, ``deploy_collector``, ``deploy``) — the
  full Platform-plus-N-Collectors OVA push used by the state module's
  deploy-on-absence path.

VRNI's on-prem architecture is a single Platform VM (API + UI + DB) plus
N Collector/Proxy VMs. Collectors join the Platform by presenting a
shared secret minted by the Platform. See the state module's ``installed``
docstring for the full step-by-step of what ``deploy`` executes.
"""

import logging

import requests

from saltext.vcf.clients import installer_appliance as ia
from saltext.vcf.clients import ovf_deploy
from saltext.vcf.clients import ovftool_deploy
from saltext.vcf.clients import vrni_platform as c

log = logging.getLogger(__name__)

__virtualname__ = "vcf_vrni"


def __virtual__():
    return __virtualname__


def get_version(profile=None):
    """Return the VRNI Platform version payload.

    CLI Example:

    .. code-block:: bash

        salt '*' vcf_vrni.get_version
    """
    return c.get_version(__opts__, profile=profile)


def list_data_sources(profile=None):
    """List every registered data source on the Platform.

    CLI Example:

    .. code-block:: bash

        salt '*' vcf_vrni.list_data_sources
    """
    return c.list_data_sources(__opts__, profile=profile)


def installed(name, min_version=None, profile=None):
    """Verify the VRNI Platform at *name* is reachable and (optionally) at *min_version*.

    Returns a dict with keys ``installed`` (bool), ``version`` (str or
    None) and ``reason`` (str on failure, otherwise absent).

    :param str name: Logical name for the installation; also treated as
        the expected Platform FQDN for the returned diagnostics.
    :param str min_version: If given, require the fetched ``version``
        field to be lexicographically >= this value.
    :param str profile: Optional pillar profile name.

    CLI Example:

    .. code-block:: bash

        salt '*' vcf_vrni.installed vrni-prod
    """
    try:
        info = c.get_version(__opts__, profile=profile)
    except requests.RequestException as exc:
        return {"installed": False, "version": None, "reason": str(exc)}
    version = None
    if isinstance(info, dict):
        version = info.get("version") or info.get("api_version")
    if min_version and (version or "") < min_version:
        return {
            "installed": False,
            "version": version,
            "reason": f"version {version!r} < required {min_version!r}",
        }
    return {"installed": True, "version": version}


# ---------------------------------------------------------------------------
# Deploy surface: Platform + N Collectors
# ---------------------------------------------------------------------------


def _deploy_backend(name):
    name = (name or "pyvmomi").lower()
    if name == "pyvmomi":
        return ovf_deploy.deploy_ova
    if name == "ovftool":
        return ovftool_deploy.deploy_ova
    raise ValueError(f"unsupported vrni deployment_backend={name!r}")


def _push_ova(spec, *, role):
    """Push a single VRNI OVA (Platform or Collector) via the configured backend.

    *role* is either ``"platform"`` or ``"collector"``; used only for
    logging + returned metadata so the caller can distinguish which
    OVA-deploy result belongs to what.
    """
    backend = _deploy_backend(spec.get("deployment_backend"))
    kwargs = {
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
    if (spec.get("deployment_backend") or "pyvmomi").lower() == "ovftool":
        kwargs["ovftool_path"] = spec.get("ovftool_path") or "ovftool"
        kwargs["extra_args"] = spec.get("ovftool_extra_args")
    log.info("vcf_vrni._push_ova: deploying %s OVA %r", role, spec.get("vm_name"))
    result = backend(**kwargs)
    if isinstance(result, dict):
        result.setdefault("role", role)
    return result


def deploy_platform(spec, profile=None):
    """Deploy the VRNI Platform OVA and run the first-boot wizard.

    *spec* is a dict with at minimum::

        {
          "platform": {                 # OVA-push params (see _push_ova)
            "ova_url": "…-platform.ova",
            "target_host": "esx-1", "target_user": "root", "target_password": "…",
            "vm_name": "vrni-platform",
            "ovf_properties": {"role": "Platform", ...},
          },
          "wizard": {
            "admin_password": "…",
            "admin_email": "netops@example.com",
            "license_key": "AAAA-BBBB-CCCC-DDDD",
            "ntp_servers": ["ntp.example.com"],
            "web_proxy": {...},         # optional
            "telemetry_enabled": false, # optional
          },
          "wait_timeout": 3600,         # optional; default 3600
          "wait_poll_interval": 30,     # optional; default 30
        }

    Sequence:
      1. Push the Platform OVA to the target ESXi/vCenter.
      2. Wait for the setup wizard to become reachable (``wait_for_setup_ready``).
      3. POST the wizard payload (``complete_setup``).

    Returns ``{"deployed": True, "ova": <ova-result>, "wizard": <wizard-result>}``.
    """
    platform_spec = spec.get("platform") or {}
    wizard_spec = spec.get("wizard") or {}
    if not platform_spec.get("ova_url"):
        raise KeyError("vrni deploy spec missing 'platform.ova_url'")
    if not wizard_spec.get("admin_password"):
        raise KeyError("vrni deploy spec missing 'wizard.admin_password'")

    ova_result = _push_ova(platform_spec, role="platform")
    c.wait_for_setup_ready(
        __opts__,
        timeout=int(spec.get("wait_timeout", 3600)),
        poll_interval=int(spec.get("wait_poll_interval", 30)),
        profile=profile,
    )
    wizard_result = c.complete_setup(
        __opts__,
        admin_password=wizard_spec["admin_password"],
        admin_email=wizard_spec.get("admin_email"),
        license_key=wizard_spec.get("license_key"),
        ntp_servers=wizard_spec.get("ntp_servers", []),
        web_proxy=wizard_spec.get("web_proxy"),
        telemetry_enabled=wizard_spec.get("telemetry_enabled", False),
        accept_eula=wizard_spec.get("accept_eula", True),
        profile=profile,
    )
    return {"deployed": True, "ova": ova_result, "wizard": wizard_result}


def deploy_collector(collector_spec, shared_secret, profile=None):
    """Deploy a single Collector/Proxy OVA, pre-configured with *shared_secret*.

    Injects ``Proxy_Shared_Secret`` into ``ovf_properties`` (matching
    what the Collector OVA reads at first boot — source: LCM/e2e
    ``NIDeployCloudProxyAPITask``). If the caller already supplied a
    ``Proxy_Shared_Secret`` in ``ovf_properties`` it is honored (allows
    manual override for lab work).
    """
    if not collector_spec.get("ova_url"):
        raise KeyError("vrni collector spec missing 'ova_url'")
    if not shared_secret:
        raise ValueError("shared_secret required to deploy a Collector")

    props = dict(collector_spec.get("ovf_properties") or {})
    props.setdefault("Proxy_Shared_Secret", shared_secret)
    merged = dict(collector_spec)
    merged["ovf_properties"] = props
    return _push_ova(merged, role="collector")


def deploy(spec, profile=None):
    """Full VRNI deploy: Platform + N Collectors.

    Returns::

        {
          "platform": {…deploy_platform result…},
          "shared_secret": "<opaque>",
          "collectors": [{…each _push_ova result…}, …],
        }

    See :func:`deploy_platform` for the platform-side spec shape;
    collectors live under ``spec['collectors']`` and each takes the same
    OVA-push shape as ``platform``.
    """
    platform_result = deploy_platform(spec, profile=profile)
    collectors_spec = spec.get("collectors") or []
    collector_results = []
    shared_secret = None
    if collectors_spec:
        shared_secret = c.get_shared_secret(__opts__, profile=profile)
        for collector in collectors_spec:
            collector_results.append(deploy_collector(collector, shared_secret, profile=profile))
    return {
        "platform": platform_result,
        "shared_secret": shared_secret,
        "collectors": collector_results,
    }


# Convenience re-export so tests + callers don't need to reach into the
# installer_appliance module directly for TCP probing when scripting
# post-deploy verification.
is_reachable = ia.is_appliance_reachable
