"""AVI Controller (NSX Advanced Load Balancer) — read/probe + bootstrap API surface.

This client covers the endpoints the state module needs to verify an AVI
Controller is deployed and reachable, plus the minimum bootstrap flow used
by the deploy-on-absence path: OVA push (via
:mod:`saltext.vcf.clients.ovf_deploy` or
:mod:`saltext.vcf.clients.ovftool_deploy`), first-boot wizard reachability
poll, wizard submission, and optional HA cluster registration.

Deep configuration (cloud onboarding, SE-group tuning, VirtualService
creation, etc.) is still intentionally out of scope — those belong in later
per-resource clients modeled on the ``mops-config-modules`` AVI controller
code.
"""

import logging
import time

import requests
import urllib3

from saltext.vcf.clients import ovf_deploy
from saltext.vcf.clients import ovftool_deploy
from saltext.vcf.utils import avi

log = logging.getLogger(__name__)

# ``/api/initial-data`` is unauthenticated on some AVI builds but always
# reflects the controller's version banner. We reach it through the same
# session flow as the rest of the API so the caller gets a single, consistent
# error surface (auth failure vs. controller unreachable).
_INITIAL_DATA = "/api/initial-data"
_CLUSTER_VERSION = "/api/cluster/version"
_CLUSTER = "/api/cluster"
_CLOUD = "/api/cloud"
_TENANT = "/api/tenant"


def get_version(opts, profile=None):
    """Return the AVI Controller version banner.

    Hits ``GET /api/initial-data`` first (a stable AVI endpoint that includes
    ``version.Version`` and ``version.build``). Falls back to
    ``GET /api/cluster/version`` if the initial-data payload has no version
    key — some older AVI builds only expose the cluster path.
    """
    data = avi.api_get(opts, _INITIAL_DATA, profile=profile)
    version_block = None
    if isinstance(data, dict):
        version_block = data.get("version")
    if isinstance(version_block, dict) and version_block.get("Version"):
        return version_block
    # Fallback for older controllers.
    return avi.api_get(opts, _CLUSTER_VERSION, profile=profile)


def get_cluster(opts, profile=None):
    """Return the current AVI cluster document."""
    return avi.api_get(opts, _CLUSTER, profile=profile)


def list_clouds(opts, profile=None):
    """Return the ``results`` list from ``GET /api/cloud`` (empty list on none)."""
    resp = avi.api_get(opts, _CLOUD, profile=profile)
    if isinstance(resp, dict):
        return resp.get("results", []) or []
    return []


def list_tenants(opts, profile=None):
    """Return the ``results`` list from ``GET /api/tenant`` (empty list on none)."""
    resp = avi.api_get(opts, _TENANT, profile=profile)
    if isinstance(resp, dict):
        return resp.get("results", []) or []
    return []


def get_cloud(opts, name, profile=None):
    """Return the cloud whose ``name`` matches *name*.

    Raises ``KeyError`` if no matching cloud exists. Use :func:`get_or_none`
    for the idempotent variant.
    """
    for cloud in list_clouds(opts, profile=profile):
        if cloud.get("name") == name:
            return cloud
    raise KeyError(f"cloud {name!r} not found on AVI Controller")


def get_or_none(opts, name, profile=None):
    """Idempotent existence check — returns the cloud dict or ``None``.

    A 404 anywhere on the path (which for AVI usually means the controller
    itself is not up) also collapses to ``None`` so callers can treat "no
    cloud" and "controller unreachable" uniformly at the state layer. All
    other HTTP errors propagate.
    """
    try:
        return get_cloud(opts, name, profile=profile)
    except KeyError:
        return None
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            return None
        raise


def ping(opts, profile=None):
    """Return ``True`` iff ``get_version`` succeeds against the controller.

    Any :class:`requests.RequestException` (auth failure, TCP reset, TLS
    error, timeout, 5xx) is swallowed and turned into ``False``. Callers that
    need the underlying error should invoke :func:`get_version` directly.
    """
    try:
        get_version(opts, profile=profile)
        return True
    except requests.RequestException:
        return False


def wait_for_setup_ready(opts, timeout=1800, poll_interval=15, profile=None):
    """Poll ``GET /api/initial-data`` (unauth) until the wizard is reachable.

    Used immediately after an OVA boot: the appliance's HTTPS listener comes
    up long before the AVI process is ready to accept the first-boot POST,
    so we back off on 5xx / connection errors until we see a 2xx response
    or *timeout* elapses.

    :param opts: Salt opts dict (used to resolve host/verify_ssl).
    :param int timeout: Overall deadline in seconds.
    :param int poll_interval: Seconds between polls.
    :param str profile: Optional pillar profile name.
    :raises RuntimeError: if the wizard endpoint does not respond 2xx
        within *timeout* seconds.
    """
    cfg = avi.get_config(opts, profile=profile)
    host = cfg["host"]
    verify = cfg.get("verify_ssl", True)
    if not verify:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    url = f"https://{host}{_INITIAL_DATA}"
    deadline = time.monotonic() + float(timeout)
    last_status = None
    last_error = None
    while True:
        try:
            resp = requests.get(url, timeout=min(15, poll_interval), verify=verify)
            last_status = resp.status_code
            if 200 <= resp.status_code < 300:
                return
            log.info(
                "avi_controller.wait_for_setup_ready: %s returned HTTP %s",
                host,
                resp.status_code,
            )
        except requests.RequestException as exc:
            last_error = str(exc)
            log.info("avi_controller.wait_for_setup_ready: %s: %s", host, exc)
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"AVI Controller {host}: first-boot wizard not ready within "
                f"{timeout}s (last HTTP={last_status}, last error={last_error})"
            )
        time.sleep(float(poll_interval))


def bootstrap_wizard(
    opts,
    default_password,
    new_password,
    dns_servers,
    ntp_servers,
    backup_passphrase,
    dns_search_domain=None,
    profile=None,
    timeout=60,
):
    """Drive AVI Controller 22.x/32.x first-boot wizard as an 8-step sequence.

    AVI 22.x+ removed ``POST /api/initial-controller-setup`` — the UI walks
    separate endpoints. The default admin password is baked into the OVA at
    ``/opt/avi/bootstrap/default_password`` and is identical across every VM
    deployed from the same build (NOT per-VM random as older docs suggest).
    Callers supply that password as *default_password*.

    Sequence:

    1. ``POST /login`` (form-urlencoded) with *default_password* → session
       cookies (``avi-sessionid``, ``sessionid``, ``csrftoken``).
    2. ``PUT /api/useraccount`` — rotate admin password. This invalidates
       the current session, so we must re-login.
    3. ``POST /login`` with *new_password*.
    4. ``PATCH /api/systemconfiguration`` — replace ``dns_configuration``.
    5. ``PATCH /api/systemconfiguration`` — replace ``ntp_configuration``.
    6. ``GET  /api/backupconfiguration`` — get the default backup config URL.
    7. ``PATCH <backup URL>`` — set ``backup_passphrase`` (≥12 chars,
       upper+lower+digit+special; ``-`` is NOT a valid special).
    8. ``PATCH /api/systemconfiguration`` — flip
       ``welcome_workflow_complete: true``.

    Headers on all PATCH/PUT calls: ``Content-Type: application/json``,
    ``X-CSRFToken: <csrftoken cookie value>``, ``Referer: https://<host>/``.

    Returns a summary dict describing what was set.
    """
    cfg = avi.get_config(opts, profile=profile)
    host = cfg["host"]
    verify = cfg.get("verify_ssl", True)
    if not verify:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    base = f"https://{host}"

    def _login(sess, pw):
        r = sess.post(
            f"{base}/login",
            data={"username": "admin", "password": pw},
            headers={"Referer": base},
            timeout=timeout,
            verify=verify,
        )
        r.raise_for_status()

    def _patch(sess, path, body):
        h = {
            "Content-Type": "application/json",
            "X-CSRFToken": sess.cookies.get("csrftoken", ""),
            "Referer": base,
        }
        r = sess.patch(f"{base}{path}", json=body, headers=h, timeout=timeout, verify=verify)
        r.raise_for_status()
        return r

    def _get(sess, path):
        r = sess.get(f"{base}{path}", headers={"Referer": base}, timeout=timeout, verify=verify)
        r.raise_for_status()
        return r

    # 1. Login with default password.
    sess = requests.Session()
    sess.verify = verify
    _login(sess, default_password)

    # 2. Rotate admin password.
    h = {
        "Content-Type": "application/json",
        "X-CSRFToken": sess.cookies.get("csrftoken", ""),
        "Referer": base,
    }
    r = sess.put(
        f"{base}/api/useraccount",
        json={
            "username": "admin",
            "password": new_password,
            "email": "",
            "old_password": default_password,
        },
        headers=h,
        timeout=timeout,
        verify=verify,
    )
    r.raise_for_status()

    # 3. Re-login with new password (previous session invalidated).
    sess = requests.Session()
    sess.verify = verify
    _login(sess, new_password)

    # 4. DNS.
    dns_cfg = {
        "server_list": [{"type": "V4", "addr": ip} for ip in (dns_servers or [])],
    }
    if dns_search_domain:
        dns_cfg["search_domain"] = dns_search_domain
    _patch(sess, "/api/systemconfiguration", {"replace": {"dns_configuration": dns_cfg}})

    # 5. NTP.
    _patch(
        sess,
        "/api/systemconfiguration",
        {
            "replace": {
                "ntp_configuration": {
                    "ntp_servers": [
                        {"server": {"type": "DNS", "addr": s}} for s in (ntp_servers or [])
                    ]
                }
            }
        },
    )

    # 6-7. Backup passphrase.
    backup_url = _get(sess, "/api/backupconfiguration").json()["results"][0]["url"]
    # backup URL is absolute (https://host/api/backupconfiguration/<uuid>);
    # strip the base to get a request-relative path.
    backup_path = backup_url.replace(base, "")
    _patch(sess, backup_path, {"add": {"backup_passphrase": backup_passphrase}})

    # 8. Flip the welcome-workflow flag.
    _patch(sess, "/api/systemconfiguration", {"replace": {"welcome_workflow_complete": True}})

    # Wizard has rotated the admin password and set the SSO/backup passphrase;
    # any cached session token from the pillar-driven client is now stale.
    avi.invalidate_session(opts, profile=profile)
    return {
        "admin_password_rotated": True,
        "dns_servers": list(dns_servers or []),
        "ntp_servers": list(ntp_servers or []),
        "welcome_workflow_complete": True,
    }


# Backwards-compat alias: the old name pointed at ``/api/initial-controller-setup``
# which no longer exists on AVI 22.x+. Callers should migrate.
def initial_setup(*args, **kwargs):  # pragma: no cover
    """DEPRECATED: use :func:`bootstrap_wizard`. AVI 22.x+ 8-step flow."""
    raise NotImplementedError(
        "avi_controller.initial_setup was removed; use bootstrap_wizard() "
        "(AVI 22.x+ replaced POST /api/initial-controller-setup with an "
        "8-step wizard sequence — see docstring)."
    )


def configure_cluster(opts, nodes, cluster_ip=None, profile=None, timeout=300):
    """Register a 1- or 3-node AVI Controller HA cluster.

    Sends ``PUT /api/cluster`` with a ``nodes`` array of ``{name, ip}``
    entries (AVI expects ``ip`` as ``{type: V4, addr: <dotted>}``). Only
    invoked when the pillar deploy spec asks for HA — a solo controller
    does not need this call.

    :param opts: Salt opts dict.
    :param list nodes: List of ``{"name": str, "ip": str}`` dicts.
    :param str cluster_ip: Optional cluster (virtual) IP for 3-node HA.
    :param str profile: Optional pillar profile name.
    :param int timeout: HTTP timeout for the PUT.
    :raises RuntimeError: on non-2xx response from the cluster endpoint.
    :returns: parsed JSON response body.
    """
    body = {
        "nodes": [
            {
                "name": n["name"],
                "ip": {"type": "V4", "addr": n["ip"]},
            }
            for n in nodes
        ],
    }
    if cluster_ip:
        body["virtual_ip"] = {"type": "V4", "addr": cluster_ip}
    try:
        return avi.api_put(opts, _CLUSTER, body=body, profile=profile)
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "?"
        text = exc.response.text[:200] if exc.response is not None else ""
        raise RuntimeError(
            f"AVI Controller cluster configure returned HTTP {status}: {text}"
        ) from exc


def deploy_ova(spec):
    """Push the AVI Controller OVA per *spec* using the requested backend.

    *spec* keys mirror :func:`saltext.vcf.clients.installer_appliance.deploy_installer`:

    - ``ova_url`` (or ``controller_ova_url``): local path or ``http(s)://`` URL.
    - ``vm_name`` (or ``controller_vm_name``): VM name on the target host.
    - ``target_host``: FQDN/IP of the ESXi or vCenter to deploy against.
    - ``target_user`` / ``target_password``: creds for *target_host*.
    - ``deployment_backend``: ``pyvmomi`` (default) or ``ovftool``.
    - Optional: ``datastore``, ``network_map``, ``ovf_properties``,
      ``disk_provisioning``, ``deployment_option``, ``target_port``
      (default 443), ``power_on`` (default ``True``), ``verify_ssl``
      (default ``False``), ``upload_timeout``, ``ovftool_path``,
      ``ovftool_extra_args``.

    :raises ValueError: if *deployment_backend* is unrecognised.
    :returns: the dict produced by the selected deployment backend.
    """
    backend = str(spec.get("deployment_backend", "pyvmomi")).lower()
    ova_source = spec.get("ova_url") or spec.get("controller_ova_url")
    vm_name = spec.get("vm_name") or spec.get("controller_vm_name")
    if not ova_source:
        raise ValueError("AVI deploy spec missing 'ova_url' / 'controller_ova_url'")
    if not vm_name:
        raise ValueError("AVI deploy spec missing 'vm_name' / 'controller_vm_name'")
    kwargs = {
        "ova_source": ova_source,
        "target_host": spec["target_host"],
        "target_user": spec["target_user"],
        "target_password": spec["target_password"],
        "target_port": int(spec.get("target_port", 443)),
        "vm_name": vm_name,
        "datastore": spec.get("datastore"),
        "network_map": spec.get("network_map"),
        "ovf_properties": spec.get("ovf_properties"),
        "disk_provisioning": spec.get("disk_provisioning", "thin"),
        "deployment_option": spec.get("deployment_option"),
        "power_on": spec.get("power_on", True),
        "verify_ssl": spec.get("verify_ssl", False),
        "upload_timeout": spec.get("upload_timeout", 3600),
    }
    if backend == "pyvmomi":
        return ovf_deploy.deploy_ova(**kwargs)
    if backend == "ovftool":
        kwargs["ovftool_path"] = spec.get("ovftool_path") or "ovftool"
        kwargs["extra_args"] = spec.get("ovftool_extra_args")
        return ovftool_deploy.deploy_ova(**kwargs)
    raise ValueError(f"unsupported AVI deployment_backend={backend!r}")
