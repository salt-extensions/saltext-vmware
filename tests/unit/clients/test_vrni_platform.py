"""Tests for clients.vrni_platform + utils.vrni.

The saltext-vcf conftest ``opts`` fixture doesn't declare a ``vrni``
pillar block, so each test layers one in via the ``vrni_opts`` fixture
below. The ``vrni_authed`` fixture pre-registers the ``/api/ni/auth/token``
POST that every request path funnels through.
"""

import pytest
import requests
import responses

from saltext.vcf.clients import vrni_platform as c
from saltext.vcf.utils import vrni

_VRNI_HOST = "vrni.test"
_TOKEN_URL = f"https://{_VRNI_HOST}/api/ni/auth/token"


@pytest.fixture
def vrni_opts(opts):
    """Layer a ``vrni`` pillar section onto the shared ``opts`` fixture."""
    opts["pillar"]["saltext.vcf"]["vrni"] = {
        "host": _VRNI_HOST,
        "username": "admin@local",
        "password": "secret",
        "verify_ssl": False,
    }
    return opts


@pytest.fixture(autouse=True)
def _clear_vrni_cache():
    vrni._TOKEN_CACHE.clear()
    yield
    vrni._TOKEN_CACHE.clear()


@pytest.fixture
def vrni_authed(mocked_responses):
    """Pre-register the VRNI auth token POST."""
    mocked_responses.add(
        responses.POST,
        _TOKEN_URL,
        json={"token": "vrni-tok-abc", "expiry": 1_800_000_000_000},
        status=200,
    )
    return mocked_responses


# ---------------------------------------------------------------------------
# utils.vrni auth
# ---------------------------------------------------------------------------


def test_get_token_posts_local_domain(vrni_opts, vrni_authed):
    tok = vrni.get_token(vrni_opts)
    assert tok == "vrni-tok-abc"
    req = vrni_authed.calls[-1].request
    assert req.url == _TOKEN_URL
    body = req.body.decode() if isinstance(req.body, bytes) else req.body
    assert '"username": "admin@local"' in body
    assert '"password": "secret"' in body
    assert '"domain_type": "LOCAL"' in body
    assert '"value": "local"' in body


def test_get_token_is_cached(vrni_opts, vrni_authed):
    vrni.get_token(vrni_opts)
    vrni.get_token(vrni_opts)
    # Only one POST to /auth/token despite two get_token calls.
    token_calls = [c_ for c_ in vrni_authed.calls if c_.request.url == _TOKEN_URL]
    assert len(token_calls) == 1


def test_get_token_raises_when_response_missing_token(vrni_opts, mocked_responses):
    mocked_responses.add(responses.POST, _TOKEN_URL, json={"expiry": 0}, status=200)
    with pytest.raises(RuntimeError, match="missing token"):
        vrni.get_token(vrni_opts)


def test_401_triggers_token_refresh_and_retry(vrni_opts, mocked_responses):
    # First auth
    mocked_responses.add(responses.POST, _TOKEN_URL, json={"token": "tok-1"}, status=200)
    # Version call: first 401, then 200 after refresh
    mocked_responses.add(
        responses.GET,
        f"https://{_VRNI_HOST}/api/ni/info/version",
        status=401,
    )
    mocked_responses.add(responses.POST, _TOKEN_URL, json={"token": "tok-2"}, status=200)
    mocked_responses.add(
        responses.GET,
        f"https://{_VRNI_HOST}/api/ni/info/version",
        json={"version": "6.14.0"},
        status=200,
    )
    body = c.get_version(vrni_opts)
    assert body == {"version": "6.14.0"}
    # Verify a re-auth happened (two POSTs to /auth/token).
    token_posts = [c_ for c_ in mocked_responses.calls if c_.request.url == _TOKEN_URL]
    assert len(token_posts) == 2


# ---------------------------------------------------------------------------
# clients.vrni_platform.get_version + list_data_sources
# ---------------------------------------------------------------------------


def test_get_version_returns_body_and_uses_bearer(vrni_opts, vrni_authed):
    vrni_authed.add(
        responses.GET,
        f"https://{_VRNI_HOST}/api/ni/info/version",
        json={"version": "6.14.0", "api_version": "1.5.0"},
        status=200,
    )
    result = c.get_version(vrni_opts)
    assert result == {"version": "6.14.0", "api_version": "1.5.0"}
    ver_call = [
        call for call in vrni_authed.calls if call.request.url.endswith("/api/ni/info/version")
    ][-1]
    assert ver_call.request.headers["Authorization"] == "NetworkInsight vrni-tok-abc"


def test_get_version_propagates_5xx(vrni_opts, vrni_authed):
    vrni_authed.add(
        responses.GET,
        f"https://{_VRNI_HOST}/api/ni/info/version",
        status=503,
    )
    with pytest.raises(requests.HTTPError):
        c.get_version(vrni_opts)


def test_list_data_sources_returns_body(vrni_opts, vrni_authed):
    vrni_authed.add(
        responses.GET,
        f"https://{_VRNI_HOST}/api/ni/data-sources",
        json={"results": [{"entity_id": "1", "nickname": "vc-prod"}]},
        status=200,
    )
    body = c.list_data_sources(vrni_opts)
    assert body["results"][0]["nickname"] == "vc-prod"


# ---------------------------------------------------------------------------
# get_or_none idempotence contract
# ---------------------------------------------------------------------------


def test_get_or_none_matches_nickname(vrni_opts, vrni_authed):
    vrni_authed.add(
        responses.GET,
        f"https://{_VRNI_HOST}/api/ni/data-sources",
        json={
            "results": [
                {"entity_id": "1", "nickname": "vc-lab"},
                {"entity_id": "2", "nickname": "vc-prod"},
            ]
        },
        status=200,
    )
    assert c.get_or_none(vrni_opts, "vc-prod") == {
        "entity_id": "2",
        "nickname": "vc-prod",
    }


def test_get_or_none_matches_entity_id(vrni_opts, vrni_authed):
    vrni_authed.add(
        responses.GET,
        f"https://{_VRNI_HOST}/api/ni/data-sources",
        json={"data_sources": [{"entity_id": "abc-123", "nickname": "vc-lab"}]},
        status=200,
    )
    assert c.get_or_none(vrni_opts, "abc-123")["nickname"] == "vc-lab"


def test_get_or_none_returns_none_when_missing(vrni_opts, vrni_authed):
    vrni_authed.add(
        responses.GET,
        f"https://{_VRNI_HOST}/api/ni/data-sources",
        json={"results": [{"entity_id": "1", "nickname": "other"}]},
        status=200,
    )
    assert c.get_or_none(vrni_opts, "missing") is None


def test_get_or_none_returns_none_on_404(vrni_opts, vrni_authed):
    vrni_authed.add(
        responses.GET,
        f"https://{_VRNI_HOST}/api/ni/data-sources",
        status=404,
    )
    assert c.get_or_none(vrni_opts, "anything") is None


def test_get_or_none_propagates_500(vrni_opts, vrni_authed):
    vrni_authed.add(
        responses.GET,
        f"https://{_VRNI_HOST}/api/ni/data-sources",
        status=500,
    )
    with pytest.raises(requests.HTTPError):
        c.get_or_none(vrni_opts, "anything")


def test_get_or_none_accepts_bare_list(vrni_opts, vrni_authed):
    vrni_authed.add(
        responses.GET,
        f"https://{_VRNI_HOST}/api/ni/data-sources",
        json=[{"entity_id": "1", "nickname": "vc-prod"}],
        status=200,
    )
    assert c.get_or_none(vrni_opts, "vc-prod")["entity_id"] == "1"


# ---------------------------------------------------------------------------
# Bootstrap helpers: wait_for_setup_ready + complete_setup + get_shared_secret
# ---------------------------------------------------------------------------


def test_wait_for_setup_ready_polls_and_succeeds(vrni_opts, monkeypatch):
    """Polls until the Platform answers with any <500 status."""
    version_url = f"https://{_VRNI_HOST}{c._VERSION_PATH}"

    class _R:  # minimal stand-in for requests.Response with a status_code
        def __init__(self, code):
            self.status_code = code

    calls = {"n": 0}

    def fake_get(url, **_kw):
        assert url == version_url
        calls["n"] += 1
        # First two calls: appliance still booting (503); third: 401 (auth
        # required — good enough — wizard-reachable).
        return _R(503 if calls["n"] < 3 else 401)

    sleeps = []
    monkeypatch.setattr(c.requests, "get", fake_get)
    monkeypatch.setattr(c.time, "sleep", sleeps.append)
    monkeypatch.setattr(c.time, "monotonic", lambda: 0.0)

    resp = c.wait_for_setup_ready(vrni_opts, timeout=60, poll_interval=1)
    assert resp.status_code == 401
    assert calls["n"] == 3
    # Slept between the first three probes (i.e. twice).
    assert sleeps == [1.0, 1.0]


def test_wait_for_setup_ready_times_out(vrni_opts, monkeypatch):
    """Raises TimeoutError once the deadline elapses."""

    def _boom(*_a, **_kw):
        raise c.requests.ConnectionError("no route to host")

    monkeypatch.setattr(c.requests, "get", _boom)
    monkeypatch.setattr(c.time, "sleep", lambda _s: None)
    # 0 → still-open; 100 → past deadline of 60.
    seq = iter([0.0, 100.0])
    monkeypatch.setattr(c.time, "monotonic", lambda: next(seq))

    with pytest.raises(TimeoutError, match="setup wizard not reachable"):
        c.wait_for_setup_ready(vrni_opts, timeout=60, poll_interval=1)


def test_complete_setup_posts_wizard_body(vrni_opts, mocked_responses):
    """POSTs the setup path with the wizard fields; returns parsed body."""
    setup_url = f"https://{_VRNI_HOST}{c._SETUP_PATH}"
    mocked_responses.add(
        responses.POST,
        setup_url,
        json={"status": "SETUP_COMPLETE"},
        status=200,
    )
    result = c.complete_setup(
        vrni_opts,
        admin_password="s3cret",
        admin_email="netops@example.com",
        license_key="AAAA-BBBB-CCCC-DDDD",
        ntp_servers=["ntp.example.com"],
        web_proxy={"host": "proxy.example.com", "port": 3128},
        telemetry_enabled=True,
    )
    assert result == {"status": "SETUP_COMPLETE"}
    req = mocked_responses.calls[-1].request
    assert req.url == setup_url
    body = req.body.decode() if isinstance(req.body, bytes) else req.body
    assert '"admin_password": "s3cret"' in body
    assert '"admin_email": "netops@example.com"' in body
    assert '"license_key": "AAAA-BBBB-CCCC-DDDD"' in body
    assert '"ntp.example.com"' in body
    assert '"telemetry_enabled": true' in body
    assert '"accept_eula": true' in body
    assert '"proxy.example.com"' in body


def test_complete_setup_raises_on_http_error(vrni_opts, mocked_responses):
    """Any 4xx/5xx from the wizard endpoint becomes a RuntimeError."""
    setup_url = f"https://{_VRNI_HOST}{c._SETUP_PATH}"
    mocked_responses.add(
        responses.POST,
        setup_url,
        json={"error": "invalid license"},
        status=400,
    )
    with pytest.raises(RuntimeError, match="setup wizard POST"):
        c.complete_setup(
            vrni_opts,
            admin_password="s3cret",
            admin_email="netops@example.com",
            license_key="bad",
            ntp_servers=["ntp.example.com"],
        )


def test_get_shared_secret_returns_bare_secret(vrni_opts, vrni_authed):
    """Newer builds return {'secret': '…'} directly."""
    vrni_authed.add(
        responses.POST,
        f"https://{_VRNI_HOST}{c._SHARED_SECRET_PATH}",
        json={"secret": "sh4red-abc"},
        status=200,
    )
    assert c.get_shared_secret(vrni_opts) == "sh4red-abc"


def test_get_shared_secret_returns_results_array_shape(vrni_opts, vrni_authed):
    """Older builds wrap in {'results': [{'secret': '…'}]} (see NIRestClient)."""
    vrni_authed.add(
        responses.POST,
        f"https://{_VRNI_HOST}{c._SHARED_SECRET_PATH}",
        json={"results": [{"secret": "sh4red-xyz", "id": "n-1"}]},
        status=200,
    )
    assert c.get_shared_secret(vrni_opts) == "sh4red-xyz"


def test_get_shared_secret_raises_on_missing_field(vrni_opts, vrni_authed):
    vrni_authed.add(
        responses.POST,
        f"https://{_VRNI_HOST}{c._SHARED_SECRET_PATH}",
        json={"results": []},
        status=200,
    )
    with pytest.raises(RuntimeError, match="missing 'secret'"):
        c.get_shared_secret(vrni_opts)
