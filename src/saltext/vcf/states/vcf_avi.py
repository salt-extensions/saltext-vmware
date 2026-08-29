"""State module for the AVI Controller (NSX Advanced Load Balancer).

Scope
-----

``installed`` is now a deploy-on-absence state:

1. Probe the Controller with :func:`saltext.vcf.clients.avi_controller.ping`.
   If it answers, the state is an idempotent no-op (``changes={}``).
2. If it does not answer AND a ``deploy_spec`` is supplied (either as a
   state kwarg or via pillar ``saltext.vcf:avi:deploy_spec``), push the
   OVA, wait for the first-boot wizard to come up, run
   ``/api/initial-controller-setup`` with the pillar-supplied admin
   credentials/DNS/NTP/backup passphrase, and — if
   ``deploy_spec['cluster_nodes']`` is present — register a HA cluster via
   ``PUT /api/cluster``. A final ``get_version`` confirms the Controller
   is up before the state returns success.
3. If it does not answer AND no ``deploy_spec`` was configured, the state
   fails with a clear message. This preserves the verify-only behaviour of
   the previous MVP for callers who don't want deploy-on-absence.

Deep AVI configuration (cloud onboarding, SE-group tuning, VirtualService
creation, NSX ALB provider registration) remains out of scope; per-resource
states will land alongside their config-modules counterparts.
"""

import logging

from saltext.vcf.clients import avi_controller as c

log = logging.getLogger(__name__)

__virtualname__ = "vcf_avi"


def __virtual__():
    return __virtualname__


def _ret(name):
    return {"name": name, "changes": {}, "result": True, "comment": ""}


def _resolve_deploy_spec(deploy_spec, profile=None):
    """Resolve the deploy spec from an explicit kwarg or pillar."""
    if deploy_spec is not None:
        return deploy_spec
    pillar = __opts__.get("pillar", {}) or {}  # noqa: F821
    root = pillar.get("saltext.vcf", {}) or {}
    avi_cfg = root.get("avi", {}) or {}
    if profile:
        avi_cfg = root.get("profiles", {}).get(profile, {}).get("avi", avi_cfg)
    return avi_cfg.get("deploy_spec")


def installed(name, deploy_spec=None, profile=None):
    """Ensure the AVI Controller identified by pillar ``saltext.vcf:avi`` is up.

    Idempotent: if the Controller already answers ``get_version``, returns
    ``result=True`` with ``changes={}``. Otherwise, if a *deploy_spec* is
    available (arg or pillar ``saltext.vcf:avi:deploy_spec``), pushes the
    OVA and runs the first-boot wizard. Without a *deploy_spec*, an absent
    Controller is a failure.

    :param str name: Salt state ID (used in the success/failure comment).
    :param dict deploy_spec: Optional OVA-deploy + wizard spec. See
        :func:`saltext.vcf.modules.vcf_avi.deploy` for the schema.
    :param str profile: Optional pillar profile name for multi-target setups.
    """
    ret = _ret(name)

    if c.ping(__opts__, profile=profile):  # noqa: F821
        ret["comment"] = f"AVI Controller {name!r} already reachable"
        return ret

    spec = _resolve_deploy_spec(deploy_spec, profile=profile)
    if not spec:
        ret["result"] = False
        ret["comment"] = (
            f"AVI Controller {name!r} not reachable and no deploy_spec "
            "configured (set state arg 'deploy_spec' or pillar "
            "'saltext.vcf:avi:deploy_spec')."
        )
        return ret

    ova_source = spec.get("ova_url") or spec.get("controller_ova_url")
    if __opts__.get("test"):  # noqa: F821
        ret["result"] = None
        ret["comment"] = (
            f"Would deploy AVI Controller from {ova_source!r} to "
            f"{spec.get('target_host')!r} and run first-boot wizard"
        )
        ret["changes"] = {"plan": "deploy_avi_controller", "ova": ova_source}
        return ret

    try:
        result = __salt__["vcf_avi.deploy"](spec, profile=profile)  # noqa: F821
    except (RuntimeError, TimeoutError, KeyError, ValueError, LookupError) as exc:
        ret["result"] = False
        ret["comment"] = f"AVI Controller deploy failed: {exc}"
        return ret

    version = result.get("version") or {}
    banner = version.get("Version") if isinstance(version, dict) else None
    ret["changes"] = {"deployed": banner or True}
    ret["comment"] = (
        f"AVI Controller {name!r} deployed (version={banner})"
        if banner
        else f"AVI Controller {name!r} deployed"
    )
    return ret
