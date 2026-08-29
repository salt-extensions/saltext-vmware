"""
VCF Orchestrator (VRO / Aria Orchestrator) REST connection helpers.

VCF Orchestrator 9.x ships as a standalone Photon-based virtual appliance
(the "Aria Orchestrator" / "VCF Orchestrator" VA), *not* as the vCenter-
embedded Orchestrator of the vSphere 6/7 era. The appliance exposes:

* a REST API rooted at ``https://<orchestrator>/vco/api/`` for workflow
  and inventory operations, and
* a Control Center at ``/vco-controlcenter`` for appliance administration.

The saltext-vcf VRO utility talks only to the REST surface. Two auth
modes are supported by the appliance:

* **HTTP Basic** — a local ``vcoadmin`` account (or SSO principal) can
  authenticate every request with ``Authorization: Basic ...``. Simplest;
  fine for verify-only or short-lived read paths.
* **SSO cookie** — ``POST /vco/api/login-sso`` issues an
  ``JSESSIONID`` / ``VMWARE_JSESSIONID`` cookie usable on subsequent
  requests.

For the verify-only MVP this module uses HTTP Basic; the cookie flow can
be layered on later without changing callers.

Config is read from Salt opts/pillar under ``saltext.vcf.vro``:

.. code-block:: yaml

    saltext.vcf:
      vro:
        host: vro.example.test          # required
        port: 443                       # optional; default 443
        username: vcoadmin@vsphere.local
        password: secret
        verify_ssl: false
        timeout: 30                     # optional; default DEFAULT_TIMEOUT

The ``vro:{host,username}`` pair keys the session cache; call
:func:`invalidate_session` to force a re-open (e.g. after credential
rotation).
"""

import logging

import requests
import urllib3

log = logging.getLogger(__name__)

_SESSION_CACHE: dict[str, requests.Session] = {}

DEFAULT_TIMEOUT = 30
DEFAULT_PORT = 443


def get_config(opts, profile=None):
    """Extract VCF Orchestrator connection config from Salt opts/pillar."""
    pillar = opts.get("pillar", {})
    root = pillar.get("saltext.vcf", {}) or opts.get("saltext.vcf", {})
    cfg = root.get("vro", {})
    if profile:
        cfg = root.get("profiles", {}).get(profile, {}).get("vro", cfg)
    return {
        "host": cfg.get("host") or cfg.get("hostname"),
        "port": int(cfg.get("port", DEFAULT_PORT)),
        "username": cfg.get("username") or cfg.get("user"),
        "password": cfg.get("password"),
        "verify_ssl": cfg.get("verify_ssl", True),
        "timeout": cfg.get("timeout", DEFAULT_TIMEOUT),
        # vRO 9.x envoy proxy routes on Host header, not IP. When the VM
        # DHCPs to a different IP than its configured hostname (very common
        # because VAMI static-IP props don't stick on this OVA), envoy
        # returns 404 unless the request sets Host to the appliance's
        # configured name. Pillar key: saltext.vcf:vro:sni_hostname.
        "sni_hostname": cfg.get("sni_hostname"),
    }


def _resolve_timeout(opts, profile, override):
    if override is not None:
        return override
    return get_config(opts, profile=profile)["timeout"]


def _base_url(cfg):
    """Return ``https://host[:port]`` — the port suffix is omitted when 443."""
    host = cfg["host"]
    port = cfg["port"]
    if port and port != 443:
        return f"https://{host}:{port}"
    return f"https://{host}"


def get_session(opts, profile=None):
    """Return ``(session, base_url)``.

    The session has HTTP Basic auth pre-installed against the configured
    VRO credentials. Cached per ``(host, username)``.
    """
    cfg = get_config(opts, profile=profile)
    verify = cfg["verify_ssl"]
    if not verify:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    cache_key = f"{cfg['host']}:{cfg['username']}"
    session = _SESSION_CACHE.get(cache_key)
    if session is None:
        session = requests.Session()
        session.verify = verify
        session.auth = (cfg["username"], cfg["password"])
        headers = {"Accept": "application/json"}
        # Force Host header when caller pinned sni_hostname (see get_config docstring).
        if cfg.get("sni_hostname"):
            headers["Host"] = cfg["sni_hostname"]
        session.headers.update(headers)
        _SESSION_CACHE[cache_key] = session
    return session, _base_url(cfg)


def invalidate_session(opts, profile=None):
    """Drop the cached VRO session, forcing a fresh one on next use."""
    cfg = get_config(opts, profile=profile)
    _SESSION_CACHE.pop(f"{cfg['host']}:{cfg['username']}", None)


def api_get(opts, path, params=None, profile=None, timeout=None):
    """GET ``<base>/vco/api...`` and return parsed JSON.

    Caller passes the full path (starting with a slash). Non-JSON bodies
    return ``{}``; HTTP errors raise ``requests.HTTPError``.
    """
    session, base = get_session(opts, profile=profile)
    resp = session.get(
        f"{base}{path}",
        params=params,
        timeout=_resolve_timeout(opts, profile, timeout),
    )
    resp.raise_for_status()
    if resp.content:
        try:
            return resp.json()
        except ValueError:
            return {}
    return {}


def api_post(opts, path, body=None, params=None, profile=None, timeout=None):
    """POST JSON *body* to VRO."""
    session, base = get_session(opts, profile=profile)
    resp = session.post(
        f"{base}{path}",
        json=body,
        params=params,
        timeout=_resolve_timeout(opts, profile, timeout),
    )
    resp.raise_for_status()
    if resp.content:
        try:
            return resp.json()
        except ValueError:
            return {}
    return {}
