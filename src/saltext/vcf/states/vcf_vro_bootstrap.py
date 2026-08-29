"""State module: idempotent bootstrap-remediation for a freshly-deployed vRO 9.0.2 appliance.

Runs the four appliance-side remediations documented in
:mod:`saltext.vcf.modules.vcf_vro_bootstrap` (``/etc/hosts`` fix, skip
non-idempotent firstboot script, re-bootstrap kubelet, install envoy
NodePort DNAT) and verifies ``GET /vco/api/about`` returns 200.

Each remediation is a no-op if the fix is already in place, so the
state converges to a green result on repeated runs.

Usage::

    vro-bootstrap:
      vcf_vro_bootstrap.remediate:
        - name: vro
        - host: <vro-ip>            # optional; falls back to pillar
        - root_password: <root-pw>  # optional; falls back to pillar

Pillar shape (used when args are omitted)::

    saltext.vcf:
      vro:
        host: <vro-ip>
        root_password: <root-pw>

In ``test=True`` mode the state describes the plan without touching the
appliance.
"""

import logging

log = logging.getLogger(__name__)

__virtualname__ = "vcf_vro_bootstrap"


def __virtual__():
    return __virtualname__


def _ret(name):
    return {"name": name, "changes": {}, "result": True, "comment": ""}


def remediate(name, host=None, root_password=None, verify_timeout=600, firstboot_timeout=1800):
    """Bring a freshly-deployed vRO 9.0.2 appliance to ``/vco/api/about`` = 200.

    :param str name: descriptive name (informational only).
    :param str host: appliance IP or hostname reachable over HTTPS/SSH;
        falls back to pillar ``saltext.vcf:vro:host``.
    :param str root_password: root password (the ``varoot-password`` set at
        OVA deploy); falls back to pillar
        ``saltext.vcf:vro:root_password``.
    :param int verify_timeout: seconds to wait for ``/vco/api/about``
        to return 200 after remediation completes.
    :param int firstboot_timeout: seconds to wait for
        ``run-bootstrap.service`` to finish deploying Prelude helm
        charts (~10 min once kubelet is healthy).

    ``changes`` on success is::

        {
            "steps": {step_name: {"changed": bool, "reason": str}},
            "verify": {"ok": True, "status_code": 200, "version": "9.0.2..."},
        }
    """
    ret = _ret(name)

    if __opts__.get("test"):
        ret["result"] = None
        ret["comment"] = (
            f"Would run vRO bootstrap remediation on {name!r} "
            "(fix /etc/hosts, skip 02-setup-kubernetes, re-bootstrap kubelet, "
            "re-run firstboot, install envoy DNAT, verify /vco/api/about)"
        )
        return ret

    result = __salt__["vcf_vro_bootstrap.remediate"](  # noqa: F821
        host=host,
        root_password=root_password,
        verify_timeout=int(verify_timeout),
        firstboot_timeout=int(firstboot_timeout),
    )

    steps = result.get("steps") or {}
    verify = result.get("verify") or {}
    fired = {n: s for n, s in steps.items() if s.get("changed")}

    if result.get("short_circuit"):
        ret["comment"] = (
            f"vRO {name!r} already serving /vco/api/about " f"(version={verify.get('version')!r})"
        )
        return ret

    if not result.get("ok"):
        ret["result"] = False
        ret["changes"] = {"steps": steps, "verify": verify}
        ret["comment"] = (
            f"vRO {name!r} remediation ran but /vco/api/about not 200 "
            f"(status={verify.get('status_code')!r}, "
            f"error={verify.get('error')!r})"
        )
        return ret

    ret["changes"] = {"steps": fired, "verify": verify}
    ret["comment"] = (
        f"vRO {name!r} remediated: {len(fired)}/{len(steps)} steps fired; "
        f"/vco/api/about returned 200 (version={verify.get('version')!r})"
    )
    return ret
