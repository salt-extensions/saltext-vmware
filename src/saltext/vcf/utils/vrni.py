"""
VCF Network Insight (VRNI, formerly vRealize Network Insight) REST helpers.

VRNI's public API is served by the *Platform VM* — the API tier of a
Platform + Collector deployment — at ``https://<platform>/api/ni/...``.
A bearer token is acquired with::

    POST /api/ni/auth/token
    {
        "username": "admin@local",
        "password": "…",
        "domain": {"domain_type": "LOCAL", "value": "local"}
    }
    -> {"token": "<opaque>", "expiry": <epoch-millis>}

Subsequent calls use ``Authorization: NetworkInsight <token>``. The token
is short-lived (default 5 minutes); this module caches it per
``(host, username)`` and transparently re-mints it on ``401``.

Config is read from Salt opts/pillar under ``saltext.vcf.vrni``::

    saltext.vcf:
      vrni:
        host: vrni-platform.example.com
        username: admin@local
        password: secret
        domain_type: LOCAL          # optional; default LOCAL
        domain_value: local         # optional; default "local"
        verify_ssl: false
        timeout: 60                 # optional; default DEFAULT_TIMEOUT

The pillar shape mirrors the other saltext-vcf products; a per-profile
override lives at ``saltext.vcf.profiles.<name>.vrni``.
"""

import logging

import requests
import urllib3

log = logging.getLogger(__name__)

# Tokens cached per (host, username). Value is the opaque bearer string.
_TOKEN_CACHE: dict[str, str] = {}

DEFAULT_TIMEOUT = 60
DEFAULT_DOMAIN_TYPE = "LOCAL"
DEFAULT_DOMAIN_VALUE = "local"


def get_config(opts, profile=None):
    """Extract VRNI connection config from Salt opts/pillar."""
    pillar = opts.get("pillar", {})
    root = pillar.get("saltext.vcf", {}) or opts.get("saltext.vcf", {})
    cfg = root.get("vrni", {})
    if profile:
        cfg = root.get("profiles", {}).get(profile, {}).get("vrni", cfg)
    return {
        "host": cfg.get("host") or cfg.get("hostname"),
        "username": cfg.get("username") or cfg.get("user"),
        "password": cfg.get("password"),
        "domain_type": cfg.get("domain_type", DEFAULT_DOMAIN_TYPE),
        "domain_value": cfg.get("domain_value", DEFAULT_DOMAIN_VALUE),
        "verify_ssl": cfg.get("verify_ssl", True),
        "timeout": cfg.get("timeout", DEFAULT_TIMEOUT),
    }


def _resolve_timeout(opts, profile, override):
    if override is not None:
        return override
    return get_config(opts, profile=profile)["timeout"]


def _acquire_token(cfg):
    """POST /api/ni/auth/token → bearer token string."""
    body = {
        "username": cfg["username"],
        "password": cfg["password"],
        "domain": {
            "domain_type": cfg["domain_type"],
            "value": cfg["domain_value"],
        },
    }
    resp = requests.post(
        f"https://{cfg['host']}/api/ni/auth/token",
        json=body,
        verify=cfg["verify_ssl"],
        timeout=cfg["timeout"],
    )
    resp.raise_for_status()
    payload = resp.json()
    token = payload.get("token")
    if not token:
        raise RuntimeError(f"VRNI /api/ni/auth/token response missing token: {payload!r}")
    return token


def get_token(opts, profile=None):
    """Acquire and cache a VRNI auth token. Returns the raw token string."""
    cfg = get_config(opts, profile=profile)
    if not cfg["verify_ssl"]:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    cache_key = f"{cfg['host']}:{cfg['username']}"
    if cache_key in _TOKEN_CACHE:
        return _TOKEN_CACHE[cache_key]

    token = _acquire_token(cfg)
    _TOKEN_CACHE[cache_key] = token
    return token


def invalidate_token(opts, profile=None):
    """Clear the cached VRNI token for this host+user."""
    cfg = get_config(opts, profile=profile)
    _TOKEN_CACHE.pop(f"{cfg['host']}:{cfg['username']}", None)


def _session(opts, profile=None):
    cfg = get_config(opts, profile=profile)
    verify = cfg["verify_ssl"]
    if not verify:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    token = get_token(opts, profile=profile)
    session = requests.Session()
    session.verify = verify
    session.headers.update(
        {
            "Authorization": f"NetworkInsight {token}",
            "Accept": "application/json",
        }
    )
    return session, cfg["host"]


def _request(method, opts, path, *, profile=None, timeout=None, **kwargs):
    """Underlying request; retries once on 401 by refreshing the bearer."""
    session, host = _session(opts, profile=profile)
    url = f"https://{host}{path}"
    eff_timeout = _resolve_timeout(opts, profile, timeout)
    resp = session.request(method, url, timeout=eff_timeout, **kwargs)
    if resp.status_code == 401:
        invalidate_token(opts, profile=profile)
        new_token = get_token(opts, profile=profile)
        session.headers["Authorization"] = f"NetworkInsight {new_token}"
        resp = session.request(method, url, timeout=eff_timeout, **kwargs)
    resp.raise_for_status()
    return resp


def api_get(opts, path, params=None, profile=None, timeout=None):
    resp = _request("GET", opts, path, params=params, profile=profile, timeout=timeout)
    if resp.content:
        return resp.json()
    return {}


def api_post(opts, path, body=None, params=None, profile=None, timeout=None):
    resp = _request("POST", opts, path, json=body, params=params, profile=profile, timeout=timeout)
    if resp.content:
        return resp.json()
    return {}


def api_put(opts, path, body=None, params=None, profile=None, timeout=None):
    resp = _request("PUT", opts, path, json=body, params=params, profile=profile, timeout=timeout)
    if resp.content:
        return resp.json()
    return {}


def api_delete(opts, path, params=None, profile=None, timeout=None):
    _request("DELETE", opts, path, params=params, profile=profile, timeout=timeout)
    return {}
