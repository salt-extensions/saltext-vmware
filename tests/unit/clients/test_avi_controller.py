"""Tests for clients.avi_controller and the AVI session utility.

AVI's REST API uses cookie-based session auth. These tests exercise the
happy path (login, initial-data version fetch, cloud lookup) and the
failure/edge cases (missing cluster fallback, 404 collapsed to None by
``get_or_none``, controller-down handled by ``ping``).
"""

import pytest
import requests
import responses

from saltext.vcf.clients import avi_controller as c
from saltext.vcf.utils import avi as avi_utils


@pytest.fixture
def avi_opts():
    """Opts with only the AVI pillar populated."""
    return {
        "pillar": {
            "saltext.vcf": {
                "avi": {
                    "host": "alb.test",
                    "username": "admin",
                    "password": "p",
                    "tenant": "admin",
                    "verify_ssl": False,
                },
            },
        },
        "test": False,
    }


@pytest.fixture(autouse=True)
def _reset_avi_cache():
    avi_utils._SESSION_CACHE.clear()
    yield
    avi_utils._SESSION_CACHE.clear()


@pytest.fixture
def avi_authed(mocked_responses):
    """Pre-register the AVI /login POST so read calls succeed."""
    mocked_responses.add(
        responses.POST,
        "https://alb.test/login",
        json={"user": {"name": "admin"}},
        status=200,
        headers={"Set-Cookie": "csrftoken=csrf-tok-abc; Path=/"},
    )
    return mocked_responses


def test_get_version_returns_initial_data_version_block(avi_opts, avi_authed):
    avi_authed.add(
        responses.GET,
        "https://alb.test/api/initial-data",
        json={"version": {"Version": "22.1.3", "build": 9012}},
        status=200,
    )
    result = c.get_version(avi_opts)
    assert result == {"Version": "22.1.3", "build": 9012}


def test_get_version_falls_back_to_cluster_version(avi_opts, avi_authed):
    avi_authed.add(
        responses.GET,
        "https://alb.test/api/initial-data",
        json={"version": {}},
        status=200,
    )
    avi_authed.add(
        responses.GET,
        "https://alb.test/api/cluster/version",
        json={"Version": "20.1.7", "build": 4321},
        status=200,
    )
    result = c.get_version(avi_opts)
    assert result == {"Version": "20.1.7", "build": 4321}


def test_login_sets_csrf_header(avi_opts, avi_authed):
    avi_authed.add(
        responses.GET,
        "https://alb.test/api/initial-data",
        json={"version": {"Version": "22.1.3"}},
        status=200,
    )
    c.get_version(avi_opts)
    # First call is /login, second is /api/initial-data.
    login_req = avi_authed.calls[0].request
    api_req = avi_authed.calls[1].request
    assert login_req.url == "https://alb.test/login"
    assert api_req.headers.get("X-CSRFToken") == "csrf-tok-abc"
    assert api_req.headers.get("Referer") == "https://alb.test/"
    assert api_req.headers.get("X-Avi-Version")


def test_session_is_cached_across_calls(avi_opts, avi_authed):
    avi_authed.add(
        responses.GET,
        "https://alb.test/api/initial-data",
        json={"version": {"Version": "22.1.3"}},
        status=200,
    )
    avi_authed.add(
        responses.GET,
        "https://alb.test/api/initial-data",
        json={"version": {"Version": "22.1.3"}},
        status=200,
    )
    c.get_version(avi_opts)
    c.get_version(avi_opts)
    # /login should have fired exactly once.
    login_calls = [call for call in avi_authed.calls if call.request.url.endswith("/login")]
    assert len(login_calls) == 1


def test_list_clouds_unwraps_results(avi_opts, avi_authed):
    avi_authed.add(
        responses.GET,
        "https://alb.test/api/cloud",
        json={"results": [{"name": "Default-Cloud"}, {"name": "vc-mgmt"}]},
        status=200,
    )
    clouds = c.list_clouds(avi_opts)
    assert [cl["name"] for cl in clouds] == ["Default-Cloud", "vc-mgmt"]


def test_list_clouds_empty_when_no_results(avi_opts, avi_authed):
    avi_authed.add(responses.GET, "https://alb.test/api/cloud", json={"results": []}, status=200)
    assert c.list_clouds(avi_opts) == []


def test_get_or_none_returns_matching_cloud(avi_opts, avi_authed):
    avi_authed.add(
        responses.GET,
        "https://alb.test/api/cloud",
        json={"results": [{"name": "vc-mgmt", "uuid": "cloud-1"}]},
        status=200,
    )
    assert c.get_or_none(avi_opts, "vc-mgmt") == {"name": "vc-mgmt", "uuid": "cloud-1"}


def test_get_or_none_returns_none_when_absent(avi_opts, avi_authed):
    avi_authed.add(
        responses.GET,
        "https://alb.test/api/cloud",
        json={"results": [{"name": "Default-Cloud"}]},
        status=200,
    )
    assert c.get_or_none(avi_opts, "missing") is None


def test_get_or_none_returns_none_on_404(avi_opts, avi_authed):
    avi_authed.add(responses.GET, "https://alb.test/api/cloud", status=404)
    assert c.get_or_none(avi_opts, "vc-mgmt") is None


def test_get_or_none_propagates_500(avi_opts, avi_authed):
    avi_authed.add(responses.GET, "https://alb.test/api/cloud", status=500)
    with pytest.raises(requests.HTTPError):
        c.get_or_none(avi_opts, "vc-mgmt")


def test_ping_true_on_reachable(avi_opts, avi_authed):
    avi_authed.add(
        responses.GET,
        "https://alb.test/api/initial-data",
        json={"version": {"Version": "22.1.3"}},
        status=200,
    )
    assert c.ping(avi_opts) is True


def test_ping_false_on_connection_error(avi_opts, mocked_responses):
    """Controller down => /login fails => ping returns False."""
    mocked_responses.add(
        responses.POST,
        "https://alb.test/login",
        body=requests.ConnectionError("boom"),
    )
    assert c.ping(avi_opts) is False


def test_ping_false_on_http_error(avi_opts, avi_authed):
    """Auth OK but /api/initial-data 500 => ping False, no exception."""
    avi_authed.add(responses.GET, "https://alb.test/api/initial-data", status=500)
    avi_authed.add(responses.GET, "https://alb.test/api/cluster/version", status=500)
    assert c.ping(avi_opts) is False


def test_get_config_reads_defaults(avi_opts):
    cfg = avi_utils.get_config(avi_opts)
    assert cfg["host"] == "alb.test"
    assert cfg["username"] == "admin"
    assert cfg["tenant"] == "admin"
    assert cfg["verify_ssl"] is False
    assert cfg["api_version"]  # default present


def test_get_config_profile_override():
    opts = {
        "pillar": {
            "saltext.vcf": {
                "avi": {"host": "primary.test", "username": "a", "password": "p"},
                "profiles": {
                    "alt": {
                        "avi": {
                            "host": "alt.test",
                            "username": "b",
                            "password": "q",
                            "verify_ssl": False,
                        }
                    }
                },
            }
        }
    }
    cfg = avi_utils.get_config(opts, profile="alt")
    assert cfg["host"] == "alt.test"
    assert cfg["username"] == "b"


def test_invalidate_session_forces_relogin(avi_opts, avi_authed):
    avi_authed.add(
        responses.GET,
        "https://alb.test/api/initial-data",
        json={"version": {"Version": "22.1.3"}},
        status=200,
    )
    # second login (after invalidate) needs its own /login response.
    avi_authed.add(
        responses.POST,
        "https://alb.test/login",
        json={"user": {"name": "admin"}},
        status=200,
        headers={"Set-Cookie": "csrftoken=csrf-tok-def; Path=/"},
    )
    avi_authed.add(
        responses.GET,
        "https://alb.test/api/initial-data",
        json={"version": {"Version": "22.1.3"}},
        status=200,
    )
    c.get_version(avi_opts)
    avi_utils.invalidate_session(avi_opts)
    c.get_version(avi_opts)
    login_calls = [call for call in avi_authed.calls if call.request.url.endswith("/login")]
    assert len(login_calls) == 2


# ---------------------------------------------------------------------------
# Bootstrap / deploy-on-absence surface
# ---------------------------------------------------------------------------


def test_wait_for_setup_ready_succeeds_after_poll(avi_opts, mocked_responses, monkeypatch):
    """503, 503, 200 sequence => returns without raising."""
    monkeypatch.setattr("time.sleep", lambda _s: None)
    mocked_responses.add(responses.GET, "https://alb.test/api/initial-data", status=503)
    mocked_responses.add(responses.GET, "https://alb.test/api/initial-data", status=503)
    mocked_responses.add(
        responses.GET,
        "https://alb.test/api/initial-data",
        json={"version": {"Version": "22.1.3"}},
        status=200,
    )
    c.wait_for_setup_ready(avi_opts, timeout=60, poll_interval=1)
    poll_calls = [
        call for call in mocked_responses.calls if call.request.url.endswith("/api/initial-data")
    ]
    assert len(poll_calls) == 3


def test_wait_for_setup_ready_times_out(avi_opts, mocked_responses, monkeypatch):
    """All 503s => RuntimeError once the deadline passes."""
    # Fake a monotonic clock that advances by 10s per call so we time out
    # after two polls without relying on real sleep.
    now = {"t": 0.0}

    def _fake_monotonic():
        now["t"] += 10
        return now["t"]

    monkeypatch.setattr("time.sleep", lambda _s: None)
    monkeypatch.setattr("time.monotonic", _fake_monotonic)
    for _ in range(5):
        mocked_responses.add(responses.GET, "https://alb.test/api/initial-data", status=503)
    with pytest.raises(RuntimeError, match="first-boot wizard not ready"):
        c.wait_for_setup_ready(avi_opts, timeout=15, poll_interval=1)


def test_initial_setup_removed_raises_not_implemented(avi_opts):
    # POST /api/initial-controller-setup does not exist on AVI 22.x+; the
    # symbol is kept only as an actionable deprecation stub.
    with pytest.raises(NotImplementedError, match="bootstrap_wizard"):
        c.initial_setup(
            avi_opts,
            admin_password="ignored",
            dns_servers=[],
            ntp_servers=[],
            backup_passphrase="ignored",
        )


def test_bootstrap_wizard_walks_expected_endpoints(avi_opts, mocked_responses):
    """The AVI 22.x wizard is an 8-step form/JSON dance driven from the UI.

    Verify that :func:`bootstrap_wizard` fires each step in the expected order
    and that the CSRF token is sourced from the ``csrftoken`` cookie set by
    the login response (not from a request header).
    """
    calls = []

    def _login(request):
        calls.append(("POST", "/login"))
        return (
            200,
            {"Set-Cookie": "csrftoken=csrf-1; Path=/"},
            "{}",
        )

    def _useraccount(request):
        calls.append(("PUT", "/api/useraccount"))
        return (200, {}, "null")

    def _login2(request):
        calls.append(("POST", "/login-2"))
        return (
            200,
            {"Set-Cookie": "csrftoken=csrf-2; Path=/"},
            "{}",
        )

    def _syscfg(request):
        import json as _json

        body = _json.loads(request.body)
        calls.append(("PATCH", "/api/systemconfiguration", list(body["replace"].keys())[0]))
        return (200, {}, "{}")

    def _get_backup(request):
        calls.append(("GET", "/api/backupconfiguration"))
        return (
            200,
            {},
            '{"results":[{"url":"https://alb.test/api/backupconfiguration/backup-abc"}]}',
        )

    def _patch_backup(request):
        calls.append(("PATCH", "/api/backupconfiguration/backup-abc"))
        return (200, {}, "{}")

    mocked_responses.add_callback(
        responses.POST,
        "https://alb.test/login",
        callback=_login,
        content_type="application/json",
    )
    mocked_responses.add_callback(
        responses.PUT,
        "https://alb.test/api/useraccount",
        callback=_useraccount,
        content_type="application/json",
    )
    mocked_responses.add_callback(
        responses.POST,
        "https://alb.test/login",
        callback=_login2,
        content_type="application/json",
    )
    # Three PATCH calls to /api/systemconfiguration (DNS, NTP, welcome).
    for _ in range(3):
        mocked_responses.add_callback(
            responses.PATCH,
            "https://alb.test/api/systemconfiguration",
            callback=_syscfg,
            content_type="application/json",
        )
    mocked_responses.add_callback(
        responses.GET,
        "https://alb.test/api/backupconfiguration",
        callback=_get_backup,
        content_type="application/json",
    )
    mocked_responses.add_callback(
        responses.PATCH,
        "https://alb.test/api/backupconfiguration/backup-abc",
        callback=_patch_backup,
        content_type="application/json",
    )

    result = c.bootstrap_wizard(
        avi_opts,
        default_password="ova-baked-default",
        new_password="rotated!",
        dns_servers=["10.0.0.53"],
        ntp_servers=["pool.ntp.org"],
        backup_passphrase="AviBackupPass1!",
    )
    assert result["admin_password_rotated"] is True
    assert result["welcome_workflow_complete"] is True
    # Step ordering: login, PUT useraccount, re-login, PATCH DNS, PATCH NTP,
    # GET backup URL, PATCH backup passphrase, PATCH welcome-workflow.
    kinds = [c[:2] for c in calls]
    assert kinds == [
        ("POST", "/login"),
        ("PUT", "/api/useraccount"),
        ("POST", "/login-2"),
        ("PATCH", "/api/systemconfiguration"),
        ("PATCH", "/api/systemconfiguration"),
        ("GET", "/api/backupconfiguration"),
        ("PATCH", "/api/backupconfiguration/backup-abc"),
        ("PATCH", "/api/systemconfiguration"),
    ]
    # DNS first, then NTP, then welcome_workflow.
    patch_targets = [c[2] for c in calls if c[0] == "PATCH" and c[1] == "/api/systemconfiguration"]
    assert patch_targets == ["dns_configuration", "ntp_configuration", "welcome_workflow_complete"]


def test_configure_cluster_puts_nodes(avi_opts, avi_authed):
    captured = {}

    def _capture(request):
        import json as _json

        captured["body"] = _json.loads(request.body)
        return (200, {}, "{}")

    avi_authed.add_callback(
        responses.PUT,
        "https://alb.test/api/cluster",
        callback=_capture,
        content_type="application/json",
    )
    result = c.configure_cluster(
        avi_opts,
        nodes=[
            {"name": "node-1", "ip": "10.0.0.11"},
            {"name": "node-2", "ip": "10.0.0.12"},
            {"name": "node-3", "ip": "10.0.0.13"},
        ],
        cluster_ip="10.0.0.10",
    )
    assert result == {}
    body = captured["body"]
    assert body["virtual_ip"] == {"type": "V4", "addr": "10.0.0.10"}
    assert body["nodes"] == [
        {"name": "node-1", "ip": {"type": "V4", "addr": "10.0.0.11"}},
        {"name": "node-2", "ip": {"type": "V4", "addr": "10.0.0.12"}},
        {"name": "node-3", "ip": {"type": "V4", "addr": "10.0.0.13"}},
    ]


def test_deploy_ova_dispatches_pyvmomi(monkeypatch):
    from saltext.vcf.clients import ovf_deploy as ovf

    seen = {}

    def _fake(**kwargs):
        seen.update(kwargs)
        return {"vm_name": kwargs["vm_name"], "powered_on": True}

    monkeypatch.setattr(ovf, "deploy_ova", _fake)
    result = c.deploy_ova(
        {
            "ova_url": "/tmp/avi.ova",
            "vm_name": "alb-1",
            "target_host": "esxi.test",
            "target_user": "root",
            "target_password": "pw",
        }
    )
    assert result == {"vm_name": "alb-1", "powered_on": True}
    assert seen["ova_source"] == "/tmp/avi.ova"
    assert seen["target_host"] == "esxi.test"
    assert seen["disk_provisioning"] == "thin"


def test_deploy_ova_dispatches_ovftool(monkeypatch):
    from saltext.vcf.clients import ovftool_deploy as ot

    seen = {}

    def _fake(**kwargs):
        seen.update(kwargs)
        return {"vm_name": kwargs["vm_name"]}

    monkeypatch.setattr(ot, "deploy_ova", _fake)
    c.deploy_ova(
        {
            "ova_url": "/tmp/avi.ova",
            "vm_name": "alb-1",
            "target_host": "esxi.test",
            "target_user": "root",
            "target_password": "pw",
            "deployment_backend": "ovftool",
            "ovftool_path": "/usr/local/bin/ovftool",
        }
    )
    assert seen["ovftool_path"] == "/usr/local/bin/ovftool"
    assert seen["ova_source"] == "/tmp/avi.ova"


def test_deploy_ova_rejects_unknown_backend():
    with pytest.raises(ValueError, match="unsupported AVI deployment_backend"):
        c.deploy_ova(
            {
                "ova_url": "/tmp/x.ova",
                "vm_name": "n",
                "target_host": "h",
                "target_user": "u",
                "target_password": "p",
                "deployment_backend": "bogus",
            }
        )
