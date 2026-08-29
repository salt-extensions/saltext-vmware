"""Tests for the vcf_vro state module (deploy-on-absence VRO installed state)."""

import pytest
import requests

from saltext.vcf.clients import vro_orchestrator as c
from saltext.vcf.modules import vcf_vro as vro_mod
from saltext.vcf.states import vcf_vro as vro_state
from saltext.vcf.utils import vro as vro_util


@pytest.fixture
def vro_opts(opts):
    opts["pillar"]["saltext.vcf"]["vro"] = {
        "host": "vro.test",
        "username": "vcoadmin@vsphere.local",
        "password": "p",
        "verify_ssl": False,
    }
    return opts


@pytest.fixture(autouse=True)
def inject_opts(monkeypatch, vro_opts):
    monkeypatch.setattr(vro_state, "__opts__", vro_opts, raising=False)
    monkeypatch.setattr(vro_mod, "__opts__", vro_opts, raising=False)

    class _DynamicSalt(dict):
        def __getitem__(self, key):
            if key == "vcf_vro.deploy":
                return vro_mod.deploy
            return super().__getitem__(key)

    monkeypatch.setattr(vro_state, "__salt__", _DynamicSalt(), raising=False)
    vro_util._SESSION_CACHE.clear()
    yield
    vro_util._SESSION_CACHE.clear()


def test_installed_success_no_version_required(monkeypatch):
    monkeypatch.setattr(
        c, "get_version", lambda o, profile=None: {"version": "9.0.0", "api-version": "8.0"}
    )
    ret = vro_state.installed("vro-prod")
    assert ret["result"] is True
    assert ret["changes"] == {}
    assert "9.0.0" in ret["comment"]


def test_installed_is_idempotent(monkeypatch):
    """Two back-to-back calls produce identical no-op returns."""
    monkeypatch.setattr(c, "get_version", lambda o, profile=None: {"version": "9.0.0"})
    first = vro_state.installed("vro-prod")
    second = vro_state.installed("vro-prod")
    assert first == second
    assert first["changes"] == {}
    assert second["changes"] == {}


def test_installed_version_match_ok(monkeypatch):
    monkeypatch.setattr(c, "get_version", lambda o, profile=None: {"version": "9.0.1"})
    ret = vro_state.installed("vro-prod", version="9.0.1")
    assert ret["result"] is True
    assert "9.0.1" in ret["comment"]


def test_installed_version_mismatch_fails(monkeypatch):
    monkeypatch.setattr(c, "get_version", lambda o, profile=None: {"version": "9.0.1"})
    ret = vro_state.installed("vro-prod", version="9.0.2")
    assert ret["result"] is False
    assert ret["changes"] == {}
    assert "mismatch" in ret["comment"]
    assert "9.0.2" in ret["comment"]
    assert "9.0.1" in ret["comment"]


def test_installed_reports_failure_when_orchestrator_unreachable(monkeypatch):
    def _boom(_opts, profile=None):
        raise requests.exceptions.ConnectionError("host down")

    monkeypatch.setattr(c, "get_version", _boom)
    ret = vro_state.installed("vro-prod")
    assert ret["result"] is False
    assert ret["changes"] == {}
    assert "not reachable" in ret["comment"]
    assert "host down" in ret["comment"]
    # Operator guidance appears verbatim.
    assert "Deploy VRO out of band" in ret["comment"]


def test_installed_reports_failure_on_http_error(monkeypatch):
    def _boom(_opts, profile=None):
        resp = requests.Response()
        resp.status_code = 503
        raise requests.HTTPError("503 Service Unavailable", response=resp)

    monkeypatch.setattr(c, "get_version", _boom)
    ret = vro_state.installed("vro-prod")
    assert ret["result"] is False
    assert "not reachable" in ret["comment"]


def test_installed_test_mode_reports_would_verify(monkeypatch, vro_opts):
    vro_opts["test"] = True
    monkeypatch.setattr(vro_state, "__opts__", vro_opts)
    calls = {"n": 0}

    def _spy(_opts, profile=None):
        calls["n"] += 1
        return {"version": "9.0.0"}

    monkeypatch.setattr(c, "get_version", _spy)
    ret = vro_state.installed("vro-prod", version="9.0.0")
    assert ret["result"] is None
    assert "Would verify" in ret["comment"]
    assert "9.0.0" in ret["comment"]
    assert calls["n"] == 0  # test-mode short-circuits before any API call


# ---------------------------------------------------------------------------
# Deploy-on-absence tests
# ---------------------------------------------------------------------------


def _deploy_spec(license_key=None):
    spec = {
        "installer_ova_url": "https://depot.test/vro-9.0.0.ova",
        "installer_vm_name": "vro-prod",
        "installer_deploy_esxi": "esxi-01.test",
        "esxi_hosts": [{"fqdn": "esxi-01.test", "username": "root", "password": "p"}],
        "sso": {
            "lookup_service_url": "https://vc.test/lookupservice/sdk",
            "admin_user": "administrator@vsphere.local",
            "admin_password": "s3cret",
        },
    }
    if license_key:
        spec["license_key"] = license_key
    return spec


def test_installed_present_noop(monkeypatch):
    """Already reachable → no OVA push, no changes."""
    monkeypatch.setattr(c, "get_version", lambda o, profile=None: {"version": "9.0.0"})

    def _fail(*a, **kw):
        raise AssertionError("deploy() should not be invoked when appliance is reachable")

    monkeypatch.setattr(vro_mod, "deploy", _fail)
    ret = vro_state.installed("vro-prod", deploy_spec=_deploy_spec(license_key="XYZ"))
    assert ret["result"] is True
    assert ret["changes"] == {}
    assert "installed" in ret["comment"]


def test_installed_absent_deploy_spec_test_mode(monkeypatch, vro_opts):
    """test=True + deploy_spec → describes plan, no API calls."""
    vro_opts["test"] = True
    monkeypatch.setattr(vro_state, "__opts__", vro_opts)
    calls = {"n": 0}

    def _spy(_opts, profile=None):
        calls["n"] += 1
        raise AssertionError("no API call should happen in test mode")

    monkeypatch.setattr(c, "get_version", _spy)

    def _fail(*a, **kw):
        raise AssertionError("deploy() should not run in test mode")

    monkeypatch.setattr(vro_mod, "deploy", _fail)

    ret = vro_state.installed("vro-prod", deploy_spec=_deploy_spec(license_key="LK-1"))
    assert ret["result"] is None
    assert "Would deploy" in ret["comment"]
    assert "esxi-01.test" in ret["comment"]
    assert "license" in ret["comment"]
    assert ret["changes"]["plan"][0] == "deploy_vro_ova"
    assert "install_license" in ret["changes"]["plan"]
    assert ret["changes"]["plan"][-1] == "verify_get_version"
    assert calls["n"] == 0


def test_installed_absent_deploy_spec_real_mode(monkeypatch):
    """Unreachable + deploy_spec → full deploy chain runs, changes reported."""

    # First get_version raises (unreachable). We drive the deploy chain
    # via monkeypatching the module.deploy() as a single unit so we can
    # assert exactly what the state does with its return value.
    def _unreachable(_opts, profile=None):
        raise requests.exceptions.ConnectionError("nothing there yet")

    monkeypatch.setattr(c, "get_version", _unreachable)

    seen = {}

    def _fake_deploy(spec, profile=None):
        seen["spec"] = spec
        seen["profile"] = profile
        return {
            "deployed": True,
            "ova": {"vm_name": "vro-prod"},
            "sso_joined": True,
            "license_installed": True,
            "version": "9.0.0",
            "about": {"version": "9.0.0"},
        }

    monkeypatch.setattr(vro_mod, "deploy", _fake_deploy)

    ret = vro_state.installed(
        "vro-prod",
        version="9.0.0",
        deploy_spec=_deploy_spec(license_key="LK-1"),
    )
    assert ret["result"] is True
    assert ret["changes"] == {"deployed": "9.0.0"}
    assert "deployed" in ret["comment"]
    assert "9.0.0" in ret["comment"]
    assert "license" in ret["comment"]
    assert seen["spec"]["installer_vm_name"] == "vro-prod"
    assert seen["spec"]["sso"]["admin_user"] == "administrator@vsphere.local"


def test_installed_absent_deploy_spec_real_mode_full_chain(monkeypatch):
    """Same as above but exercise the underlying client seams end-to-end."""
    # Sequence of get_version returns:
    #   1. state's initial reachability probe → raise
    #   2. wait_for_setup_ready (post-OVA) → 200
    #   3. wait_for_setup_ready (post-SSO) → 200
    #   4. deploy()'s final verify → 200
    #   5. state doesn't re-probe after deploy — we consume the deploy's returned version
    responses_seq = [
        requests.exceptions.ConnectionError("boot"),
        {"version": "9.0.0"},
        {"version": "9.0.0"},
        {"version": "9.0.0"},
    ]
    idx = {"i": 0}

    def _get_version(_opts, profile=None):
        i = idx["i"]
        idx["i"] += 1
        result = responses_seq[i]
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(c, "get_version", _get_version)
    # Stub the find_vm check so we don't try to open a real vCenter session.
    from saltext.vcf.clients import ovf_deploy as _ovf

    monkeypatch.setattr(_ovf, "find_vm", lambda **kw: None)
    monkeypatch.setattr(
        vro_mod.ia_client,
        "deploy_installer",
        lambda spec: {"vm_name": spec["installer_vm_name"], "powered_on": True},
    )
    sso_calls = []
    monkeypatch.setattr(
        c,
        "sso_join",
        lambda opts, lookup_service_url, admin_user, admin_password, profile=None: sso_calls.append(
            (lookup_service_url, admin_user, admin_password)
        )
        or {"status": "OK"},
    )
    license_calls = []
    monkeypatch.setattr(
        c,
        "install_license",
        lambda opts, license_key, profile=None: license_calls.append(license_key)
        or {"status": "OK"},
    )
    # Skip any real sleeping inside wait_for_setup_ready.
    monkeypatch.setattr("saltext.vcf.clients.vro_orchestrator.time.sleep", lambda _s: None)

    ret = vro_state.installed("vro-prod", deploy_spec=_deploy_spec(license_key="LK-9"))
    assert ret["result"] is True
    assert ret["changes"] == {"deployed": "9.0.0"}
    assert sso_calls == [
        (
            "https://vc.test/lookupservice/sdk",
            "administrator@vsphere.local",
            "s3cret",
        )
    ]
    assert license_calls == ["LK-9"]


def test_installed_absent_no_deploy_spec_fails(monkeypatch):
    """Unreachable + no deploy_spec → result=False with actionable comment."""

    def _unreachable(_opts, profile=None):
        raise requests.exceptions.ConnectionError("host down")

    monkeypatch.setattr(c, "get_version", _unreachable)

    def _fail(*a, **kw):
        raise AssertionError("deploy() should not run without a deploy_spec")

    monkeypatch.setattr(vro_mod, "deploy", _fail)

    ret = vro_state.installed("vro-prod")
    assert ret["result"] is False
    assert ret["changes"] == {}
    assert "not reachable" in ret["comment"]
    assert "host down" in ret["comment"]
    assert "deploy_spec" in ret["comment"]


def test_installed_absent_deploy_spec_from_pillar(monkeypatch, vro_opts):
    """No arg deploy_spec, but pillar has one → deploy still runs."""
    vro_opts["pillar"]["saltext.vcf"]["vro"]["deploy_spec"] = _deploy_spec()
    monkeypatch.setattr(vro_state, "__opts__", vro_opts)
    monkeypatch.setattr(
        c,
        "get_version",
        lambda o, profile=None: (_ for _ in ()).throw(requests.exceptions.ConnectionError("boot")),
    )

    called = {}

    def _fake_deploy(spec, profile=None):
        called["spec"] = spec
        return {
            "deployed": True,
            "ova": {},
            "sso_joined": True,
            "license_installed": False,
            "version": "9.0.0",
            "about": {"version": "9.0.0"},
        }

    monkeypatch.setattr(vro_mod, "deploy", _fake_deploy)
    ret = vro_state.installed("vro-prod")
    assert ret["result"] is True
    assert called["spec"]["installer_vm_name"] == "vro-prod"
