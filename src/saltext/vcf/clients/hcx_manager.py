"""HCX Manager REST client.

Thin wrapper around :mod:`saltext.vcf.utils.hcx` for the endpoints the
``vcf_hcx`` module + state need to verify that an HCX Manager appliance is
installed and reachable, and to bootstrap a freshly-deployed appliance
through its first-boot activation + vCenter registration flow.

Two API surfaces used:

* Data-plane / :443:
  * ``GET /hybridity/api/endpointInfo`` -- unauthenticated liveness probe.
    HCX 9.x has no ``/api/activate`` (runs in EVALUATION_MODE by default);
    ``/about`` requires a session token which itself requires SSO, so this
    is the only reliable pre-registration readiness signal.
  * ``GET /hybridity/api/about`` -- appliance build/version, authenticated.
  * ``GET /hybridity/api/cloudConfigs`` -- registered cloud sites (list).

* Admin plane / :9443, ``x-hm-authorization`` header:
  * ``POST /api/admin/v1/sessions`` -- login, returns bearer token in the
    ``x-hm-authorization`` response header.
  * ``POST /api/admin/global/config/vcenter`` -- register a vCenter Server.
    First call returns 400 with the vCenter cert in ``data[0].certificate``
    (base64 DER); post that cert to ``/api/admin/certificates`` and retry.
  * ``POST /api/admin/global/config/lookupservice`` -- register external SSO.
  * ``GET /api/admin/global/config/applianceConfiguration`` -- returns bool;
    ``true`` once the wizard has landed a vCenter registration.
  * ``GET /api/admin/global/config/applianceInfo`` -- ``activationType`` etc.
  * ``GET /api/admin/licenses`` -- license state (``EVALUATION_MODE`` etc.).
"""

import base64
import logging
import time

import requests
import urllib3

from saltext.vcf.utils import hcx

log = logging.getLogger(__name__)

_ABOUT = "/hybridity/api/about"
_ENDPOINT_INFO = "/hybridity/api/endpointInfo"
_SITES = "/hybridity/api/cloudConfigs"
# Admin-plane endpoints (port 9443). Kept as constants for grep-ability.
_ADMIN_PORT = 9443
_ADMIN_LOGIN = "/api/admin/v1/sessions"
_ADMIN_VCENTER = "/api/admin/global/config/vcenter"
_ADMIN_LOOKUP = "/api/admin/global/config/lookupservice"
_ADMIN_CERTS = "/api/admin/certificates"
_ADMIN_APPLIANCE_CFG = "/api/admin/global/config/applianceConfiguration"
_ADMIN_APPLIANCE_INFO = "/api/admin/global/config/applianceInfo"
_ADMIN_LICENSES = "/api/admin/licenses"


def get_version(opts, profile=None):
    """Return the appliance ``/hybridity/api/about`` payload.

    A successful response is treated as the canonical proof that an HCX
    Manager is deployed and its API is up.
    """
    return hcx.api_get(opts, _ABOUT, profile=profile)


def list_sites(opts, profile=None):
    """Return the registered cloud sites payload (``/hybridity/api/cloudConfigs``).

    Used by the state to detect whether the Manager is already paired with a
    remote HCX endpoint.
    """
    return hcx.api_get(opts, _SITES, profile=profile)


def get_or_none(opts, name, profile=None):
    """Return the site dict whose ``endpointName`` matches *name*, or ``None``.

    HCX doesn't expose a per-name GET on ``cloudConfigs``; we list and filter.
    Raises the underlying ``requests.HTTPError`` for anything other than a
    404 on the list endpoint.
    """
    try:
        body = list_sites(opts, profile=profile)
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            return None
        raise
    items = _extract_sites(body)
    for entry in items:
        if not isinstance(entry, dict):
            continue
        if entry.get("endpointName") == name or entry.get("name") == name:
            return entry
    return None


def _extract_sites(body):
    """Normalize the HCX cloudConfigs body into a list of entries."""
    if isinstance(body, list):
        return body
    if not isinstance(body, dict):
        return []
    for key in ("items", "data", "cloudConfigs", "cloudConfig"):
        val = body.get(key)
        if isinstance(val, list):
            return val
    return []


def wait_for_setup_ready(opts, timeout=1800, poll_interval=15, profile=None):
    """Poll ``GET /hybridity/api/endpointInfo`` until it returns ``200``.

    HCX 9.x's ``/hybridity/api/sessions`` login endpoint requires SSO to
    already be configured (chicken-and-egg pre-registration), so ``get_version``
    is NOT a valid readiness probe on a fresh appliance. ``endpointInfo`` is
    unauthenticated and returns ``200`` as soon as the Apache front-end is up
    - the correct pre-registration liveness signal.

    Returns the decoded ``endpointInfo`` payload on success. Raises
    ``TimeoutError`` after *timeout* seconds.
    """
    cfg = hcx.get_config(opts, profile=profile)
    host = cfg["host"]
    verify = cfg.get("verify_ssl", True)
    if not verify:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    url = f"https://{host}{_ENDPOINT_INFO}"
    deadline = time.monotonic() + float(timeout)
    last_exc = None
    while True:
        try:
            resp = requests.get(url, timeout=float(poll_interval), verify=verify)
            if resp.status_code < 500:
                # HCX 9.x returns XML on this endpoint (com.vmware.vchs.hybridity.protocol.RestResponse),
                # not JSON. Treat any 2xx/3xx/4xx with a body as ready — don't parse.
                return {
                    "status": resp.status_code,
                    "content_type": resp.headers.get("Content-Type", ""),
                    "body_prefix": resp.text[:200],
                }
            last_exc = f"HTTP {resp.status_code}"
        except requests.RequestException as exc:
            last_exc = exc
            log.info("hcx_manager.wait_for_setup_ready: not ready yet: %s", exc)
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"HCX Manager /endpointInfo not ready within {timeout}s (last error: {last_exc})"
            )
        time.sleep(float(poll_interval))


def _admin_base(opts, profile=None):
    cfg = hcx.get_config(opts, profile=profile)
    return f"https://{cfg['host']}:{_ADMIN_PORT}", cfg.get("verify_ssl", True)


def _admin_login(opts, admin_password, profile=None, timeout=60):
    """POST admin creds to :9443/api/admin/v1/sessions. Return a ``requests.Session``.

    HCX 9.x admin API uses a bearer token supplied in the
    ``x-hm-authorization`` response header (not a cookie). We stash that
    header on the returned Session so downstream calls carry it.
    """
    base, verify = _admin_base(opts, profile=profile)
    if not verify:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    sess = requests.Session()
    sess.verify = verify
    resp = sess.post(
        f"{base}{_ADMIN_LOGIN}",
        json={"username": "admin", "password": admin_password},
        timeout=timeout,
    )
    resp.raise_for_status()
    token = resp.headers.get("x-hm-authorization")
    if not token:
        raise RuntimeError(
            f"HCX admin login did not return x-hm-authorization header (body={resp.text[:200]!r})"
        )
    sess.headers["x-hm-authorization"] = token
    return sess, base


def activate(opts, activation_key=None, profile=None, timeout=60, admin_password=None):
    """Verify HCX activation state; no-op on modern builds (evaluation mode is fine).

    HCX 9.x doesn't expose ``POST /hybridity/api/activate`` — the appliance
    runs in ``EVALUATION_MODE`` for 90 days by default. The presence of an
    *activation_key* argument is retained for API compatibility but ignored
    unless a specialised entitlement-server POST is needed (not implemented
    here — add if lab needs it).

    Returns a dict describing the observed activation/license state so the
    caller can log meaningfully.
    """
    if activation_key and str(activation_key).startswith("PLACEHOLDER"):
        log.warning(
            "hcx_manager.activate: activation_key is placeholder — ignoring "
            "(HCX 9.x runs in EVALUATION_MODE without a key)"
        )
    if not admin_password:
        log.info(
            "hcx_manager.activate: no admin_password supplied — skipping "
            "activation-state probe (assumed evaluation)"
        )
        return {"skipped": True, "reason": "no admin_password"}
    sess, base = _admin_login(opts, admin_password, profile=profile, timeout=timeout)
    try:
        info = sess.get(f"{base}{_ADMIN_APPLIANCE_INFO}", timeout=timeout).json()
        lic = sess.get(f"{base}{_ADMIN_LICENSES}", timeout=timeout).json()
        return {
            "activationType": (
                info.get("data", {}).get("items", [{}])[0].get("config", {}).get("activationType")
            ),
            "licenseStatus": lic.get("licenseStatus") or lic.get("data", {}).get("licenseStatus"),
            "already_activated": True,  # evaluation mode counts as usable
        }
    finally:
        sess.close()


def configure_vcenter(
    opts,
    vcenter_url,
    vcenter_username,
    vcenter_password,
    sso_url=None,
    profile=None,
    timeout=300,
    admin_password=None,
):
    """Register HCX Manager with a vCenter Server via the :9443 admin API.

    HCX 9.x moved vCenter registration off ``/hybridity/api/vcenters`` and
    onto ``POST /api/admin/global/config/vcenter`` (admin port 9443, wrapped
    body shape). The first call typically returns HTTP 400 with the vCenter
    cert in ``data[0].certificate`` (base64 DER); we trust it via
    ``/api/admin/certificates`` and retry once.

    *admin_password* is required (there's no per-user creds hidden in
    pillar — the HCX admin login is separate from the vCenter one). The
    caller (module) should read it from ``saltext.vcf:hcx:password`` or the
    spec's ``admin_password`` key.

    Returns a dict describing what was registered. For embedded PSC (SSO
    hosted on the vCenter itself), ``POST vcenter`` also auto-registers the
    LookupService, so a separate *sso_url* is only needed for external SSO.
    """
    if not admin_password:
        raise KeyError(
            "hcx_manager.configure_vcenter: admin_password is required "
            "(HCX 9.x admin API needs the admin bearer token; pass "
            "admin_password= from pillar 'saltext.vcf:hcx:password' or "
            "deploy_spec 'admin_password')"
        )
    sess, base = _admin_login(opts, admin_password, profile=profile, timeout=timeout)
    try:
        # Idempotency short-circuit: if applianceConfiguration is already true,
        # a POST to /vcenter would return HTTP 400 (HCX has no 409 semantics
        # for "already registered" here). Return the existing config so the
        # caller sees a stable "already done" result.
        already = sess.get(f"{base}{_ADMIN_APPLIANCE_CFG}", timeout=timeout)
        if already.status_code == 200 and already.json() is True:
            log.info("hcx_manager.configure_vcenter: applianceConfiguration=true already; skipping")
            existing = sess.get(f"{base}{_ADMIN_VCENTER}", timeout=timeout)
            return {
                "vcenter": True,
                "lookupservice": True if sso_url else None,
                "applianceConfiguration": True,
                "already_registered": True,
                "existing": (existing.json() if existing.status_code == 200 else None),
            }
        vc_body = {
            "data": {
                "items": [
                    {
                        "section": "vcenter",
                        "config": {
                            "url": vcenter_url,
                            "userName": vcenter_username,
                            "password": base64.b64encode(vcenter_password.encode("utf-8")).decode(
                                "ascii"
                            ),
                        },
                    }
                ]
            }
        }
        resp = sess.post(f"{base}{_ADMIN_VCENTER}", json=vc_body, timeout=timeout)
        if resp.status_code == 400:
            try:
                body = resp.json()
            except ValueError:
                body = None
            cert = None
            if isinstance(body, dict):
                data = body.get("data")
                if isinstance(data, list) and data:
                    cert = data[0].get("certificate") if isinstance(data[0], dict) else None
            if cert:
                log.info(
                    "hcx_manager.configure_vcenter: trusting vCenter cert (len=%d) then retrying",
                    len(cert),
                )
                trust = sess.post(
                    f"{base}{_ADMIN_CERTS}", json={"certificate": cert}, timeout=timeout
                )
                trust.raise_for_status()
                resp = sess.post(f"{base}{_ADMIN_VCENTER}", json=vc_body, timeout=timeout)
        resp.raise_for_status()
        result = {"vcenter": True, "lookupservice": None}
        if sso_url:
            sso_resp = sess.post(
                f"{base}{_ADMIN_LOOKUP}",
                json={
                    "data": {
                        "items": [
                            {
                                "section": "lookupservice",
                                "config": {"lookupServiceUrl": sso_url},
                            }
                        ]
                    }
                },
                timeout=timeout,
            )
            sso_resp.raise_for_status()
            result["lookupservice"] = True
        # Verify: applianceConfiguration should be true now
        cfg_resp = sess.get(f"{base}{_ADMIN_APPLIANCE_CFG}", timeout=timeout)
        cfg_resp.raise_for_status()
        result["applianceConfiguration"] = cfg_resp.json()
        return result
    finally:
        sess.close()
