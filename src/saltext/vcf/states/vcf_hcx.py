"""State module for HCX Manager installation.

The state is deploy-on-absence: if HCX Manager is already reachable it is a
no-op; otherwise the OVA is deployed, activated, and registered with
vCenter/SSO per the supplied deploy spec (either the ``deploy_spec``
kwarg or pillar ``saltext.vcf:hcx:deploy_spec``).

The deploy chain is:

1. OVA push via :mod:`saltext.vcf.clients.ovf_deploy` (pyVmomi) or
   :mod:`saltext.vcf.clients.ovftool_deploy` (subprocess).
2. ``clients.hcx_manager.wait_for_setup_ready`` -- poll ``/hybridity/api/about``
   until the appliance's REST API is up.
3. ``clients.hcx_manager.activate`` -- ``POST /hybridity/api/activate``
   with the Broadcom-issued subscription key.
4. ``clients.hcx_manager.configure_vcenter`` -- ``POST /hybridity/api/vcenters``
   (and optionally ``POST /hybridity/api/lookupservice`` when
   ``sso_url`` is supplied).
5. ``clients.hcx_manager.get_version`` -- verify final version.

For HCX attached to VCF, a follow-up SDDC Manager ``POST /v1/hcx-connect``
call is still required to register the manager with the domain; that lives
outside this state.
"""

import logging

import requests

from saltext.vcf.clients import hcx_manager as c

log = logging.getLogger(__name__)

__virtualname__ = "vcf_hcx"


def __virtual__():
    return __virtualname__


def _ret(name):
    return {"name": name, "changes": {}, "result": True, "comment": ""}


def _version(about):
    """Extract the version field from an HCX ``/about`` payload."""
    if not isinstance(about, dict):
        return None
    return about.get("buildVersion") or about.get("version")


def _resolve_deploy_spec(deploy_spec):
    if deploy_spec is not None:
        return deploy_spec
    pillar = __opts__.get("pillar", {}) or {}  # noqa: F821
    root = pillar.get("saltext.vcf", {}) or {}
    return (root.get("hcx", {}) or {}).get("deploy_spec")


def installed(name, version=None, profile=None, deploy_spec=None):
    """Ensure an HCX Manager is installed and reachable.

    If HCX Manager answers ``/hybridity/api/about``, the state is a no-op
    (optionally verifying the reported version matches *version*).

    If unreachable and a *deploy_spec* (or pillar
    ``saltext.vcf:hcx:deploy_spec``) is supplied, the OVA is pushed,
    activated, and registered with vCenter, then the version is verified.

    If unreachable and no deploy spec is supplied, the state fails with a
    clear message.

    :param str name: State ID; also the expected Manager FQDN (informational).
    :param str version: Optional required HCX Manager version.
    :param str profile: Optional pillar profile name.
    :param dict deploy_spec: Optional deploy spec (see
        :func:`saltext.vcf.modules.vcf_hcx.deploy`). Defaults to pillar
        ``saltext.vcf:hcx:deploy_spec``.
    """
    ret = _ret(name)

    # Fast path: reachable AND already vCenter-registered.
    # HCX 9.x's /hybridity/api/about needs SSO auth (via /hybridity/api/sessions),
    # which itself requires a vCenter/SSO to already be registered — chicken/egg
    # on a fresh appliance. Two-part probe: (1) unauth endpointInfo confirms
    # the Apache front-end is up; (2) admin-plane applianceConfiguration=true
    # confirms the wizard has landed a vCenter registration. If both true,
    # this state is a no-op.
    _admin_pw = (deploy_spec or {}).get("admin_password") or (
        __opts__.get("pillar", {})  # noqa: F821
        .get("saltext.vcf", {})
        .get("hcx", {})
        .get("password")
    )
    about = None
    reach_error = None
    try:
        c.wait_for_setup_ready(__opts__, timeout=5, poll_interval=5, profile=profile)  # noqa: F821
        if _admin_pw:
            _sess, _base = c._admin_login(  # pylint: disable=protected-access
                __opts__, _admin_pw, profile=profile, timeout=30  # noqa: F821
            )
            try:
                _cfg = _sess.get(
                    f"{_base}/api/admin/global/config/applianceConfiguration", timeout=30
                )
                if _cfg.status_code == 200 and _cfg.json() is True:
                    about = {"already_configured": True, "applianceConfiguration": True}
            finally:
                _sess.close()
    except (requests.RequestException, RuntimeError, TimeoutError) as reach_exc:
        reach_error = reach_exc

    if about is not None:
        observed = _version(about)
        if version and observed != version:
            if __opts__.get("test"):  # noqa: F821
                ret["result"] = None
                ret["comment"] = f"HCX Manager {name!r} at {observed!r}, would require {version!r}"
                return ret
            ret["result"] = False
            ret["comment"] = (
                f"HCX Manager {name!r} version drift: installed={observed!r}, "
                f"required={version!r} (state does not reinstall)"
            )
            ret["changes"] = {"observed_version": observed, "required_version": version}
            return ret
        if __opts__.get("test"):  # noqa: F821
            ret["comment"] = f"HCX Manager {name!r} reachable at version {observed!r}"
            return ret
        ret["comment"] = f"HCX Manager {name!r} already installed" + (
            f" at version {observed}" if observed else ""
        )
        return ret

    # Unreachable path.
    spec = _resolve_deploy_spec(deploy_spec)
    if not spec:
        ret["result"] = False
        ret["comment"] = (
            f"HCX Manager {name!r} not reachable and no deploy_spec configured "
            f"(pass deploy_spec= or set pillar saltext.vcf:hcx:deploy_spec). "
            f"Underlying error: {reach_error}"
        )
        return ret

    if __opts__.get("test"):  # noqa: F821
        ret["result"] = None
        ret["comment"] = (
            f"Would deploy HCX Manager from {spec.get('ova_url')!r} "
            f"to {spec.get('target_host')!r}"
        )
        ret["changes"] = {
            "plan": "deploy_hcx_manager",
            "ova_url": spec.get("ova_url"),
            "target_host": spec.get("target_host"),
        }
        return ret

    try:
        deploy_result = __salt__["vcf_hcx.deploy"](spec, profile=profile)  # noqa: F821
    except (
        requests.RequestException,
        RuntimeError,
        TimeoutError,
        LookupError,
        FileNotFoundError,
        KeyError,
        ValueError,
    ) as exc:
        ret["result"] = False
        ret["comment"] = f"HCX Manager {name!r} deploy failed: {exc}"
        return ret

    # Post-deploy verification — use the admin-plane applianceConfiguration
    # check (same reason as the fast-path: /hybridity/api/about needs SSO).
    try:
        c.wait_for_setup_ready(
            __opts__, timeout=60, poll_interval=10, profile=profile
        )  # noqa: F821
        if _admin_pw:
            _sess, _base = c._admin_login(  # pylint: disable=protected-access
                __opts__, _admin_pw, profile=profile, timeout=30  # noqa: F821
            )
            try:
                _cfg = _sess.get(
                    f"{_base}/api/admin/global/config/applianceConfiguration", timeout=30
                )
                about = {"applianceConfiguration": _cfg.json()}
            finally:
                _sess.close()
        else:
            about = {"applianceConfiguration": None}
    except (requests.RequestException, RuntimeError, TimeoutError) as exc:
        ret["result"] = False
        ret["comment"] = f"HCX Manager {name!r} deployed but post-deploy probe failed: {exc}"
        return ret

    observed = _version(about)
    if version and observed != version:
        ret["result"] = False
        ret["comment"] = (
            f"HCX Manager {name!r} deployed but version drift: "
            f"installed={observed!r}, required={version!r}"
        )
        ret["changes"] = {
            "deployed": observed,
            "observed_version": observed,
            "required_version": version,
        }
        return ret

    ret["changes"] = {
        "deployed": observed,
        "activate": deploy_result.get("activate"),
        "vcenter": deploy_result.get("vcenter"),
    }
    ret["comment"] = f"HCX Manager {name!r} deployed" + (
        f" at version {observed}" if observed else ""
    )
    return ret
