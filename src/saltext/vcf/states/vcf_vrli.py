"""State module: ensure a VMware Log Insight / VCF Operations for Logs master
appliance is deployed and reachable.

Behaviour matrix
----------------

+---------------------+---------------------+---------------------------------+
| master reachable?   | deploy_spec set?    | outcome                         |
+=====================+=====================+=================================+
| yes                 | (any)               | verify-only no-op (unchanged)   |
+---------------------+---------------------+---------------------------------+
| no                  | yes (test-mode)     | describe plan, result=None      |
+---------------------+---------------------+---------------------------------+
| no                  | yes (real mode)     | deploy single-node master,      |
|                     |                     | return ``changes``              |
+---------------------+---------------------+---------------------------------+
| no                  | no                  | fail with actionable hint       |
+---------------------+---------------------+---------------------------------+

Deploy path (single-node MVP)
-----------------------------

When *master unreachable + deploy_spec set + not test-mode*, the state
calls :func:`saltext.vcf.modules.vcf_vrli.deploy` which sequences (see
``~/src/saltext-opsdev/VRLI-MANUAL-INSTALL-NOTES.md`` for the manual
transcript this was derived from):

1. OVA push for the master (pyvmomi or ovftool backend).
2. Poll the wizard root until it becomes reachable (``<500``).
3. Drive the 3-call CSRF form flow:
   ``GET /csrf`` → ``POST /login`` (``authMethod=DEFAULT``) →
   ``POST /admin/startup`` (``_eventName=newDeployment``).
4. Optionally reset the ``admin`` password over SSH via
   ``/opt/vmware/bin/li-reset-admin-passwd.sh``.
5. Verify with ``GET /api/v2/version``.

Cluster form (Master + N Workers) is intentionally **out of scope** for
this MVP — the manual notes only cover a single-node master. Worker
join will need its own wizard endpoint reversed.

Deploy spec source of truth
---------------------------

The ``deploy_spec`` argument wins over pillar. If not passed inline,
the state reads it from pillar key
``saltext.vcf:vrli:deploy_spec``.

Deploy spec shape
-----------------

Top-level keys (all required unless noted). See the manual-install notes
for the concrete values used in a working install.

* OVA-push section (mirrors
  :func:`saltext.vcf.clients.ovf_deploy.deploy_ova`):

  ``ova_source``, ``vm_name``, ``target_host``, ``target_user``,
  ``target_password`` (+ optional ``target_port``, ``datastore``,
  ``network_map``, ``ovf_properties``, ``deployment_option``,
  ``disk_provisioning``, ``power_on``, ``verify_ssl``,
  ``upload_timeout``, ``deployment_backend``,
  ``ovftool_path``/``ovftool_extra_args``).

  OVF property keys for vRLI must use the fully-qualified
  ``<classId>.<instanceId>.<key>`` form (e.g.
  ``vami.VMware_vCenter_Log_Insight.ip0``); bare keys are silently
  dropped by the OVF importer.

  **Known quirk (see notes):** VAMI networking props (``ip0``,
  ``netmask0``, ``gateway``, ``DNS``, ``domain``, ``searchpath``)
  did *not* stick on the working manual install even with
  fully-qualified keys — the VM booted DHCP. ``rootpw`` did consume
  correctly. If your run needs a static IP, expect to locate the VM
  by its DHCP-assigned address post-boot (or extend this state with
  a ``vrli.static_ip_configured`` follow-up).

* Wizard section:

  ``admin_password`` (required) — the password to log the wizard in
  as ``admin`` (matches the OVA's ``rootpw`` OVF property; supply via
  the deploy spec — no hardcoded default).
  Optional ``ready_timeout``, ``ready_poll_interval``,
  ``bootstrap_timeout``.

* Optional SSH admin-password reset:

  Set ``reset_admin_password_via_ssh: true`` and supply
  ``ssh_root_password`` + ``new_admin_password`` (+ optional
  ``ssh_host``, ``ssh_user``, ``ssh_timeout``).
"""

import logging

import requests

from saltext.vcf.clients import vrli_master

log = logging.getLogger(__name__)

__virtualname__ = "vcf_vrli"


def __virtual__():
    return __virtualname__


def _ret(name):
    return {"name": name, "changes": {}, "result": True, "comment": ""}


_NO_SPEC_HINT = (
    "vRLI master is not reachable and no 'deploy_spec' was provided (arg "
    "or pillar 'saltext.vcf:vrli:deploy_spec'). Provide the deploy spec "
    "(single-node master OVA + wizard admin password) and rerun, or "
    "provision the master out of band and rerun to verify."
)


def _resolve_deploy_spec(deploy_spec):
    """Return an inline *deploy_spec* if given, else the pillar shape (or None)."""
    if deploy_spec is not None:
        return deploy_spec
    pillar = __opts__.get("pillar", {}) or {}  # noqa: F821
    root = pillar.get("saltext.vcf", {}) or __opts__.get("saltext.vcf", {}) or {}  # noqa: F821
    vrli_cfg = root.get("vrli", {}) or {}
    return vrli_cfg.get("deploy_spec")


def _describe_plan(name, deploy_spec):
    return (
        f"{name}: would deploy single-node vRLI master "
        f"(vm_name={deploy_spec.get('vm_name')!r}, "
        f"target={deploy_spec.get('target_host')!r})"
    )


def installed(name, version=None, deploy_spec=None, profile=None):
    """Ensure the pillar-configured vRLI master is reachable; deploy if absent.

    Idempotent:

    * If the master answers ``GET /api/v2/version``, the state is a no-op
      (``changes == {}``).
    * If it does not answer **and** *deploy_spec* (or pillar
      ``saltext.vcf:vrli:deploy_spec``) is set, the OVA is pushed and the
      first-run wizard is completed via the 3-call CSRF form flow
      (``GET /csrf`` → ``POST /login`` (``authMethod=DEFAULT``) →
      ``POST /admin/startup``). See module docstring for the deploy-spec
      shape and the manual-install notes reference.
    * If the master does not answer and no ``deploy_spec`` is available,
      the state fails with an actionable hint (no side effects).

    :param str name: Logical identity (typically the master FQDN).
    :param str version: Optional expected running version. Only checked
        when the master is already reachable — deploy always installs
        whatever the OVA in the spec produces.
    :param dict deploy_spec: Optional inline deploy spec. See module
        docstring for the top-level shape.
    :param str profile: Optional pillar profile for multi-target setups.
    """
    ret = _ret(name)

    # ----- Reachable → verify-only no-op ---------------------------------
    try:
        info = vrli_master.get_version(__opts__, profile=profile)  # noqa: F821
    except requests.exceptions.RequestException as unreachable_exc:
        info = None
        unreachable_reason = f"cannot reach vRLI master ({unreachable_exc})"
    except RuntimeError as cfg_exc:
        info = None
        unreachable_reason = str(cfg_exc)
    else:
        current = (info or {}).get("version")
        release = (info or {}).get("releaseName")
        if version is not None and current != version:
            ret["result"] = False
            ret["comment"] = (
                f"{name}: vRLI is installed at version {current!r} "
                f"(releaseName={release!r}) but expected {version!r}. "
                f"Upgrade/downgrade is out of scope for this state."
            )
            return ret
        ret["comment"] = (
            f"{name}: vRLI master is installed and reachable "
            f"(version={current!r}, releaseName={release!r})"
        )
        return ret

    # ----- Not reachable → decide on deploy ------------------------------
    spec = _resolve_deploy_spec(deploy_spec)

    if not spec:
        ret["result"] = False
        ret["comment"] = f"{name}: {unreachable_reason}. {_NO_SPEC_HINT}"
        return ret

    if __opts__.get("test"):  # noqa: F821
        ret["result"] = None
        ret["comment"] = _describe_plan(name, spec)
        return ret

    # ----- Real deploy ---------------------------------------------------
    try:
        # Call via __salt__ so the loader injects __opts__ into the module.
        # Direct-import (vrli_module.deploy) bypasses that injection.
        result = __salt__["vcf_vrli.deploy"](spec, profile=profile)
    except (KeyError, ValueError, RuntimeError, TimeoutError, LookupError) as exc:
        ret["result"] = False
        ret["comment"] = f"{name}: vRLI deploy failed: {exc}"
        return ret
    except requests.exceptions.RequestException as exc:
        ret["result"] = False
        ret["comment"] = f"{name}: vRLI deploy failed talking to master API: {exc}"
        return ret

    ret["changes"] = {
        "master_deployed": True,
        "admin_password_reset": bool(result.get("admin_password_reset")),
    }
    version = (result.get("version") or {}).get("version") if result else None
    version_note = f" (version={version!r})" if version else ""
    ret["comment"] = f"{name}: vRLI single-node master bootstrapped{version_note}"
    return ret
