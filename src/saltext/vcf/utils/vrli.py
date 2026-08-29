"""
VCF Operations for Logs (vRLI / Log Insight rebrand) REST + SSH helpers.

VCF Operations for Logs 9.x is the rebrand of VMware vRealize Log
Insight. It exposes a token-authenticated REST API on TCP **9543**
(the ``:443`` port is the web UI vhost — it returns 403 for API
paths). A session token is acquired with::

    POST /api/v2/sessions
    { "provider": "Local", "username": "...", "password": "..." }
   -> { "userId": "...", "sessionId": "<token>", "ttl": 1800 }

Subsequent requests use ``Authorization: Bearer <sessionId>``.
Bare-token and ``X-Auth-Token`` are both rejected (401). The token
TTL is the same 1800 s (30 min) that appears in the appliance's
``web.xml`` ``<session-timeout>`` — this module refreshes at 80 % of
that TTL to avoid mid-request 401s.

A 401 mid-request is only retried when the body carries vRLI's
``"Session expired"`` marker; other 401s (e.g. Forbidden) surface
unchanged so genuine auth failures don't ping-pong through re-login.

Some appliance-local controls (session inactivity timeout, IPv4 DNS
config) have **no REST surface** on this build; they are edited on
the appliance itself via SSH. The connection info for that transport
is read from a nested ``ssh`` sub-block, mirroring the pattern used
by :mod:`saltext.vcf.utils.sddc` / :mod:`saltext.vcf.utils.ssh`.

Config is read from Salt opts/pillar under ``saltext.vcf.vrli``::

    saltext.vcf:
      vrli:
        host: logs.vcf.example.com
        port: 9543                    # optional; default 9543
        username: admin
        password: secret
        verify_ssl: false
        timeout: 30                   # optional
        ssh:                          # optional — required only for
          host: logs.vcf.example.com  # SSH-driven controls
          username: root
          password: secret
"""

import logging
import time

import requests
import urllib3

log = logging.getLogger(__name__)

DEFAULT_PORT = 9543
DEFAULT_TIMEOUT = 30
DEFAULT_PROVIDER = "Local"
# Proactive refresh at 80 % of the server-supplied ttl so a request
# started near expiry doesn't race with the invalidation.
_REFRESH_FRACTION = 0.80

# Cached per (host, username). Value shape:
#     {"token": str, "expires_at": float, "provider": str}
_TOKEN_CACHE: dict[str, dict] = {}


def get_config(opts, profile=None):
    """Extract vRLI connection config from Salt opts/pillar."""
    pillar = opts.get("pillar", {})
    root = pillar.get("saltext.vcf", {}) or opts.get("saltext.vcf", {})
    cfg = root.get("vrli", {})
    if profile:
        cfg = root.get("profiles", {}).get(profile, {}).get("vrli", cfg)
    return {
        "host": cfg.get("host") or cfg.get("hostname"),
        "port": int(cfg.get("port", DEFAULT_PORT)),
        "username": cfg.get("username") or cfg.get("user"),
        "password": cfg.get("password"),
        "provider": cfg.get("provider", DEFAULT_PROVIDER),
        "verify_ssl": cfg.get("verify_ssl", True),
        "timeout": cfg.get("timeout", DEFAULT_TIMEOUT),
        "ssh": cfg.get("ssh", {}) or {},
    }


def get_ssh_config(opts, profile=None):
    """Return the ``ssh`` sub-block, defaulting ``host`` to the REST host."""
    cfg = get_config(opts, profile=profile)
    ssh = dict(cfg.get("ssh") or {})
    ssh.setdefault("host", cfg["host"])
    return ssh


def _base_url(cfg):
    return f"https://{cfg['host']}:{cfg['port']}"


def _now():
    # Wrapped for testability.
    return time.monotonic()


def _cache_key(cfg):
    return f"{cfg['host']}:{cfg['port']}:{cfg['username']}"


def _acquire_token(cfg):
    """POST /api/v2/sessions → ``{"token", "expires_at", "provider"}``."""
    resp = requests.post(
        f"{_base_url(cfg)}/api/v2/sessions",
        json={
            "provider": cfg["provider"],
            "username": cfg["username"],
            "password": cfg["password"],
        },
        verify=cfg["verify_ssl"],
        timeout=cfg["timeout"],
    )
    resp.raise_for_status()
    body = resp.json() or {}
    session_id = body.get("sessionId")
    if not session_id:
        raise RuntimeError(f"vRLI POST /api/v2/sessions did not return sessionId: {body!r}")
    ttl = int(body.get("ttl", 1800))
    return {
        "token": session_id,
        "expires_at": _now() + ttl * _REFRESH_FRACTION,
        "provider": cfg["provider"],
    }


def get_token(opts, profile=None):
    """Return a cached (or freshly acquired) session token string.

    Refreshes proactively at 80 % of the server-supplied ttl so
    long-running Salt states don't race with token expiry mid-request.
    """
    cfg = get_config(opts, profile=profile)
    if not cfg["host"]:
        raise RuntimeError("saltext.vcf.vrli.host is not configured; cannot reach vRLI master")
    if not cfg["verify_ssl"]:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    key = _cache_key(cfg)
    cached = _TOKEN_CACHE.get(key)
    if cached and cached["expires_at"] > _now():
        return cached["token"]

    entry = _acquire_token(cfg)
    _TOKEN_CACHE[key] = entry
    return entry["token"]


def invalidate_token(opts, profile=None):
    """Drop the cached token for this ``(host, port, user)``."""
    cfg = get_config(opts, profile=profile)
    _TOKEN_CACHE.pop(_cache_key(cfg), None)


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
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
    )
    return session, cfg


def _looks_like_session_expired(resp):
    """True if a 401 response body carries vRLI's 'Session expired' marker.

    Genuine auth failures (401 Forbidden, bad creds) don't match — those
    should surface to the caller instead of triggering a re-login retry
    that will just fail again.
    """
    if resp is None or resp.status_code != 401:
        return False
    try:
        body = resp.json()
    except ValueError:
        return False
    if not isinstance(body, dict):
        return False
    msg = str(body.get("errorMessage") or body.get("message") or "")
    return "session expired" in msg.lower() or "session has expired" in msg.lower()


def _request(method, opts, path, *, profile=None, **kwargs):
    """Underlying request with 401-retry once on 'Session expired'."""
    session, cfg = _session(opts, profile=profile)
    url = f"{_base_url(cfg)}{path}"
    timeout = kwargs.pop("timeout", None) or cfg["timeout"]
    resp = session.request(method, url, timeout=timeout, **kwargs)
    if _looks_like_session_expired(resp):
        invalidate_token(opts, profile=profile)
        session, cfg = _session(opts, profile=profile)
        resp = session.request(method, url, timeout=timeout, **kwargs)
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


def api_patch(opts, path, body=None, params=None, profile=None, timeout=None):
    resp = _request("PATCH", opts, path, json=body, params=params, profile=profile, timeout=timeout)
    if resp.content:
        return resp.json()
    return {}


def api_delete(opts, path, params=None, profile=None, timeout=None):
    _request("DELETE", opts, path, params=params, profile=profile, timeout=timeout)
    return {}
