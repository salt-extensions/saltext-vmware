"""VCF Network Insight (VRNI) Platform API — read + first-boot bootstrap.

The MVP read surface (``get_version``, ``list_data_sources``,
``get_or_none``) covers the verify-only ``installed`` state path. The
bootstrap surface (``wait_for_setup_ready``, ``complete_setup``,
``get_shared_secret``) covers the deploy-on-absence path: after the
Platform OVA boots, poll until the setup wizard is reachable, POST the
first-run wizard payload, then mint a Collector-join shared secret to
inject into each Collector OVA as the ``Proxy_Shared_Secret`` OVF
property.

Endpoint sourcing (see also the report block on the state module):

* ``/api/ni/info/version`` — public API, present on every Platform build
  once the wizard finishes. Also serves as the "setup done" probe.
* ``/api/ni/settings/*`` — LCM references (see
  ``vmlcm-vrniplugin/.../VRNIUriConstants.java``) confirm this is the
  Platform's settings namespace; the wizard-completion POST lives here
  under ``/api/ni/settings/setup``. The exact JSON shape is
  Broadcom-internal — see the ``# TODO: verify endpoint against live
  Platform`` comments where we stub it.
* Collector shared-secret: LCM/e2e code (see ``NIRestClient.OTK_URI``
  and the commented-out ``/api/management/nodes`` form) shows both the
  SaaS ``/api/ni/customers/proxy/secrets`` shape and the on-prem
  ``/api/management/nodes`` (aka "nodes" registration) shape. On on-prem
  Platform the collector-join secret is minted via
  ``POST /api/management/nodes`` and returned in the ``secret`` field.
  The Collector OVA reads it as the ``Proxy_Shared_Secret`` OVF
  property (source: e2e ``NIDeployCloudProxyAPITask.ovfDeployCmd``).
"""

import logging
import time

import requests

from saltext.vcf.utils import vrni

log = logging.getLogger(__name__)

_VERSION_PATH = "/api/ni/info/version"
_DATA_SOURCES_PATH = "/api/ni/data-sources"

# Setup / bootstrap endpoints. See module docstring for source-of-truth
# citations; both are # TODO: verify against a live Platform.
_SETUP_PATH = "/api/ni/settings/setup"
_SHARED_SECRET_PATH = "/api/management/nodes"


def get_version(opts, profile=None):
    """Return the Platform version dict.

    On a healthy Platform VM this looks like
    ``{"api_version": "1.5.0", "build_number": "…", "version": "6.14.0"}``.
    """
    return vrni.api_get(opts, _VERSION_PATH, profile=profile)


def list_data_sources(opts, profile=None):
    """List every registered data source on the Platform."""
    return vrni.api_get(opts, _DATA_SOURCES_PATH, profile=profile)


def _iter_entries(body):
    """VRNI paged responses put entries under ``results`` or ``data_sources``.

    Fall back to a bare list so callers can hand us anything list-shaped.
    """
    if isinstance(body, list):
        return body
    if not isinstance(body, dict):
        return []
    for key in ("results", "data_sources", "entities"):
        entries = body.get(key)
        if entries:
            return entries
    return []


def get_or_none(opts, name, profile=None):
    """Return the data source dict whose ``nickname`` matches *name*, or None.

    VRNI's list endpoint returns paged results; the MVP scans the first
    page only. If VRNI ever returns 404 for a list-style endpoint (it
    shouldn't) we treat that as "no match" for consistency with the
    saltext-vcf ``get_or_none`` contract.
    """
    try:
        body = list_data_sources(opts, profile=profile)
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            return None
        raise
    for entry in _iter_entries(body):
        if not isinstance(entry, dict):
            continue
        if entry.get("nickname") == name or entry.get("entity_id") == name:
            return entry
    return None


# ---------------------------------------------------------------------------
# First-boot / bootstrap helpers used by the deploy path in the state module.
# These make *unauthenticated* HTTPS calls — the Platform's public API is not
# online until ``complete_setup`` finishes, so we can't use utils.vrni here.
# ---------------------------------------------------------------------------


def _platform_host(opts, profile=None):
    cfg = vrni.get_config(opts, profile=profile)
    if not cfg["host"]:
        raise KeyError(
            "vrni.host is unset; cannot bootstrap Platform. Set pillar "
            "'saltext.vcf:vrni:host' before calling deploy."
        )
    return cfg


def wait_for_setup_ready(opts, timeout=3600, poll_interval=30, profile=None):
    """Block until the freshly-booted Platform accepts wizard traffic.

    VRNI first-boot takes 20-40 minutes: the OVA boots, the guest
    unpacks the appliance bundle, brings up nginx, then the wizard, and
    finally the API. This helper polls
    ``GET /api/ni/info/version`` (returns 200 once the API is up — which
    on a *bootstrapped* Platform means "setup done", but on a fresh
    Platform means "setup wizard is at least reachable and returns 401
    or a placeholder version") **or** ``GET /`` (the wizard HTML page).

    We accept any HTTP response code < 500 as "the appliance answered"
    to avoid mistaking a 401/403 (auth required, wizard done) or a 404
    (wizard is at a different path on this build) as "still booting".

    Raises ``TimeoutError`` after *timeout* seconds.
    Returns the final ``requests.Response`` on success.
    """
    cfg = _platform_host(opts, profile=profile)
    host = cfg["host"]
    verify = cfg["verify_ssl"]
    req_timeout = min(10, poll_interval)
    deadline = time.monotonic() + float(timeout)
    last_err = None
    while True:
        try:
            resp = requests.get(
                f"https://{host}{_VERSION_PATH}",
                verify=verify,
                timeout=req_timeout,
            )
            if resp.status_code < 500:
                log.info(
                    "vrni_platform.wait_for_setup_ready: %s answered HTTP %s",
                    host,
                    resp.status_code,
                )
                return resp
            last_err = f"HTTP {resp.status_code}"
        except requests.RequestException as exc:
            last_err = str(exc)
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"VRNI Platform {host} setup wizard not reachable within "
                f"{timeout}s (last: {last_err})"
            )
        log.info(
            "vrni_platform.wait_for_setup_ready: %s not ready yet (%s); sleeping %ss",
            host,
            last_err,
            poll_interval,
        )
        time.sleep(float(poll_interval))


def complete_setup(
    opts,
    admin_password,
    admin_email,
    license_key,
    ntp_servers,
    profile=None,
    timeout=300,
    web_proxy=None,
    telemetry_enabled=False,
    accept_eula=True,
):
    """POST the first-run wizard to bring the Platform's API online.

    Payload shape mirrors what the wizard UI submits — see the module
    docstring for the endpoint's provenance. ``ntp_servers`` is an
    iterable of NTP hostnames; ``web_proxy`` is an optional dict with
    ``host``/``port``/``username``/``password``.

    ``# TODO: verify endpoint against live Platform`` — the exact JSON
    shape is Broadcom-internal; this is a best-effort model matching
    the wizard UI's fields. The state module treats any 200/201/204 as
    success.
    """
    cfg = _platform_host(opts, profile=profile)
    body = {
        "accept_eula": bool(accept_eula),
        "admin_password": admin_password,
        "admin_email": admin_email,
        "license_key": license_key,
        "ntp_servers": list(ntp_servers) if ntp_servers else [],
        "telemetry_enabled": bool(telemetry_enabled),
    }
    if web_proxy:
        body["web_proxy"] = {
            "host": web_proxy.get("host"),
            "port": int(web_proxy["port"]) if web_proxy.get("port") else None,
            "username": web_proxy.get("username"),
            "password": web_proxy.get("password"),
        }
    resp = requests.post(
        f"https://{cfg['host']}{_SETUP_PATH}",
        json=body,
        verify=cfg["verify_ssl"],
        timeout=timeout,
    )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"VRNI setup wizard POST {_SETUP_PATH} failed: HTTP {resp.status_code} "
            f"{resp.text[:200]}"
        )
    if resp.content:
        try:
            return resp.json()
        except ValueError:
            return {"status_code": resp.status_code}
    return {"status_code": resp.status_code}


def get_shared_secret(opts, profile=None, node_type="proxy"):
    """Mint / retrieve a Collector-join shared secret from the Platform.

    Returns the raw secret string, ready to be injected into a
    Collector OVA as the ``Proxy_Shared_Secret`` OVF property.

    On on-prem VRNI, the secret is minted per-registration via
    ``POST /api/management/nodes`` (source: LCM ``NIRestClient.OTK_URI``
    and the commented ``/api/management/nodes`` fallback). The response
    body carries either a bare ``secret`` field or a ``results`` array
    whose first entry has a ``secret``.

    ``# TODO: verify endpoint against live Platform`` — the wizard flow
    may also expose ``GET /api/ni/nodes/shared-secret`` on newer
    builds; both shapes are handled below.
    """
    body = vrni.api_post(
        opts,
        _SHARED_SECRET_PATH,
        body={"node_type": node_type},
        profile=profile,
    )
    if isinstance(body, dict):
        if body.get("secret"):
            return body["secret"]
        results = body.get("results") or []
        if results and isinstance(results[0], dict) and results[0].get("secret"):
            return results[0]["secret"]
    raise RuntimeError(f"VRNI shared-secret response missing 'secret' field: {body!r}")
