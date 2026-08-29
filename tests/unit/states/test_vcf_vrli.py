"""Tests for the vcf_vrli state (verify + single-node deploy-on-absence).

The deploy path exercises the *real* vRLI 9.0.2 first-run wizard flow:

* OVA push (delegated to the module's ``_push_ova`` — mocked here).
* Wait for the wizard root to become reachable.
* 3-call CSRF form flow: ``GET /csrf`` → ``POST /login`` (``authMethod``
  ``=DEFAULT``) → ``POST /admin/startup`` (``_eventName=newDeployment``).
* Optional SSH ``li-reset-admin-passwd.sh`` shortcut.
* Verify with ``GET /api/v2/version``.

See ``~/src/saltext-opsdev/VRLI-MANUAL-INSTALL-NOTES.md`` for the
manual-install transcript this covers. Worker/cluster join is out of
scope for the MVP and is not exercised.
"""

import pytest
import requests

from saltext.vcf.clients import vrli_master
from saltext.vcf.modules import vcf_vrli as vrli_module
from saltext.vcf.states import vcf_vrli as vrli_state


@pytest.fixture
def vrli_opts():
    return {
        "pillar": {
            "saltext.vcf": {
                "vrli": {
                    "host": "vrli.test",
                    "username": "admin",
                    "password": "p",
                    "verify_ssl": False,
                },
            },
        },
        "test": False,
    }


@pytest.fixture(autouse=True)
def _inject_opts(monkeypatch, vrli_opts):
    monkeypatch.setattr(vrli_state, "__opts__", vrli_opts, raising=False)
    monkeypatch.setattr(vrli_module, "__opts__", vrli_opts, raising=False)

    # The state now dispatches deploy via __salt__["vcf_vrli.deploy"] so Salt's
    # loader injects __opts__ into module functions. In unit tests we don't
    # have the loader — wire the dunder to look up the module function each
    # call so test-time ``monkeypatch.setattr(vrli_module, "deploy", ...)``
    # rewrites take effect.
    class _DynamicSalt(dict):
        def __getitem__(self, key):
            if key == "vcf_vrli.deploy":
                return vrli_module.deploy
            return super().__getitem__(key)

    monkeypatch.setattr(vrli_state, "__salt__", _DynamicSalt(), raising=False)


def _mk_http_error(status):
    resp = requests.Response()
    resp.status_code = status
    return requests.HTTPError(f"{status} error", response=resp)


# ---------------------------------------------------------------------------
# Verify-only paths
# ---------------------------------------------------------------------------


def test_installed_success_no_version_check(monkeypatch):
    monkeypatch.setattr(
        vrli_master,
        "get_version",
        lambda o, profile=None: {"releaseName": "VMware Log Insight 8.18.0", "version": "8.18.0"},
    )
    ret = vrli_state.installed("vrli-master.example.test")
    assert ret["result"] is True
    assert ret["changes"] == {}
    assert "installed and reachable" in ret["comment"]
    assert "8.18.0" in ret["comment"]


def test_installed_idempotent_repeated_calls(monkeypatch):
    """Two consecutive runs both no-op — verify-only ⇒ never mutates state."""
    calls = []
    monkeypatch.setattr(
        vrli_master,
        "get_version",
        lambda o, profile=None: calls.append(True) or {"version": "8.18.0"},
    )
    r1 = vrli_state.installed("vrli-master")
    r2 = vrli_state.installed("vrli-master")
    assert r1["result"] is True and r1["changes"] == {}
    assert r2["result"] is True and r2["changes"] == {}
    assert len(calls) == 2


def test_installed_version_match(monkeypatch):
    monkeypatch.setattr(
        vrli_master,
        "get_version",
        lambda o, profile=None: {"version": "8.18.0"},
    )
    ret = vrli_state.installed("vrli-master", version="8.18.0")
    assert ret["result"] is True
    assert ret["changes"] == {}


def test_installed_version_drift_fails_state(monkeypatch):
    monkeypatch.setattr(
        vrli_master,
        "get_version",
        lambda o, profile=None: {"version": "8.17.0", "releaseName": "old"},
    )
    ret = vrli_state.installed("vrli-master", version="8.18.0")
    assert ret["result"] is False
    assert ret["changes"] == {}
    assert "expected '8.18.0'" in ret["comment"]
    assert "8.17.0" in ret["comment"]
    assert "out of scope" in ret["comment"]


def test_installed_master_unreachable_fails_state(monkeypatch):
    def _boom(o, profile=None):
        raise requests.exceptions.ConnectionError("no route to host")

    monkeypatch.setattr(vrli_master, "get_version", _boom)
    ret = vrli_state.installed("vrli-master.example.test")
    assert ret["result"] is False
    assert ret["changes"] == {}
    assert "cannot reach vRLI master" in ret["comment"]
    # Without a deploy_spec, the state can't self-heal — actionable hint expected.
    assert "no 'deploy_spec' was provided" in ret["comment"]


def test_installed_missing_pillar_host_fails_state(monkeypatch):
    def _boom(o, profile=None):
        raise RuntimeError("saltext.vcf.vrli.host is not configured; cannot reach vRLI master")

    monkeypatch.setattr(vrli_master, "get_version", _boom)
    ret = vrli_state.installed("vrli-master")
    assert ret["result"] is False
    assert "not configured" in ret["comment"]
    assert "no 'deploy_spec' was provided" in ret["comment"]


def test_installed_master_returns_5xx_fails_state(monkeypatch):
    def _boom(o, profile=None):
        raise _mk_http_error(503)

    monkeypatch.setattr(vrli_master, "get_version", _boom)
    ret = vrli_state.installed("vrli-master")
    assert ret["result"] is False
    assert "cannot reach vRLI master" in ret["comment"]


def test_installed_test_mode_reachable_is_noop(monkeypatch, vrli_opts):
    """Test-mode + reachable master ⇒ still a no-op (verify is non-destructive)."""
    vrli_opts["test"] = True
    monkeypatch.setattr(vrli_state, "__opts__", vrli_opts)
    monkeypatch.setattr(
        vrli_master,
        "get_version",
        lambda o, profile=None: {"version": "8.18.0"},
    )
    ret = vrli_state.installed("vrli-master", version="8.18.0")
    assert ret["result"] is True
    assert ret["changes"] == {}
    assert "installed and reachable" in ret["comment"]


def test_installed_passes_profile_through(monkeypatch):
    seen = {}

    def _get(o, profile=None):
        seen["profile"] = profile
        return {"version": "8.18.0"}

    monkeypatch.setattr(vrli_master, "get_version", _get)
    vrli_state.installed("vrli-master", profile="alt")
    assert seen["profile"] == "alt"


# ---------------------------------------------------------------------------
# Deploy-on-absence coverage (single-node MVP)
# ---------------------------------------------------------------------------


def _sample_master_spec():
    """A minimal single-node deploy spec mirroring the manual-install shape."""
    return {
        "ova_source": "/tmp/Operations-Logs-Appliance.ova",
        "vm_name": "vrli",
        "target_host": "vc.example.test",
        "target_user": "administrator@vsphere.local",
        "target_password": "vc-pw",
        "datastore": "mgmt-mgmt-anchor-vc",
        "network_map": {"Network 1": "mgmt-mgmt-anchor-vds01-vm-mgmt"},
        "ovf_properties": {
            "hostname": "vrli-25-0-0-60",
            "rootpw": "VMware123!VMware123!",
            # Note: fully-qualified <classId>.<instanceId>.<key> form is
            # required; bare keys are silently dropped by the OVF importer.
            "vami.VMware_vCenter_Log_Insight.ip0": "25.0.0.60",
        },
        "deployment_option": "xsmall",
        "disk_provisioning": "thin",
        "admin_password": "VMware123!VMware123!",
    }


def test_installed_present_noop(monkeypatch):
    """Reachable master ⇒ no deploy is attempted even if a deploy_spec is set."""
    monkeypatch.setattr(
        vrli_master,
        "get_version",
        lambda o, profile=None: {"version": "8.18.0", "releaseName": "VMware Log Insight 8.18.0"},
    )

    def _boom(*_a, **_kw):  # deploy must not be called
        raise AssertionError("deploy called for already-reachable master")

    monkeypatch.setattr(vrli_module, "deploy", _boom)
    ret = vrli_state.installed("vrli-master", deploy_spec=_sample_master_spec())
    assert ret["result"] is True
    assert ret["changes"] == {}
    assert "installed and reachable" in ret["comment"]


def test_installed_absent_deploy_spec_test_mode(monkeypatch, vrli_opts):
    def _unreach(o, profile=None):
        raise requests.exceptions.ConnectionError("no route to host")

    monkeypatch.setattr(vrli_master, "get_version", _unreach)

    def _boom(*_a, **_kw):
        raise AssertionError("deploy called in test-mode")

    monkeypatch.setattr(vrli_module, "deploy", _boom)

    vrli_opts["test"] = True
    monkeypatch.setattr(vrli_state, "__opts__", vrli_opts)
    ret = vrli_state.installed("vrli-master", deploy_spec=_sample_master_spec())
    assert ret["result"] is None
    assert ret["changes"] == {}
    assert "would deploy single-node vRLI master" in ret["comment"]
    assert "'vrli'" in ret["comment"]  # vm_name from _sample_master_spec
    assert "'vc.example.test'" in ret["comment"]  # target_host


def test_installed_absent_deploy_spec_real_mode_single_node(monkeypatch):
    def _unreach(o, profile=None):
        raise requests.exceptions.ConnectionError("no route to host")

    monkeypatch.setattr(vrli_master, "get_version", _unreach)

    seen = {}

    def fake_deploy(spec, profile=None):
        seen["spec"] = spec
        seen["profile"] = profile
        return {
            "ova": {},
            "wizard": {"csrf_token": "t", "login": {}, "startup": {}},
            "admin_password_reset": False,
            "version": {"version": "9.0.2.0.25575214", "releaseName": "Nightly"},
        }

    monkeypatch.setattr(vrli_module, "deploy", fake_deploy)

    ret = vrli_state.installed("vrli-master", deploy_spec=_sample_master_spec())
    assert ret["result"] is True
    assert ret["changes"] == {"master_deployed": True, "admin_password_reset": False}
    assert "single-node master bootstrapped" in ret["comment"]
    assert "9.0.2.0.25575214" in ret["comment"]
    assert seen["spec"]["vm_name"] == "vrli"


def test_installed_absent_deploy_spec_real_mode_reports_ssh_reset(monkeypatch):
    def _unreach(o, profile=None):
        raise requests.exceptions.ConnectionError("no route to host")

    monkeypatch.setattr(vrli_master, "get_version", _unreach)

    def fake_deploy(spec, profile=None):
        return {
            "ova": {},
            "wizard": {"csrf_token": "t"},
            "admin_password_reset": True,
            "version": {"version": "9.0.2.0"},
        }

    monkeypatch.setattr(vrli_module, "deploy", fake_deploy)
    ret = vrli_state.installed("vrli-master", deploy_spec=_sample_master_spec())
    assert ret["result"] is True
    assert ret["changes"]["admin_password_reset"] is True


def test_installed_absent_no_deploy_spec_fails(monkeypatch):
    def _unreach(o, profile=None):
        raise requests.exceptions.ConnectionError("no route to host")

    monkeypatch.setattr(vrli_master, "get_version", _unreach)

    def _boom(*_a, **_kw):
        raise AssertionError("deploy called with no spec")

    monkeypatch.setattr(vrli_module, "deploy", _boom)

    ret = vrli_state.installed("vrli-master")
    assert ret["result"] is False
    assert ret["changes"] == {}
    assert "no 'deploy_spec' was provided" in ret["comment"]
    assert "cannot reach vRLI master" in ret["comment"]


def test_installed_deploy_spec_from_pillar(monkeypatch, vrli_opts):
    """When deploy_spec arg is absent, pillar saltext.vcf:vrli:deploy_spec wins."""

    def _unreach(o, profile=None):
        raise requests.exceptions.ConnectionError("no route to host")

    monkeypatch.setattr(vrli_master, "get_version", _unreach)

    seen = {}

    def fake_deploy(spec, profile=None):
        seen["spec"] = spec
        return {"ova": {}, "wizard": {}, "admin_password_reset": False, "version": None}

    monkeypatch.setattr(vrli_module, "deploy", fake_deploy)
    vrli_opts["pillar"]["saltext.vcf"]["vrli"]["deploy_spec"] = _sample_master_spec()
    monkeypatch.setattr(vrli_state, "__opts__", vrli_opts)

    ret = vrli_state.installed("vrli-master")
    assert ret["result"] is True
    assert seen["spec"]["vm_name"] == "vrli"


def test_installed_deploy_raises_maps_to_failed_state(monkeypatch):
    def _unreach(o, profile=None):
        raise requests.exceptions.ConnectionError("no route to host")

    monkeypatch.setattr(vrli_master, "get_version", _unreach)

    def fake_deploy(spec, profile=None):
        raise TimeoutError("wizard never came up")

    monkeypatch.setattr(vrli_module, "deploy", fake_deploy)

    ret = vrli_state.installed("vrli-master", deploy_spec=_sample_master_spec())
    assert ret["result"] is False
    assert "vRLI deploy failed" in ret["comment"]
    assert "wizard never came up" in ret["comment"]
