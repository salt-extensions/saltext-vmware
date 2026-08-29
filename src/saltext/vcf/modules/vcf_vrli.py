"""Execution module for VMware Log Insight / VCF Operations for Logs (vRLI).

Extends the read-only surface (``get_version`` / ``list_hosts`` / verify
``installed``) with a deploy-on-absence flow rebuilt against the *real*
vRLI 9.0.2 first-run wizard (see
``~/src/saltext-opsdev/VRLI-MANUAL-INSTALL-NOTES.md`` for the manual
transcript this implementation mirrors):

- :func:`deploy` — top-level single-node MVP: push the OVA, wait for the
  wizard to come up, drive the 3-call CSRF form flow
  (:func:`vrli_master.bootstrap_master`), optionally reset the ``admin``
  password over SSH, and verify with ``GET /api/v2/version``.

Cluster / Worker join is intentionally out of scope for this MVP; the
manual notes only cover a single-node master deploy.

The OVA push itself is delegated to the same backends used by the VCF
Installer flow (:mod:`saltext.vcf.clients.ovf_deploy` for pyVmomi or
:mod:`saltext.vcf.clients.ovftool_deploy` for ovftool). Backend selection
follows the ``deployment_backend`` key on the spec (default ``pyvmomi``);
this mirrors the installer-appliance convention so pillar shapes stay
consistent across the saltext.
"""

import logging

import requests

from saltext.vcf.clients import ovf_deploy
from saltext.vcf.clients import ovftool_deploy
from saltext.vcf.clients import vrli_master
from saltext.vcf.utils import vrli as vrli_utils

log = logging.getLogger(__name__)

__virtualname__ = "vcf_vrli"


def __virtual__():
    return __virtualname__


def get_version(profile=None):
    """Return the ``/api/v2/version`` payload from the configured vRLI master.

    CLI Example:

    .. code-block:: bash

        salt '*' vcf_vrli.get_version
    """
    return vrli_master.get_version(__opts__, profile=profile)


def list_hosts(profile=None):
    """Return the cluster node inventory (``/api/v2/hosts``).

    CLI Example:

    .. code-block:: bash

        salt '*' vcf_vrli.list_hosts
    """
    return vrli_master.list_hosts(__opts__, profile=profile)


def installed(name, version=None, profile=None):
    """Verify the vRLI master is reachable and (optionally) at the expected version.

    This is the exec-module counterpart to the state of the same name. It
    returns a dict shaped for programmatic use::

        {
          "installed": True|False,
          "version": "<vrli version>" | None,
          "release_name": "<release string>" | None,
          "reason": "<why False, if False>"
        }

    :param str name: Logical identity string (typically the FQDN of the master).
        Not used to talk to the API — connection details come from pillar.
    :param str version: Optional expected ``version`` string. If supplied and
        the running version differs, ``installed`` is returned as ``False``
        with ``reason`` describing the drift.
    :param str profile: Optional pillar profile.

    CLI Example:

    .. code-block:: bash

        salt '*' vcf_vrli.installed vrli-master.example.test
    """
    try:
        info = vrli_master.get_version(__opts__, profile=profile)
    except requests.exceptions.RequestException as exc:
        return {
            "installed": False,
            "version": None,
            "release_name": None,
            "reason": f"vRLI master unreachable: {exc}",
        }
    except RuntimeError as exc:
        return {
            "installed": False,
            "version": None,
            "release_name": None,
            "reason": str(exc),
        }

    current = (info or {}).get("version")
    release = (info or {}).get("releaseName")
    if version is not None and current != version:
        return {
            "installed": False,
            "version": current,
            "release_name": release,
            "reason": f"vRLI is at version {current!r}, expected {version!r}",
        }
    return {
        "installed": True,
        "version": current,
        "release_name": release,
        "reason": "",
    }


def invalidate_token(profile=None):
    """Drop any cached vRLI session token (forces re-login on next call)."""
    vrli_utils.invalidate_token(__opts__, profile=profile)
    return True


# ---------------------------------------------------------------------------
# Deploy path
# ---------------------------------------------------------------------------


def _select_backend(name):
    """Return the OVA-push callable for backend *name* (``pyvmomi``/``ovftool``)."""
    backend = (name or "pyvmomi").lower()
    if backend == "pyvmomi":
        return ovf_deploy.deploy_ova
    if backend == "ovftool":
        return ovftool_deploy.deploy_ova
    raise ValueError(f"unsupported vRLI deployment_backend={name!r}")


def _push_ova(spec):
    """Push the OVA described by *spec* using the selected backend.

    Mirrors the manual-install driver in
    ``~/src/saltext-opsdev/VRLI-MANUAL-INSTALL-NOTES.md``. *spec* keys
    used (all pillar-supplied):

    - ``ova_source`` (or legacy ``ova_url``) — local path or
      ``http(s)://`` URL to the OVA.
    - ``vm_name`` — VM name on the target.
    - ``target_host`` — ESXi/vCenter FQDN.
    - ``target_user`` / ``target_password`` — target creds.
    - ``target_port`` (default 443).
    - ``datastore``, ``network_map``, ``ovf_properties`` — pass-through.
    - ``disk_provisioning`` (default ``thin``), ``deployment_option``,
      ``power_on`` (default ``True``), ``verify_ssl`` (default ``False``),
      ``upload_timeout`` (default 3600), ``deployment_backend``
      (default ``pyvmomi``).
    - ``ovftool_path`` / ``ovftool_extra_args`` — used only when
      ``deployment_backend == 'ovftool'``.

    OVF property keys for vRLI must use the full
    ``<classId>.<instanceId>.<key>`` form (e.g.
    ``vami.VMware_vCenter_Log_Insight.ip0``); bare keys are silently
    dropped by the OVF importer. See notes.
    """
    push_backend = _select_backend(spec.get("deployment_backend"))
    ova_source = spec.get("ova_source") or spec.get("ova_url")
    if not ova_source:
        raise KeyError("vRLI deploy spec missing required 'ova_source' (or 'ova_url')")
    kwargs = {
        "ova_source": ova_source,
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
    existing = ovf_deploy.find_vm(
        target_host=kwargs["target_host"],
        target_user=kwargs["target_user"],
        target_password=kwargs["target_password"],
        vm_name=kwargs["vm_name"],
        target_port=kwargs["target_port"],
        verify_ssl=kwargs["verify_ssl"],
    )
    if existing is not None:
        log.info(
            "vRLI VM %r already exists on %s (moid=%s); skipping OVA push, "
            "will drive wizard only",
            kwargs["vm_name"],
            kwargs["target_host"],
            existing["vm_moid"],
        )
        return {**existing, "skipped_ova_push": True}
    return push_backend(**kwargs)


def deploy(spec, profile=None):
    """Single-node vRLI deploy: OVA push → wizard bootstrap → verify.

    Mirrors the working manual-install procedure in
    ``~/src/saltext-opsdev/VRLI-MANUAL-INSTALL-NOTES.md``. Steps:

    1. Push the OVA to *spec['target_host']* via
       :mod:`saltext.vcf.clients.ovf_deploy` (or ``ovftool``).
    2. Wait for the wizard root to become reachable via
       :func:`vrli_master.wait_for_setup_ready` (accepts ``<500``).
    3. Drive the 3-call CSRF form flow via
       :func:`vrli_master.bootstrap_master` using ``spec['admin_password']``
       (which on a fresh OVA is the ``rootpw`` OVF property).
    4. Optionally reset the ``admin`` password over SSH via
       :func:`vrli_master.reset_admin_password_via_ssh` when
       ``spec['reset_admin_password_via_ssh']`` is truthy.
    5. Verify with ``GET /api/v2/version``.

    *spec* keys beyond the ``_push_ova`` shape:

    - ``admin_password`` — password to authenticate the wizard's
      ``admin`` login with (matches the OVA ``rootpw`` OVF property
      supplied at deploy time — caller-provided, no default value).
    - ``ready_timeout`` (default 1800), ``ready_poll_interval`` (default 20).
    - ``bootstrap_timeout`` (default 600).
    - ``reset_admin_password_via_ssh`` (default ``False``). When true,
      requires:

      * ``ssh_host`` — the vRLI IP/FQDN to SSH into (falls back to
        pillar ``saltext.vcf:vrli:host``).
      * ``ssh_root_password`` — the OVA rootpw.
      * ``new_admin_password`` — the new UI admin password.
      * ``ssh_user`` (default ``root``), ``ssh_timeout`` (default 60).

    Returns::

        {
          "ova": <deploy_ova result>,
          "wizard": <bootstrap_master result>,
          "admin_password_reset": True | False,
          "version": <get_version result | None>
        }

    Worker/cluster form is intentionally out of scope for the MVP; see
    :func:`vrli_master.join_worker`.
    """
    if not spec:
        raise KeyError("vRLI deploy spec is empty")
    if "admin_password" not in spec:
        raise KeyError("vRLI deploy spec missing required 'admin_password'")

    ova_result = _push_ova(spec)

    vrli_master.wait_for_setup_ready(
        __opts__,
        timeout=int(spec.get("ready_timeout", 1800)),
        poll_interval=int(spec.get("ready_poll_interval", 20)),
        profile=profile,
    )

    wizard = vrli_master.bootstrap_master(
        __opts__,
        admin_password=spec["admin_password"],
        profile=profile,
        timeout=int(spec.get("bootstrap_timeout", 600)),
    )

    admin_password_reset = False
    if spec.get("reset_admin_password_via_ssh"):
        cfg = vrli_utils.get_config(__opts__, profile=profile)
        vrli_master.reset_admin_password_via_ssh(
            host=spec.get("ssh_host") or cfg["host"],
            root_password=spec["ssh_root_password"],
            new_admin_password=spec["new_admin_password"],
            ssh_user=spec.get("ssh_user", "root"),
            timeout=int(spec.get("ssh_timeout", 60)),
        )
        admin_password_reset = True
        # Newly-set admin password invalidates any cached session token.
        vrli_utils.invalidate_token(__opts__, profile=profile)

    try:
        version = vrli_master.get_version(__opts__, profile=profile)
    except (requests.RequestException, RuntimeError) as exc:
        log.warning("vRLI deploy: post-bootstrap /api/v2/version probe failed: %s", exc)
        version = None

    return {
        "ova": ova_result,
        "wizard": wizard,
        "admin_password_reset": admin_password_reset,
        "version": version,
    }
