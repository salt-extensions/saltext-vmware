"""
HCX Manager REST connection helpers.

VMware HCX Manager exposes a REST API rooted at ``/hybridity/api/``. Session
auth is a two-step flow:

1. ``POST /hybridity/api/sessions`` with HTTP Basic auth returns the session
   token in the ``x-hm-authorization`` *response header*.
2. Subsequent requests send that token back in the ``x-hm-authorization``
   *request header* (no ``Bearer`` prefix).

Config is read from Salt opts/pillar under ``saltext.vcf.hcx``::

    saltext.vcf:
      hcx:
        host: hcx.example.test
        username: admin
        password: secret
        verify_ssl: false

Modeled on :mod:`saltext.vcf.utils.vcfops` but adapted for HCX's header-based
session scheme rather than a JSON-body token.
"""

import logging

import requests
import urllib3

log = logging.getLogger(__name__)

_TOKEN_CACHE: dict[str, str] = {}

_SESSION_PATH = "/hybridity/api/sessions"
_AUTH_HEADER = "x-hm-authorization"


def get_config(opts, profile=None):
    """Extract HCX Manager connection config from Salt opts/pillar."""
    pillar = opts.get("pillar", {})
    root = pillar.get("saltext.vcf", {}) or opts.get("saltext.vcf", {})
    cfg = root.get("hcx", {})
    if profile:
        cfg = root.get("profiles", {}).get(profile, {}).get("hcx", cfg)
    return {
        "host": cfg.get("host") or cfg.get("hostname"),
        "username": cfg.get("username") or cfg.get("user"),
        "password": cfg.get("password"),
        "verify_ssl": cfg.get("verify_ssl", True),
    }


def get_token(opts, profile=None):
    """Acquire and cache an HCX Manager session token.

    Returns the raw ``x-hm-authorization`` token string. Cached per
    ``(host, username)`` pair for the lifetime of the Salt loader session.
    """
    cfg = get_config(opts, profile=profile)
    host = cfg["host"]
    username = cfg["username"]
    verify = cfg["verify_ssl"]
    if not verify:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    cache_key = f"{host}:{username}"
    if cache_key in _TOKEN_CACHE:
        return _TOKEN_CACHE[cache_key]

    resp = requests.post(
        f"https://{host}{_SESSION_PATH}",
        auth=(username, cfg["password"]),
        verify=verify,
        timeout=30,
    )
    resp.raise_for_status()
    token = resp.headers.get(_AUTH_HEADER)
    if not token:
        # Some HCX builds also return the token in a JSON body; fall back.
        if resp.content:
            try:
                body = resp.json()
            except ValueError:
                body = None
            if isinstance(body, dict):
                token = body.get("hcspAuthorization") or body.get("token")
    if not token:
        raise RuntimeError(f"HCX Manager {host}: sessions POST returned no {_AUTH_HEADER} token")
    _TOKEN_CACHE[cache_key] = token
    return token


def invalidate_token(opts, profile=None):
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
            _AUTH_HEADER: token,
            "Accept": "application/json",
        }
    )
    return session, cfg["host"]


def api_get(opts, path, params=None, profile=None, timeout=30):
    session, host = _session(opts, profile=profile)
    resp = session.get(f"https://{host}{path}", params=params, timeout=timeout)
    resp.raise_for_status()
    if resp.content:
        return resp.json()
    return {}


def api_post(opts, path, body=None, params=None, profile=None, timeout=60):
    session, host = _session(opts, profile=profile)
    resp = session.post(f"https://{host}{path}", json=body, params=params, timeout=timeout)
    resp.raise_for_status()
    if resp.content:
        return resp.json()
    return {}


def api_delete(opts, path, params=None, profile=None, timeout=30):
    session, host = _session(opts, profile=profile)
    resp = session.delete(f"https://{host}{path}", params=params, timeout=timeout)
    resp.raise_for_status()
    return {}
