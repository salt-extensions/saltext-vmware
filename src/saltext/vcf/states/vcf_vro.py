"""State module for VCF Orchestrator (VRO).

Idempotent deploy-on-absence:

1. If the appliance's ``/vco/api/about`` answers, the state is a no-op
   (optionally also asserting a required ``version``).
2. Otherwise, if a ``deploy_spec`` is available (either passed to the
   state or resolved from pillar
   ``saltext.vcf:vro:deploy_spec``), the state:

   * pushes the OVA (see :func:`saltext.vcf.modules.vcf_vro.deploy`),
   * waits for first-boot on ``/vco/api/about``,
   * joins vCenter SSO via ``/vco-controlcenter/api/server/sso``,
   * waits for the post-SSO appliance restart,
   * optionally installs a license via
     ``/vco-controlcenter/api/server/license``, and
   * re-verifies the appliance version.

3. If unreachable and no ``deploy_spec`` is available, the state
   fails with an actionable ``comment``.

In ``test=True`` mode the state describes the plan (OVA push + SSO
join + optional license) instead of performing it, with
``result=None``. If no ``deploy_spec`` is available in test mode, the
state falls back to describing the plain verify it would perform,
matching the earlier verify-only MVP behaviour.
"""

import logging

import requests

from saltext.vcf.clients import vro_orchestrator as c

log = logging.getLogger(__name__)

__virtualname__ = "vcf_vro"


def __virtual__():
    return __virtualname__


def _ret(name):
    return {"name": name, "changes": {}, "result": True, "comment": ""}


def _resolve_deploy_spec(opts, deploy_spec):
    """Return *deploy_spec* directly, else fall back to pillar."""
    if deploy_spec is not None:
        return deploy_spec
    pillar = opts.get("pillar", {}) or {}
    root = pillar.get("saltext.vcf", {}) or opts.get("saltext.vcf", {}) or {}
    vro_cfg = root.get("vro", {}) or {}
    return vro_cfg.get("deploy_spec")


def _version_matches(about, required):
    if required is None:
        return True
    return about.get("version") == required


def installed(name, version=None, profile=None, deploy_spec=None):
    """Ensure VCF Orchestrator is installed and reachable.

    - Reachable and (optionally) at the requested ``version``: no-op.
    - Unreachable + ``deploy_spec`` (arg or pillar
      ``saltext.vcf:vro:deploy_spec``) resolvable: deploy the OVA,
      join SSO, optionally install a license, verify.
    - Unreachable + no ``deploy_spec``: fail with an actionable
      ``comment``.

    :param str name: Descriptive VRO instance name (informational only,
        also used in comments).
    :param str version: Optional required version string.
    :param str profile: Pillar profile name for multi-target setups.
    :param dict deploy_spec: Optional in-line deploy spec (see
        :func:`saltext.vcf.modules.vcf_vro.deploy` for the shape).
    """
    ret = _ret(name)
    spec = _resolve_deploy_spec(__opts__, deploy_spec)

    if __opts__.get("test"):
        # In test mode we do NOT probe the appliance — describe what we
        # would do based on whether a deploy_spec is available.
        if spec:
            plan = [
                "deploy_vro_ova",
                "wait_for_setup_ready",
                "sso_join",
                "wait_for_setup_ready",
            ]
            if spec.get("license_key"):
                plan.append("install_license")
            plan.append("verify_get_version")
            ret["result"] = None
            ret["comment"] = (
                f"Would deploy VCF Orchestrator {name!r} to "
                f"{spec.get('installer_deploy_esxi', '<unset>')!r} then SSO-join"
                + (" with license" if spec.get("license_key") else "")
            )
            ret["changes"] = {"plan": plan}
            return ret
        ret["result"] = None
        ret["comment"] = f"Would verify VCF Orchestrator {name!r} is installed" + (
            f" at version {version}" if version else ""
        )
        return ret

    reachable = False
    about = None
    reach_error = None
    try:
        about = c.get_version(__opts__, profile=profile)
        reachable = True
    except requests.exceptions.RequestException as exc:
        reach_error = exc

    if reachable:
        if not _version_matches(about, version):
            reported = about.get("version")
            ret["result"] = False
            ret["comment"] = (
                f"VCF Orchestrator {name!r} version mismatch: "
                f"expected {version!r}, reported {reported!r}. "
                "Upgrade or redeploy VRO out of band and re-run."
            )
            return ret
        ret["comment"] = f"VCF Orchestrator {name!r} is installed" + (
            f" at version {about.get('version')}" if about.get("version") else " and reachable"
        )
        return ret

    if not spec:
        ret["result"] = False
        ret["comment"] = (
            f"VCF Orchestrator {name!r} is not reachable: {reach_error}. "
            "Deploy VRO out of band (see vcf_vro state module docstring) "
            "then re-run this state. To enable deploy-on-absence, set "
            "'deploy_spec' arg or pillar 'saltext.vcf:vro:deploy_spec'."
        )
        return ret

    try:
        deploy_result = __salt__["vcf_vro.deploy"](spec, profile=profile)  # noqa: F821
    except (
        TimeoutError,
        LookupError,
        KeyError,
        RuntimeError,
        FileNotFoundError,
        requests.exceptions.RequestException,
    ) as exc:
        ret["result"] = False
        ret["comment"] = f"VCF Orchestrator {name!r} deploy failed: {exc}"
        return ret

    deployed_version = deploy_result.get("version")
    if version is not None and deployed_version != version:
        ret["result"] = False
        ret["comment"] = (
            f"VCF Orchestrator {name!r} deployed but version mismatch: "
            f"expected {version!r}, reported {deployed_version!r}."
        )
        ret["changes"] = {"deployed": deployed_version}
        return ret

    ret["changes"] = {"deployed": deployed_version}
    ret["comment"] = (
        f"VCF Orchestrator {name!r} deployed"
        + (f" at version {deployed_version}" if deployed_version else "")
        + (" with license installed" if deploy_result.get("license_installed") else "")
    )
    return ret
