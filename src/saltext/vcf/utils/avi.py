"""
AVI Controller (NSX Advanced Load Balancer) REST connection helpers.

The AVI Controller exposes a REST API at ``https://<controller>/api/``. Unlike
VCF Operations (bearer token) and SDDC Manager (JWT), AVI uses a *cookie*
session model: ``POST /login`` with ``{"username", "password"}`` sets a
``sessionid`` (or ``avi-sessionid``) cookie and a ``csrftoken`` cookie. Every
subsequent write request must echo the CSRF token in the ``X-CSRFToken``
header alongside a ``Referer`` header that matches the controller URL.

Config is read from Salt opts/pillar under ``saltext.vcf.avi``::

    saltext.vcf:
      avi:
        host: alb.example.test
        username: admin
        password: secret
        tenant: admin              # optional; default "admin"
        api_version: "22.1.3"      # optional; sent as X-Avi-Version
        verify_ssl: false
"""

import logging

import requests
import urllib3

log = logging.getLogger(__name__)

_SESSION_CACHE: dict[str, requests.Session] = {}

_DEFAULT_API_VERSION = "22.1.3"
_DEFAULT_TENANT = "admin"


def get_config(opts, profile=None):
    """Extract AVI Controller connection config from opts/pillar."""
    pillar = opts.get("pillar", {})
    root = pillar.get("saltext.vcf", {}) or opts.get("saltext.vcf", {})
    cfg = root.get("avi", {})
    if profile:
        cfg = root.get("profiles", {}).get(profile, {}).get("avi", cfg)
    return {
        "host": cfg.get("host") or cfg.get("hostname"),
        "username": cfg.get("username") or cfg.get("user"),
        "password": cfg.get("password"),
        "tenant": cfg.get("tenant", _DEFAULT_TENANT),
        "api_version": cfg.get("api_version", _DEFAULT_API_VERSION),
        "verify_ssl": cfg.get("verify_ssl", True),
    }


def invalidate_session(opts, profile=None):
    """Drop the cached AVI session (forces re-login on next call)."""
    cfg = get_config(opts, profile=profile)
    _SESSION_CACHE.pop(f"{cfg['host']}:{cfg['username']}", None)


def _login(opts, profile=None):
    """POST /login and return an authed :class:`requests.Session`."""
    cfg = get_config(opts, profile=profile)
    host = cfg["host"]
    username = cfg["username"]
    verify = cfg["verify_ssl"]
    if not verify:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    session = requests.Session()
    session.verify = verify
    resp = session.post(
        f"https://{host}/login",
        json={"username": username, "password": cfg["password"]},
        headers={
            "Content-Type": "application/json",
            "X-Avi-Version": cfg["api_version"],
            "Referer": f"https://{host}/",
        },
        timeout=30,
    )
    resp.raise_for_status()

    csrf = session.cookies.get("csrftoken")
    session.headers.update(
        {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Avi-Version": cfg["api_version"],
            "X-Avi-Tenant": cfg["tenant"],
            "Referer": f"https://{host}/",
        }
    )
    if csrf:
        session.headers["X-CSRFToken"] = csrf
    return session


def _session(opts, profile=None):
    """Return ``(session, host)``. Cached per host/user."""
    cfg = get_config(opts, profile=profile)
    key = f"{cfg['host']}:{cfg['username']}"
    session = _SESSION_CACHE.get(key)
    if session is None:
        session = _login(opts, profile=profile)
        _SESSION_CACHE[key] = session
    return session, cfg["host"]


def api_get(opts, path, params=None, profile=None):
    session, host = _session(opts, profile=profile)
    resp = session.get(f"https://{host}{path}", params=params, timeout=30)
    resp.raise_for_status()
    if resp.content:
        return resp.json()
    return {}


def api_post(opts, path, body=None, params=None, profile=None):
    session, host = _session(opts, profile=profile)
    resp = session.post(f"https://{host}{path}", json=body, params=params, timeout=60)
    resp.raise_for_status()
    if resp.content:
        return resp.json()
    return {}


def api_put(opts, path, body=None, profile=None):
    session, host = _session(opts, profile=profile)
    resp = session.put(f"https://{host}{path}", json=body, timeout=60)
    resp.raise_for_status()
    if resp.content:
        return resp.json()
    return {}


def api_delete(opts, path, params=None, profile=None):
    session, host = _session(opts, profile=profile)
    resp = session.delete(f"https://{host}{path}", params=params, timeout=30)
    resp.raise_for_status()
    return {}
