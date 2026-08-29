"""State module for VCF Network Insight (VRNI).

``installed`` is a **deploy-on-absence** state:

* If the Platform at *name* already responds to ``GET /api/ni/info/version``
  (and, when supplied, its ``version`` is >= ``min_version``) the state
  is a no-op.
* Otherwise, if a ``deploy_spec`` is provided (inline or via pillar
  ``saltext.vcf:vrni:deploy_spec``), the state pushes the Platform OVA,
  runs the first-boot wizard, and deploys each Collector OVA joined via
  the Platform-minted shared secret.
* Otherwise the state fails with an actionable comment.

See :mod:`saltext.vcf.modules.vcf_vrni` for the pillar shape of a
``deploy_spec`` and :mod:`saltext.vcf.clients.vrni_platform` for the
Platform-side endpoint provenance.
"""

import logging

import requests

from saltext.vcf.clients import vrni_platform as c

log = logging.getLogger(__name__)

__virtualname__ = "vcf_vrni"


def __virtual__():
    return __virtualname__


def _ret(name):
    return {"name": name, "changes": {}, "result": True, "comment": ""}


def _resolve_deploy_spec(deploy_spec):
    if deploy_spec is not None:
        return deploy_spec
    pillar = __opts__.get("pillar", {}) or {}  # noqa: F821
    root = pillar.get("saltext.vcf", {}) or {}
    return (root.get("vrni") or {}).get("deploy_spec")


def _describe_plan(spec):
    """Return a human-readable plan sentence for test-mode."""
    platform = (spec.get("platform") or {}).get("vm_name") or "vrni-platform"
    collectors = spec.get("collectors") or []
    n = len(collectors)
    return f"would deploy Platform OVA {platform!r} + {n} Collector OVA(s)"


def installed(name, min_version=None, profile=None, deploy_spec=None):
    """Ensure the VRNI Platform at *name* is deployed and reachable.

    :param str name: Salt state ID (also used in diagnostics).
    :param str min_version: If set, require the running Platform's
        ``version`` field to be lexicographically >= this value.
    :param str profile: Optional pillar profile name.
    :param dict deploy_spec: Optional full deploy spec — Platform +
        wizard + Collectors. If omitted, resolved from pillar key
        ``saltext.vcf:vrni:deploy_spec``. If both are absent and the
        Platform is unreachable, the state fails with an actionable
        comment.
    """
    ret = _ret(name)

    # Reachability probe first — a no-op if the Platform is already up.
    try:
        info = c.get_version(__opts__, profile=profile)  # noqa: F821
    except requests.RequestException as reach_exc:
        info = None
        reach_err = reach_exc
    else:
        reach_err = None

    if info is not None:
        version = None
        if isinstance(info, dict):
            version = info.get("version") or info.get("api_version")
        if min_version and (version or "") < min_version:
            ret["result"] = False
            ret["comment"] = f"VRNI Platform version {version!r} is below required {min_version!r}"
            return ret
        if min_version:
            ret["comment"] = f"VRNI Platform is installed at version {version} (>= {min_version})"
        else:
            ret["comment"] = f"VRNI Platform is installed at version {version}"
        return ret

    # Platform is unreachable — deploy-on-absence path.
    spec = _resolve_deploy_spec(deploy_spec)
    if not spec:
        ret["result"] = False
        ret["comment"] = (
            f"VRNI Platform not reachable ({reach_err}) and no deploy_spec provided. "
            "Set 'deploy_spec' arg or pillar 'saltext.vcf:vrni:deploy_spec' to enable "
            "deploy-on-absence."
        )
        return ret

    if __opts__.get("test"):  # noqa: F821
        ret["result"] = None
        ret["comment"] = _describe_plan(spec)
        ret["changes"] = {
            "plan": "deploy_vrni",
            "platform_vm_name": (spec.get("platform") or {}).get("vm_name"),
            "collector_count": len(spec.get("collectors") or []),
        }
        return ret

    try:
        result = __salt__["vcf_vrni.deploy"](spec, profile=profile)  # noqa: F821
    except (RuntimeError, TimeoutError, KeyError, ValueError, LookupError) as exc:
        ret["result"] = False
        ret["comment"] = f"VRNI deploy failed: {exc}"
        return ret

    ret["changes"] = {
        "platform_deployed": bool(result.get("platform", {}).get("deployed")),
        "collectors_deployed": len(result.get("collectors") or []),
    }
    ret["comment"] = (
        f"VRNI deployed: Platform + {ret['changes']['collectors_deployed']} Collector(s)"
    )
    return ret
