"""Tests for HCX Manager REST client + utils.

Covers the two-step session-auth flow (POST /hybridity/api/sessions with Basic
auth returning the ``x-hm-authorization`` header) plus the version / sites
endpoints and the ``get_or_none`` lookup used by the state module.

No live HCX calls are made -- all traffic is intercepted by the ``responses``
library. The ``hcx`` pillar block is injected via a local fixture (conftest
is intentionally not modified).
"""

import pytest
import requests
import responses

from saltext.vcf.clients import hcx_manager
from saltext.vcf.utils import hcx as hcx_utils

_SESSIONS_URL = "https://hcx.test/hybridity/api/sessions"
_ABOUT_URL = "https://hcx.test/hybridity/api/about"
_SITES_URL = "https://hcx.test/hybridity/api/cloudConfigs"
_ACTIVATE_URL = "https://hcx.test/hybridity/api/activate"
_VCENTERS_URL = "https://hcx.test/hybridity/api/vcenters"
_LOOKUPSERVICE_URL = "https://hcx.test/hybridity/api/lookupservice"


@pytest.fixture
def hcx_opts(opts):
    """Add ``saltext.vcf.hcx`` to the shared opts fixture."""
    opts["pillar"]["saltext.vcf"]["hcx"] = {
        "host": "hcx.test",
        "username": "admin",
        "password": "p",
        "verify_ssl": False,
    }
    return opts


@pytest.fixture(autouse=True)
def _clear_hcx_cache():
    hcx_utils._TOKEN_CACHE.clear()
    yield
    hcx_utils._TOKEN_CACHE.clear()


@pytest.fixture
def hcx_authed(mocked_responses):
    """Pre-register the HCX Manager sessions POST returning the header token."""
    mocked_responses.add(
        responses.POST,
        _SESSIONS_URL,
        status=200,
        headers={"x-hm-authorization": "hcx-tok-abc"},
        body="",
    )
    return mocked_responses


# --- utils ------------------------------------------------------------------


def test_get_config_reads_hcx_block(hcx_opts):
    cfg = hcx_utils.get_config(hcx_opts)
    assert cfg["host"] == "hcx.test"
    assert cfg["username"] == "admin"
    assert cfg["password"] == "p"
    assert cfg["verify_ssl"] is False


def test_get_token_uses_basic_auth_and_reads_header(hcx_opts, hcx_authed):
    token = hcx_utils.get_token(hcx_opts)
    assert token == "hcx-tok-abc"
    req = hcx_authed.calls[-1].request
    assert req.headers.get("Authorization", "").startswith("Basic ")


def test_get_token_is_cached(hcx_opts, hcx_authed):
    hcx_utils.get_token(hcx_opts)
    hcx_utils.get_token(hcx_opts)
    # Only one POST despite two calls.
    session_calls = [c for c in hcx_authed.calls if c.request.url == _SESSIONS_URL]
    assert len(session_calls) == 1


def test_get_token_falls_back_to_json_body(hcx_opts, mocked_responses):
    """Some HCX builds omit the header and return the token in the JSON body."""
    mocked_responses.add(
        responses.POST,
        _SESSIONS_URL,
        status=200,
        json={"hcspAuthorization": "body-tok"},
    )
    assert hcx_utils.get_token(hcx_opts) == "body-tok"


def test_get_token_raises_when_no_token(hcx_opts, mocked_responses):
    mocked_responses.add(responses.POST, _SESSIONS_URL, status=200, body="")
    with pytest.raises(RuntimeError, match="no x-hm-authorization"):
        hcx_utils.get_token(hcx_opts)


def test_invalidate_token(hcx_opts, hcx_authed):
    hcx_utils.get_token(hcx_opts)
    assert hcx_utils._TOKEN_CACHE
    hcx_utils.invalidate_token(hcx_opts)
    assert not hcx_utils._TOKEN_CACHE


def test_api_get_sends_x_hm_authorization(hcx_opts, hcx_authed):
    hcx_authed.add(responses.GET, _ABOUT_URL, json={"buildVersion": "4.9.0"}, status=200)
    body = hcx_utils.api_get(hcx_opts, "/hybridity/api/about")
    assert body == {"buildVersion": "4.9.0"}
    req = hcx_authed.calls[-1].request
    assert req.headers.get("x-hm-authorization") == "hcx-tok-abc"


# --- client -----------------------------------------------------------------


def test_get_version_returns_about_payload(hcx_opts, hcx_authed):
    hcx_authed.add(
        responses.GET,
        _ABOUT_URL,
        json={"buildVersion": "4.9.0.0-12345", "productName": "HCX"},
        status=200,
    )
    body = hcx_manager.get_version(hcx_opts)
    assert body["buildVersion"] == "4.9.0.0-12345"


def test_list_sites_returns_body(hcx_opts, hcx_authed):
    hcx_authed.add(
        responses.GET,
        _SITES_URL,
        json={"items": [{"endpointName": "peer-a"}]},
        status=200,
    )
    body = hcx_manager.list_sites(hcx_opts)
    assert body["items"][0]["endpointName"] == "peer-a"


def test_get_or_none_matches_by_endpoint_name(hcx_opts, hcx_authed):
    hcx_authed.add(
        responses.GET,
        _SITES_URL,
        json={
            "items": [
                {"endpointName": "peer-a", "url": "https://a"},
                {"endpointName": "peer-b", "url": "https://b"},
            ]
        },
        status=200,
    )
    assert hcx_manager.get_or_none(hcx_opts, "peer-b")["url"] == "https://b"


def test_get_or_none_matches_by_name_key(hcx_opts, hcx_authed):
    """Some HCX responses use ``name`` rather than ``endpointName``."""
    hcx_authed.add(
        responses.GET,
        _SITES_URL,
        json={"data": [{"name": "peer-x"}]},
        status=200,
    )
    assert hcx_manager.get_or_none(hcx_opts, "peer-x") == {"name": "peer-x"}


def test_get_or_none_returns_none_when_missing(hcx_opts, hcx_authed):
    hcx_authed.add(
        responses.GET,
        _SITES_URL,
        json={"items": [{"endpointName": "other"}]},
        status=200,
    )
    assert hcx_manager.get_or_none(hcx_opts, "peer-a") is None


def test_get_or_none_returns_none_on_404(hcx_opts, hcx_authed):
    hcx_authed.add(responses.GET, _SITES_URL, status=404)
    assert hcx_manager.get_or_none(hcx_opts, "peer-a") is None


def test_get_or_none_propagates_500(hcx_opts, hcx_authed):
    hcx_authed.add(responses.GET, _SITES_URL, status=500)
    with pytest.raises(requests.HTTPError):
        hcx_manager.get_or_none(hcx_opts, "peer-a")


def test_get_or_none_handles_list_body(hcx_opts, hcx_authed):
    hcx_authed.add(
        responses.GET,
        _SITES_URL,
        json=[{"endpointName": "peer-a"}],
        status=200,
    )
    assert hcx_manager.get_or_none(hcx_opts, "peer-a") == {"endpointName": "peer-a"}


# --- setup readiness -------------------------------------------------------


# HCX 9.x wait_for_setup_ready uses unauthenticated GET /hybridity/api/endpointInfo,
# not the old POST /hybridity/api/sessions poll. The old flow needed SSO/vCenter to
# already be configured to succeed — chicken/egg on a fresh appliance.

_ENDPOINT_INFO_URL = "https://hcx.test/hybridity/api/endpointInfo"
_ADMIN_LOGIN_URL = "https://hcx.test:9443/api/admin/v1/sessions"
_ADMIN_VCENTER_URL = "https://hcx.test:9443/api/admin/global/config/vcenter"
_ADMIN_CERTS_URL = "https://hcx.test:9443/api/admin/certificates"
_ADMIN_APPLIANCE_CFG_URL = "https://hcx.test:9443/api/admin/global/config/applianceConfiguration"


def test_wait_for_setup_ready_succeeds_on_endpoint_info_2xx(
    hcx_opts, mocked_responses, monkeypatch
):
    """503 -> 503 -> 200 XML: returns even when the body isn't JSON."""
    sleeps = []
    monkeypatch.setattr("saltext.vcf.clients.hcx_manager.time.sleep", sleeps.append)
    mocked_responses.add(responses.GET, _ENDPOINT_INFO_URL, status=503, body="warming up")
    mocked_responses.add(responses.GET, _ENDPOINT_INFO_URL, status=503, body="warming up")
    # HCX 9.x endpointInfo returns XML, not JSON. wait_for_setup_ready must not
    # try to parse the body — it should treat any <500 status as ready.
    mocked_responses.add(
        responses.GET,
        _ENDPOINT_INFO_URL,
        status=200,
        body="<?xml version='1.0'?><com.vmware.vchs.hybridity.protocol.RestResponse><success>true</success></com.vmware.vchs.hybridity.protocol.RestResponse>",
        content_type="application/xml",
    )
    result = hcx_manager.wait_for_setup_ready(hcx_opts, timeout=60, poll_interval=1)
    assert result["status"] == 200
    # Slept twice (after first two 503s), not after the successful one.
    assert sleeps == [1.0, 1.0]


def test_wait_for_setup_ready_times_out(hcx_opts, mocked_responses, monkeypatch):
    sleeps = []
    monkeypatch.setattr("saltext.vcf.clients.hcx_manager.time.sleep", sleeps.append)
    times = iter([1000.0, 1000.0, 9999.0, 9999.0, 9999.0])
    monkeypatch.setattr("saltext.vcf.clients.hcx_manager.time.monotonic", lambda: next(times))
    mocked_responses.add(responses.GET, _ENDPOINT_INFO_URL, status=503, body="warming up")
    with pytest.raises(TimeoutError, match="not ready within"):
        hcx_manager.wait_for_setup_ready(hcx_opts, timeout=10, poll_interval=1)


# --- activation ------------------------------------------------------------


def test_activate_placeholder_key_warns_and_returns_evaluation_state(hcx_opts, mocked_responses):
    """HCX 9.x has no POST /hybridity/api/activate; runs in EVALUATION_MODE.

    A placeholder key is logged-and-ignored. Without an admin_password, we
    skip even the state probe (nothing we can actually check).
    """
    result = hcx_manager.activate(
        hcx_opts,
        activation_key="PLACEHOLDER_REPLACE_ME",
        admin_password=None,
    )
    assert result["skipped"] is True


def test_activate_with_admin_pw_reports_license_state(hcx_opts, mocked_responses):
    mocked_responses.add(
        responses.POST,
        _ADMIN_LOGIN_URL,
        status=200,
        headers={"x-hm-authorization": "admin-tok-abc"},
        body="{}",
    )
    mocked_responses.add(
        responses.GET,
        "https://hcx.test:9443/api/admin/global/config/applianceInfo",
        json={"data": {"items": [{"config": {"activationType": "STANDALONE_CONNECTED"}}]}},
    )
    mocked_responses.add(
        responses.GET,
        "https://hcx.test:9443/api/admin/licenses",
        json={"licenseStatus": "EVALUATION_MODE"},
    )
    result = hcx_manager.activate(hcx_opts, admin_password="admin-pw")
    assert result["activationType"] == "STANDALONE_CONNECTED"
    assert result["licenseStatus"] == "EVALUATION_MODE"


# --- vCenter registration --------------------------------------------------


def test_configure_vcenter_requires_admin_password(hcx_opts):
    with pytest.raises(KeyError, match="admin_password is required"):
        hcx_manager.configure_vcenter(
            hcx_opts,
            vcenter_url="https://vc.test",
            vcenter_username="administrator@vsphere.local",
            vcenter_password="p",
        )


def test_configure_vcenter_short_circuits_when_already_configured(hcx_opts, mocked_responses):
    """applianceConfiguration=true → no vCenter POST, treat as done."""
    mocked_responses.add(
        responses.POST,
        _ADMIN_LOGIN_URL,
        status=200,
        headers={"x-hm-authorization": "admin-tok-abc"},
        body="{}",
    )
    mocked_responses.add(responses.GET, _ADMIN_APPLIANCE_CFG_URL, json=True)
    mocked_responses.add(responses.GET, _ADMIN_VCENTER_URL, json={"data": {"items": []}})
    result = hcx_manager.configure_vcenter(
        hcx_opts,
        vcenter_url="https://vc.test",
        vcenter_username="administrator@vsphere.local",
        vcenter_password="p",
        admin_password="admin-pw",
    )
    assert result["already_registered"] is True
    assert result["applianceConfiguration"] is True


def test_configure_vcenter_cert_trust_dance_on_400(hcx_opts, mocked_responses):
    """First POST returns 400 with an untrusted cert; client posts the cert,
    then retries the POST once, which succeeds."""
    mocked_responses.add(
        responses.POST,
        _ADMIN_LOGIN_URL,
        status=200,
        headers={"x-hm-authorization": "admin-tok-abc"},
        body="{}",
    )
    mocked_responses.add(responses.GET, _ADMIN_APPLIANCE_CFG_URL, json=False)
    # First POST /vcenter → 400 with cert.
    mocked_responses.add(
        responses.POST,
        _ADMIN_VCENTER_URL,
        status=400,
        json={"data": [{"certificate": "BASE64-CERT-BLOB"}]},
    )
    # Cert trust POST → 200.
    mocked_responses.add(responses.POST, _ADMIN_CERTS_URL, status=200, json={})
    # Retry POST /vcenter → 200.
    mocked_responses.add(responses.POST, _ADMIN_VCENTER_URL, status=200, json={})
    # applianceConfiguration verify.
    mocked_responses.add(responses.GET, _ADMIN_APPLIANCE_CFG_URL, json=True)

    result = hcx_manager.configure_vcenter(
        hcx_opts,
        vcenter_url="https://vc.test",
        vcenter_username="administrator@vsphere.local",
        vcenter_password="p",
        admin_password="admin-pw",
    )
    assert result["vcenter"] is True
    assert result["applianceConfiguration"] is True
    # Certificate POST body carries the base64 blob from the first 400.
    cert_call = [c for c in mocked_responses.calls if c.request.url == _ADMIN_CERTS_URL][0]
    assert b"BASE64-CERT-BLOB" in cert_call.request.body


def test_configure_vcenter_base64_encodes_password(hcx_opts, mocked_responses):
    import base64

    mocked_responses.add(
        responses.POST,
        _ADMIN_LOGIN_URL,
        status=200,
        headers={"x-hm-authorization": "admin-tok-abc"},
        body="{}",
    )
    mocked_responses.add(responses.GET, _ADMIN_APPLIANCE_CFG_URL, json=False)
    mocked_responses.add(responses.POST, _ADMIN_VCENTER_URL, status=200, json={})
    mocked_responses.add(responses.GET, _ADMIN_APPLIANCE_CFG_URL, json=True)

    hcx_manager.configure_vcenter(
        hcx_opts,
        vcenter_url="https://vc.test",
        vcenter_username="administrator@vsphere.local",
        vcenter_password="raw-pw",
        admin_password="admin-pw",
    )
    vc_call = [c for c in mocked_responses.calls if c.request.url == _ADMIN_VCENTER_URL][0]
    expected_b64 = base64.b64encode(b"raw-pw").decode()
    assert expected_b64.encode() in vc_call.request.body
