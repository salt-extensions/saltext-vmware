"""Tests for clients.vro_orchestrator — verify-only VRO REST surface."""

import pytest
import requests
import responses

from saltext.vcf.clients import vro_orchestrator as c
from saltext.vcf.utils import vro as vro_util


@pytest.fixture
def vro_opts(opts):
    """opts with a ``saltext.vcf.vro`` block wired for ``vro.test``."""
    opts["pillar"]["saltext.vcf"]["vro"] = {
        "host": "vro.test",
        "username": "vcoadmin@vsphere.local",
        "password": "p",
        "verify_ssl": False,
    }
    return opts


@pytest.fixture(autouse=True)
def _clear_vro_session_cache():
    vro_util._SESSION_CACHE.clear()
    yield
    vro_util._SESSION_CACHE.clear()


def test_get_version_returns_about_payload(vro_opts, mocked_responses):
    mocked_responses.add(
        responses.GET,
        "https://vro.test/vco/api/about",
        json={
            "version": "9.0.0",
            "build-number": "12345",
            "api-version": "8.0",
        },
        status=200,
    )
    about = c.get_version(vro_opts)
    assert about["version"] == "9.0.0"
    assert about["api-version"] == "8.0"
    req = mocked_responses.calls[-1].request
    assert req.headers.get("Authorization", "").startswith("Basic ")


def test_get_version_raises_on_5xx(vro_opts, mocked_responses):
    mocked_responses.add(
        responses.GET,
        "https://vro.test/vco/api/about",
        status=500,
    )
    with pytest.raises(requests.HTTPError):
        c.get_version(vro_opts)


def test_get_version_propagates_connection_error(vro_opts, mocked_responses):
    """No matching mock → responses raises ConnectionError."""
    with pytest.raises(requests.exceptions.ConnectionError):
        c.get_version(vro_opts)


def test_list_workflows_uses_max_result_cap(vro_opts, mocked_responses):
    mocked_responses.add(
        responses.GET,
        "https://vro.test/vco/api/workflows",
        json={"link": [{"attributes": [{"name": "name", "value": "wf1"}]}], "total": 1},
        status=200,
    )
    body = c.list_workflows(vro_opts)
    assert body["total"] == 1
    req = mocked_responses.calls[-1].request
    assert "maxResult=1" in req.url


def test_get_or_none_returns_entry_when_present(vro_opts, mocked_responses):
    mocked_responses.add(
        responses.GET,
        "https://vro.test/vco/api/workflows",
        json={
            "link": [
                {"attributes": [{"name": "name", "value": "MyFlow"}]},
                {"attributes": [{"name": "name", "value": "Other"}]},
            ]
        },
        status=200,
    )
    entry = c.get_or_none(vro_opts, "myflow")
    assert entry is not None
    names = {a["name"]: a["value"] for a in entry["attributes"]}
    assert names["name"] == "MyFlow"


def test_get_or_none_returns_none_on_empty_search(vro_opts, mocked_responses):
    mocked_responses.add(
        responses.GET,
        "https://vro.test/vco/api/workflows",
        json={"link": []},
        status=200,
    )
    assert c.get_or_none(vro_opts, "missing") is None


def test_get_or_none_returns_none_on_404(vro_opts, mocked_responses):
    mocked_responses.add(
        responses.GET,
        "https://vro.test/vco/api/workflows",
        status=404,
    )
    assert c.get_or_none(vro_opts, "missing") is None


def test_get_or_none_propagates_500(vro_opts, mocked_responses):
    mocked_responses.add(
        responses.GET,
        "https://vro.test/vco/api/workflows",
        status=500,
    )
    with pytest.raises(requests.HTTPError):
        c.get_or_none(vro_opts, "x")


def test_custom_port_appears_in_url(opts, mocked_responses):
    opts["pillar"]["saltext.vcf"]["vro"] = {
        "host": "vro.test",
        "port": 8281,
        "username": "u",
        "password": "p",
        "verify_ssl": False,
    }
    mocked_responses.add(
        responses.GET,
        "https://vro.test:8281/vco/api/about",
        json={"version": "9.0.0"},
        status=200,
    )
    assert c.get_version(opts)["version"] == "9.0.0"


def test_wait_for_setup_ready_polls_and_succeeds(vro_opts, mocked_responses, monkeypatch):
    """First two attempts fail, third succeeds → returns the about body."""
    mocked_responses.add(
        responses.GET,
        "https://vro.test/vco/api/about",
        status=503,
    )
    mocked_responses.add(
        responses.GET,
        "https://vro.test/vco/api/about",
        body=requests.exceptions.ConnectionError("still booting"),
    )
    mocked_responses.add(
        responses.GET,
        "https://vro.test/vco/api/about",
        json={"version": "9.0.0"},
        status=200,
    )
    sleeps = []
    monkeypatch.setattr("saltext.vcf.clients.vro_orchestrator.time.sleep", sleeps.append)

    about = c.wait_for_setup_ready(vro_opts, timeout=60, poll_interval=1)
    assert about["version"] == "9.0.0"
    # Two failures ⇒ two sleeps before the success.
    assert len(sleeps) == 2
    assert all(s == 1 for s in sleeps)


def test_wait_for_setup_ready_times_out(vro_opts, mocked_responses, monkeypatch):
    """Every poll fails → raises TimeoutError once the deadline elapses."""
    mocked_responses.add(
        responses.GET,
        "https://vro.test/vco/api/about",
        status=503,
    )
    # Fake a monotonic that jumps forward on every call so the deadline
    # trips immediately without any real sleeping.
    ticks = iter([0.0, 0.0, 5.0, 100.0, 200.0])
    monkeypatch.setattr("saltext.vcf.clients.vro_orchestrator.time.monotonic", lambda: next(ticks))
    monkeypatch.setattr("saltext.vcf.clients.vro_orchestrator.time.sleep", lambda _s: None)

    with pytest.raises(TimeoutError) as excinfo:
        c.wait_for_setup_ready(vro_opts, timeout=10, poll_interval=1)
    assert "not ready within 10s" in str(excinfo.value)


def test_sso_join_posts_lookup_service_body(vro_opts, mocked_responses):
    mocked_responses.add(
        responses.POST,
        "https://vro.test/vco-controlcenter/api/server/sso",
        json={"status": "OK"},
        status=200,
    )
    resp = c.sso_join(
        vro_opts,
        lookup_service_url="https://vc.test/lookupservice/sdk",
        admin_user="administrator@vsphere.local",
        admin_password="s3cret",
    )
    assert resp == {"status": "OK"}
    req = mocked_responses.calls[-1].request
    import json as _json

    body = _json.loads(req.body)
    assert body == {
        "ssoUrl": "https://vc.test/lookupservice/sdk",
        "adminUser": "administrator@vsphere.local",
        "adminPassword": "s3cret",
    }
    assert req.headers.get("Authorization", "").startswith("Basic ")


def test_install_license_posts_key(vro_opts, mocked_responses):
    mocked_responses.add(
        responses.POST,
        "https://vro.test/vco-controlcenter/api/server/license",
        json={"status": "OK"},
        status=200,
    )
    resp = c.install_license(vro_opts, license_key="ABCDE-12345")
    assert resp == {"status": "OK"}
    req = mocked_responses.calls[-1].request
    import json as _json

    body = _json.loads(req.body)
    assert body == {"licenseKey": "ABCDE-12345"}
    assert req.headers.get("Authorization", "").startswith("Basic ")


# ---------------------------------------------------------------------------
# sni_hostname → Host header (vRO 9.x envoy routes on Host, not IP)
# ---------------------------------------------------------------------------


def test_get_session_injects_host_header_when_sni_hostname_pillar_set(vro_opts):
    """When pillar sets ``saltext.vcf:vro:sni_hostname``, the cached session
    must carry ``Host: <sni_hostname>`` on every request. vRO 9.x's envoy
    front-end routes on the Host header rather than the destination IP —
    without this the calls 404 whenever the appliance DHCPs to an IP that
    doesn't match its configured hostname.
    """
    vro_util._SESSION_CACHE.clear()
    vro_opts["pillar"]["saltext.vcf"]["vro"]["sni_hostname"] = "vro-25-0-0-61"
    session, _ = vro_util.get_session(vro_opts)
    try:
        assert session.headers.get("Host") == "vro-25-0-0-61"
    finally:
        vro_util._SESSION_CACHE.clear()


def test_get_session_omits_host_header_when_sni_hostname_absent(vro_opts):
    """No sni_hostname → no injected Host header (fall back to requests default)."""
    vro_util._SESSION_CACHE.clear()
    vro_opts["pillar"]["saltext.vcf"]["vro"].pop("sni_hostname", None)
    session, _ = vro_util.get_session(vro_opts)
    try:
        assert "Host" not in session.headers
    finally:
        vro_util._SESSION_CACHE.clear()
