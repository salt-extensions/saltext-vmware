"""Tests for the HCX Manager execution module + deploy-on-absence install state."""

import pytest
import requests

from saltext.vcf.modules import vcf_hcx as mod
from saltext.vcf.states import vcf_hcx as state


@pytest.fixture(autouse=True)
def inject_opts(monkeypatch, opts):
    monkeypatch.setattr(mod, "__opts__", opts, raising=False)
    monkeypatch.setattr(state, "__opts__", opts, raising=False)

    class _DynamicSalt(dict):
        def __getitem__(self, key):
            if key == "vcf_hcx.deploy":
                return mod.deploy
            return super().__getitem__(key)

    monkeypatch.setattr(state, "__salt__", _DynamicSalt(), raising=False)
    return opts


# --- module -----------------------------------------------------------------


def test_module_get_version_delegates(monkeypatch):
    called = {}

    def _fake(o, profile=None):
        called["ok"] = True
        return {"buildVersion": "4.9.0"}

    monkeypatch.setattr("saltext.vcf.clients.hcx_manager.get_version", _fake)
    assert mod.get_version() == {"buildVersion": "4.9.0"}
    assert called == {"ok": True}


def test_module_installed_returns_true_when_about_succeeds(monkeypatch):
    monkeypatch.setattr(
        "saltext.vcf.clients.hcx_manager.get_version",
        lambda o, profile=None: {"buildVersion": "4.9.0"},
    )
    result = mod.installed("hcx.test")
    assert result["installed"] is True
    assert result["version"] == "4.9.0"
    about = result["about"] or {}
    assert about["buildVersion"] == "4.9.0"


def test_module_installed_returns_false_on_connection_error(monkeypatch):
    def _boom(o, profile=None):
        raise requests.ConnectionError("nope")

    monkeypatch.setattr("saltext.vcf.clients.hcx_manager.get_version", _boom)
    result = mod.installed("hcx.test")
    assert result["installed"] is False
    assert result["version"] is None
    assert result["about"] is None


def test_module_installed_returns_false_on_runtime_error(monkeypatch):
    def _boom(o, profile=None):
        raise RuntimeError("no session token")

    monkeypatch.setattr("saltext.vcf.clients.hcx_manager.get_version", _boom)
    result = mod.installed("hcx.test")
    assert result["installed"] is False


def test_module_installed_reads_version_key_fallback(monkeypatch):
    monkeypatch.setattr(
        "saltext.vcf.clients.hcx_manager.get_version",
        lambda o, profile=None: {"version": "4.10.1"},
    )
    result = mod.installed("hcx.test")
    assert result["version"] == "4.10.1"


# --- state (reachable / verify path) ---------------------------------------


# HCX 9.x fast-path: unauth GET /hybridity/api/endpointInfo (proves the
# Apache frontend is up) AND admin-plane applianceConfiguration=true (proves
# the wizard has landed a vCenter registration). /hybridity/api/about needs
# SSO to already be configured — cannot be used pre-registration.


class _FakeAdminSess:
    def __init__(self, appliance_configuration=True):
        self._ac = appliance_configuration

    def get(self, url, timeout=None):
        class R:
            def __init__(self, ac):
                self.status_code = 200
                self._ac = ac

            def json(self):
                return self._ac

        return R(self._ac)

    def close(self):
        pass


def _mock_hcx_fast_path(monkeypatch, appliance_configuration=True):
    monkeypatch.setattr(
        "saltext.vcf.clients.hcx_manager.wait_for_setup_ready",
        lambda opts, timeout=5, poll_interval=5, profile=None: {"status": 200},
    )
    monkeypatch.setattr(
        "saltext.vcf.clients.hcx_manager._admin_login",
        lambda opts, admin_password, profile=None, timeout=30: (
            _FakeAdminSess(appliance_configuration=appliance_configuration),
            "https://hcx.test:9443",
        ),
    )


def test_state_installed_ok_when_reachable_and_registered(monkeypatch, inject_opts):
    inject_opts["pillar"]["saltext.vcf"]["hcx"] = {"host": "hcx.test", "password": "p"}
    _mock_hcx_fast_path(monkeypatch, appliance_configuration=True)
    ret = state.installed("hcx.test")
    assert ret["result"] is True
    assert ret["changes"] == {}
    assert "already installed" in ret["comment"]


def test_installed_present_noop(monkeypatch, inject_opts):
    """Reachable + registered = pure no-op, no deploy chain invoked."""
    inject_opts["pillar"]["saltext.vcf"]["hcx"] = {"host": "hcx.test", "password": "p"}
    _mock_hcx_fast_path(monkeypatch, appliance_configuration=True)
    # If deploy is called, that's a bug.
    monkeypatch.setattr(
        "saltext.vcf.modules.vcf_hcx.deploy",
        lambda *a, **kw: pytest.fail("deploy() should not run when reachable"),
    )
    ret = state.installed("hcx.test", deploy_spec={"ova_url": "unused"})
    assert ret["result"] is True
    assert ret["changes"] == {}


def test_installed_absent_no_deploy_spec_fails(monkeypatch):
    """Unreachable + no deploy spec = clear failure comment."""

    def _boom(o, profile=None):
        raise requests.ConnectionError("network down")

    monkeypatch.setattr("saltext.vcf.clients.hcx_manager.get_version", _boom)
    ret = state.installed("hcx.test")
    assert ret["result"] is False
    assert "not reachable" in ret["comment"]
    assert "no deploy_spec configured" in ret["comment"]


def test_state_installed_fails_on_session_runtime_error(monkeypatch):
    def _boom(o, profile=None):
        raise RuntimeError("no token")

    monkeypatch.setattr("saltext.vcf.clients.hcx_manager.get_version", _boom)
    ret = state.installed("hcx.test")
    assert ret["result"] is False


def test_installed_absent_deploy_spec_test_mode(monkeypatch, inject_opts):
    inject_opts["test"] = True

    def _boom(o, profile=None):
        raise requests.ConnectionError("nope")

    monkeypatch.setattr("saltext.vcf.clients.hcx_manager.get_version", _boom)
    spec = {"ova_url": "https://example.test/hcx.ova", "target_host": "esxi.test"}
    ret = state.installed("hcx.test", deploy_spec=spec)
    assert ret["result"] is None
    assert "Would deploy" in ret["comment"]
    assert "https://example.test/hcx.ova" in ret["comment"]
    assert ret["changes"]["plan"] == "deploy_hcx_manager"
    assert ret["changes"]["ova_url"] == spec["ova_url"]


def test_installed_absent_deploy_spec_real_mode(monkeypatch, inject_opts):
    """Unreachable / not-yet-registered → deploy() runs + post-deploy probe."""
    inject_opts["pillar"]["saltext.vcf"]["hcx"] = {"host": "hcx.test", "password": "p"}

    # Fast-path probe fails (applianceConfiguration is not True yet).
    _mock_hcx_fast_path(monkeypatch, appliance_configuration=False)

    deploy_calls = {}

    def _deploy(spec, profile=None):
        deploy_calls["spec"] = spec
        deploy_calls["profile"] = profile
        # After deploy, flip the fast-path to already-configured so the
        # post-deploy verification sees applianceConfiguration=true.
        _mock_hcx_fast_path(monkeypatch, appliance_configuration=True)
        return {
            "deploy": {"vm_name": "hcx-mgr", "powered_on": True},
            "activate": {"already_activated": True},
            "vcenter": {"vcenter": True, "applianceConfiguration": True},
        }

    monkeypatch.setattr(
        "saltext.vcf.states.vcf_hcx.__salt__", {"vcf_hcx.deploy": _deploy}, raising=False
    )

    spec = {
        "ova_url": "https://example.test/hcx.ova",
        "target_host": "esxi.test",
        "target_user": "root",
        "target_password": "p",
        "vm_name": "hcx-mgr",
        "activation_key": "KEY",
        "vcenter_url": "https://vc.test",
        "vcenter_username": "administrator@vsphere.local",
        "vcenter_password": "p",
    }
    ret = state.installed("hcx.test", deploy_spec=spec)
    assert ret["result"] is True
    assert deploy_calls["spec"] is spec
    assert "deployed" in ret["comment"] or "already installed" in ret["comment"]
