"""Tests for utils.vrli — token cache, refresh, 401 retry."""

import responses

from saltext.vcf.utils import vrli


def test_get_config_reads_pillar(opts):
    cfg = vrli.get_config(opts)
    assert cfg["host"] == "vrli.test"
    assert cfg["port"] == 9543
    assert cfg["username"] == "admin"
    assert cfg["provider"] == "Local"
    assert cfg["verify_ssl"] is False


def test_get_config_default_port_when_missing(opts):
    del opts["pillar"]["saltext.vcf"]["vrli"]["port"]
    assert vrli.get_config(opts)["port"] == 9543


def test_get_ssh_config_defaults_host_to_rest_host(opts):
    del opts["pillar"]["saltext.vcf"]["vrli"]["ssh"]["host"]
    assert vrli.get_ssh_config(opts)["host"] == "vrli.test"


def test_get_token_caches(opts, vrli_authed):
    tok = vrli.get_token(opts)
    tok2 = vrli.get_token(opts)
    assert tok == tok2 == "vrli-tok-abc"
    # only one auth call issued despite two get_token()s
    assert sum(1 for c in vrli_authed.calls if c.request.url.endswith("/sessions")) == 1


def test_invalidate_token_forces_reauth(opts, vrli_authed):
    vrli.get_token(opts)
    vrli.invalidate_token(opts)
    # Add a second auth response so the second call succeeds.
    vrli_authed.add(
        responses.POST,
        "https://vrli.test:9543/api/v2/sessions",
        json={"userId": "u1", "sessionId": "vrli-tok-xyz", "ttl": 1800},
        status=200,
    )
    assert vrli.get_token(opts) == "vrli-tok-xyz"


def test_api_get_adds_bearer_header(opts, vrli_authed):
    vrli_authed.add(
        responses.GET,
        "https://vrli.test:9543/api/v2/version",
        json={"version": "9.0.2.0"},
        status=200,
    )
    assert vrli.api_get(opts, "/api/v2/version") == {"version": "9.0.2.0"}
    ver_call = [c for c in vrli_authed.calls if c.request.url.endswith("/version")][-1]
    assert ver_call.request.headers["Authorization"] == "Bearer vrli-tok-abc"


def test_401_triggers_reauth_and_retry(opts, vrli_authed):
    # First call 401 with "Session expired" marker → invalidate + re-auth
    # + retry succeeds. Non-"Session expired" 401s surface unchanged; see
    # tests/unit/clients/test_vrli_master.py for that coverage.
    vrli_authed.add(
        responses.GET,
        "https://vrli.test:9543/api/v2/ad",
        json={"errorMessage": "Session expired"},
        status=401,
    )
    vrli_authed.add(
        responses.POST,
        "https://vrli.test:9543/api/v2/sessions",
        json={"userId": "u1", "sessionId": "vrli-tok-2", "ttl": 1800},
        status=200,
    )
    vrli_authed.add(
        responses.GET,
        "https://vrli.test:9543/api/v2/ad",
        json={"enableAD": False},
        status=200,
    )
    assert vrli.api_get(opts, "/api/v2/ad") == {"enableAD": False}


def test_api_post_puts_and_delete_wrappers(opts, vrli_authed):
    vrli_authed.add(
        responses.POST,
        "https://vrli.test:9543/api/v2/ad",
        json={},
        status=200,
    )
    vrli_authed.add(
        responses.PUT,
        "https://vrli.test:9543/api/v2/thing",
        json={"ok": True},
        status=200,
    )
    vrli_authed.add(
        responses.PATCH,
        "https://vrli.test:9543/api/v2/thing",
        json={"ok": True},
        status=200,
    )
    vrli_authed.add(
        responses.DELETE,
        "https://vrli.test:9543/api/v2/thing",
        status=204,
    )
    assert vrli.api_post(opts, "/api/v2/ad", body={"enableAD": False}) == {}
    assert vrli.api_put(opts, "/api/v2/thing", body={"x": 1}) == {"ok": True}
    assert vrli.api_patch(opts, "/api/v2/thing", body={"x": 1}) == {"ok": True}
    assert vrli.api_delete(opts, "/api/v2/thing") == {}
