"""Tests for the vcf_avi execution & state modules.

Covers the verify-only fast path (Controller reachable => no-op), the
deploy-on-absence path (Controller absent + deploy_spec => OVA push +
wizard + optional cluster + final version probe), and the failure path
(absent + no deploy_spec => result=False).
"""

import pytest

from saltext.vcf.clients import avi_controller as c
from saltext.vcf.modules import vcf_avi as mod
from saltext.vcf.states import vcf_avi as state


@pytest.fixture
def avi_opts():
    return {
        "pillar": {
            "saltext.vcf": {
                "avi": {
                    "host": "alb.test",
                    "username": "admin",
                    "password": "p",
                    "verify_ssl": False,
                },
            },
        },
        "test": False,
    }


@pytest.fixture
def avi_opts_with_deploy_spec():
    return {
        "pillar": {
            "saltext.vcf": {
                "avi": {
                    "host": "alb.test",
                    "username": "admin",
                    "password": "p",
                    "verify_ssl": False,
                    "deploy_spec": {
                        "ova_url": "/tmp/controller.ova",
                        "vm_name": "alb-1",
                        "target_host": "esxi.test",
                        "target_user": "root",
                        "target_password": "esxi-pw",
                        "admin_password": "new-admin",
                        "dns_servers": ["10.0.0.53"],
                        "ntp_servers": ["pool.ntp.org"],
                        "backup_passphrase": "bp",
                    },
                },
            },
        },
        "test": False,
    }


@pytest.fixture(autouse=True)
def _inject_opts(monkeypatch, avi_opts):
    monkeypatch.setattr(mod, "__opts__", avi_opts, raising=False)
    monkeypatch.setattr(state, "__opts__", avi_opts, raising=False)

    class _DynamicSalt(dict):
        def __getitem__(self, key):
            if key == "vcf_avi.deploy":
                return mod.deploy
            return super().__getitem__(key)

    monkeypatch.setattr(state, "__salt__", _DynamicSalt(), raising=False)


# ---------------------------------------------------------------------------
# Execution module
# ---------------------------------------------------------------------------


def test_module_get_version_delegates(monkeypatch):
    monkeypatch.setattr(c, "get_version", lambda o, profile=None: {"Version": "22.1.3"})
    assert mod.get_version() == {"Version": "22.1.3"}


def test_module_ping_true(monkeypatch):
    monkeypatch.setattr(c, "ping", lambda o, profile=None: True)
    assert mod.ping() is True


def test_module_installed_success(monkeypatch):
    monkeypatch.setattr(c, "get_version", lambda o, profile=None: {"Version": "22.1.3", "build": 9})
    result = mod.installed(name="alb-prod")
    assert result == {
        "installed": True,
        "version": {"Version": "22.1.3", "build": 9},
        "error": None,
    }


def test_module_installed_swallows_error(monkeypatch):
    def _boom(*a, **kw):
        raise RuntimeError("controller down")

    monkeypatch.setattr(c, "get_version", _boom)
    result = mod.installed(name="alb-prod")
    assert result["installed"] is False
    assert result["version"] is None
    assert "controller down" in result["error"]


def _stub_find_vm_absent(monkeypatch):
    """Common mock: no existing VM on target, so deploy proceeds to push."""
    from saltext.vcf.clients import ovf_deploy as _ovf

    monkeypatch.setattr(_ovf, "find_vm", lambda **kw: None)


def test_module_deploy_drives_full_chain(monkeypatch):
    """deploy() must call OVA push, wait, wizard, then final version probe."""
    calls = []
    _stub_find_vm_absent(monkeypatch)

    monkeypatch.setattr(
        c, "deploy_ova", lambda spec: (calls.append(("ova", spec)), {"vm_name": "alb-1"})[1]
    )
    monkeypatch.setattr(
        c,
        "wait_for_setup_ready",
        lambda opts, timeout=1800, poll_interval=15, profile=None: calls.append(
            ("wait", timeout, poll_interval)
        ),
    )
    monkeypatch.setattr(
        c,
        "bootstrap_wizard",
        lambda opts, **kw: (calls.append(("wizard", kw)), {"ok": True})[1],
    )
    monkeypatch.setattr(
        c,
        "get_version",
        lambda opts, profile=None: (calls.append(("version",)), {"Version": "22.1.3"})[1],
    )

    spec = {
        "ova_url": "/tmp/a.ova",
        "vm_name": "alb-1",
        "target_host": "esxi.test",
        "target_user": "root",
        "target_password": "pw",
        "default_password": "ova-baked",
        "admin_password": "adm",
        "backup_passphrase": "bp",
        "dns_servers": ["10.0.0.53"],
        "ntp_servers": ["pool.ntp.org"],
    }
    result = mod.deploy(spec)
    assert result["deployed"] is True
    assert result["version"] == {"Version": "22.1.3"}
    # Order of operations matters: ova, wait, wizard, version.
    assert [c[0] for c in calls] == ["ova", "wait", "wizard", "version"]
    wizard_kwargs = dict(calls[2][1])
    assert wizard_kwargs["default_password"] == "ova-baked"
    assert wizard_kwargs["new_password"] == "adm"
    assert wizard_kwargs["backup_passphrase"] == "bp"
    assert wizard_kwargs["dns_servers"] == ["10.0.0.53"]


def test_module_deploy_includes_cluster_when_requested(monkeypatch):
    calls = []
    _stub_find_vm_absent(monkeypatch)
    monkeypatch.setattr(c, "deploy_ova", lambda spec: {})
    monkeypatch.setattr(c, "wait_for_setup_ready", lambda *a, **kw: None)
    monkeypatch.setattr(c, "bootstrap_wizard", lambda *a, **kw: {})
    monkeypatch.setattr(
        c,
        "configure_cluster",
        lambda opts, nodes, cluster_ip=None, profile=None: (
            calls.append(("cluster", nodes, cluster_ip)),
            {"ok": True},
        )[1],
    )
    monkeypatch.setattr(c, "get_version", lambda *a, **kw: {"Version": "22.1.3"})

    spec = {
        "ova_url": "/x.ova",
        "vm_name": "n",
        "target_host": "h",
        "target_user": "u",
        "target_password": "p",
        "default_password": "d",
        "admin_password": "a",
        "backup_passphrase": "b",
        "cluster_nodes": [{"name": "n1", "ip": "10.0.0.11"}],
        "cluster_ip": "10.0.0.10",
    }
    mod.deploy(spec)
    assert calls == [("cluster", [{"name": "n1", "ip": "10.0.0.11"}], "10.0.0.10")]


# ---------------------------------------------------------------------------
# State module
# ---------------------------------------------------------------------------


def test_installed_present_noop(monkeypatch):
    """Controller reachable => result=True, changes={}, no deploy invoked."""
    monkeypatch.setattr(c, "ping", lambda o, profile=None: True)

    def _blowup(*a, **kw):
        raise AssertionError("deploy path must not run when controller is up")

    monkeypatch.setattr(mod, "deploy", _blowup)
    ret = state.installed("alb-prod")
    assert ret["result"] is True
    assert ret["changes"] == {}
    assert "already reachable" in ret["comment"]


def test_installed_absent_no_deploy_spec_fails(monkeypatch):
    """Absent + no deploy_spec => result=False."""
    monkeypatch.setattr(c, "ping", lambda o, profile=None: False)
    ret = state.installed("alb-prod")
    assert ret["result"] is False
    assert ret["changes"] == {}
    assert "not reachable" in ret["comment"]
    assert "no deploy_spec configured" in ret["comment"]


def test_installed_absent_deploy_spec_test_mode(monkeypatch, avi_opts_with_deploy_spec):
    """test=True + deploy_spec => result=None, 'Would deploy...' comment."""
    monkeypatch.setattr(state, "__opts__", {**avi_opts_with_deploy_spec, "test": True})
    monkeypatch.setattr(c, "ping", lambda o, profile=None: False)

    def _blowup(*a, **kw):
        raise AssertionError("deploy must not run in test mode")

    monkeypatch.setattr(mod, "deploy", _blowup)
    ret = state.installed("alb-prod")
    assert ret["result"] is None
    assert "Would deploy" in ret["comment"]
    assert "/tmp/controller.ova" in ret["comment"]
    assert ret["changes"] == {"plan": "deploy_avi_controller", "ova": "/tmp/controller.ova"}


def test_installed_absent_deploy_spec_real_mode(monkeypatch, avi_opts_with_deploy_spec):
    """Mock deploy chain end-to-end and verify each hop is invoked in order."""
    monkeypatch.setattr(state, "__opts__", avi_opts_with_deploy_spec)
    monkeypatch.setattr(mod, "__opts__", avi_opts_with_deploy_spec)
    monkeypatch.setattr(c, "ping", lambda o, profile=None: False)
    _stub_find_vm_absent(monkeypatch)
    # AVI deploy_spec fixture doesn't include default_password by default;
    # the new bootstrap_wizard requires it, so seed the pillar.
    avi_opts_with_deploy_spec["pillar"]["saltext.vcf"]["avi"]["deploy_spec"][
        "default_password"
    ] = "ova-default"

    order = []
    monkeypatch.setattr(
        c,
        "deploy_ova",
        lambda spec: (
            order.append(("deploy_ova", spec.get("vm_name"))),
            {"vm_name": spec["vm_name"]},
        )[1],
    )
    monkeypatch.setattr(
        c,
        "wait_for_setup_ready",
        lambda opts, timeout=1800, poll_interval=15, profile=None: order.append(
            ("wait_for_setup_ready",)
        ),
    )
    monkeypatch.setattr(
        c,
        "bootstrap_wizard",
        lambda opts, **kw: (order.append(("bootstrap_wizard", kw["new_password"])), {"ok": True})[
            1
        ],
    )
    monkeypatch.setattr(
        c,
        "get_version",
        lambda opts, profile=None: (order.append(("get_version",)), {"Version": "22.1.3"})[1],
    )

    ret = state.installed("alb-prod")
    assert ret["result"] is True
    assert [step[0] for step in order] == [
        "deploy_ova",
        "wait_for_setup_ready",
        "bootstrap_wizard",
        "get_version",
    ]
    assert order[0][1] == "alb-1"
    assert order[2][1] == "new-admin"
    assert ret["changes"] == {"deployed": "22.1.3"}
    assert "22.1.3" in ret["comment"]


def test_installed_absent_deploy_failure_bubbles_up(monkeypatch, avi_opts_with_deploy_spec):
    """Deploy raises RuntimeError => state returns result=False with the message."""
    monkeypatch.setattr(state, "__opts__", avi_opts_with_deploy_spec)
    monkeypatch.setattr(mod, "__opts__", avi_opts_with_deploy_spec)
    monkeypatch.setattr(c, "ping", lambda o, profile=None: False)

    def _boom(*a, **kw):
        raise RuntimeError("OVA push exploded")

    monkeypatch.setattr(mod, "deploy", _boom)
    ret = state.installed("alb-prod")
    assert ret["result"] is False
    assert "OVA push exploded" in ret["comment"]


def test_installed_deploy_spec_arg_overrides_pillar(monkeypatch):
    """Explicit deploy_spec kwarg wins over pillar."""
    monkeypatch.setattr(c, "ping", lambda o, profile=None: False)
    captured = {}
    monkeypatch.setattr(
        mod,
        "deploy",
        lambda spec, profile=None: (captured.update(spec=spec), {"version": {"Version": "22.1.3"}})[
            1
        ],
    )
    ret = state.installed(
        "alb-prod",
        deploy_spec={
            "ova_url": "/tmp/override.ova",
            "vm_name": "alb-x",
            "target_host": "esxi.test",
            "target_user": "root",
            "target_password": "pw",
            "admin_password": "a",
            "backup_passphrase": "b",
        },
    )
    assert ret["result"] is True
    assert captured["spec"]["ova_url"] == "/tmp/override.ova"


def test_installed_passes_profile(monkeypatch):
    seen = {}

    def _capture(opts, profile=None):
        seen["profile"] = profile
        return True

    monkeypatch.setattr(c, "ping", _capture)
    ret = state.installed("alb-prod", profile="alt")
    assert ret["result"] is True
    assert seen["profile"] == "alt"
