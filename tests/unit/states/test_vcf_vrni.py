"""Tests for the VRNI state module — deploy-on-absence ``installed``."""

import pytest
import requests

from saltext.vcf.clients import vrni_platform as c
from saltext.vcf.modules import vcf_vrni as mod
from saltext.vcf.states import vcf_vrni as state


@pytest.fixture(autouse=True)
def inject_opts(monkeypatch, opts):
    monkeypatch.setattr(state, "__opts__", opts, raising=False)
    monkeypatch.setattr(mod, "__opts__", opts, raising=False)

    # State dispatches to the deploy exec-module via ``__salt__["vcf_vrni.deploy"]``.
    # Route it back to the imported module so tests that patch ``mod.deploy`` or its
    # internals (e.g. ``_push_ova``) still take effect.
    class _DynamicSalt(dict):
        def __getitem__(self, key):
            if key == "vcf_vrni.deploy":
                return mod.deploy
            return super().__getitem__(key)

    monkeypatch.setattr(state, "__salt__", _DynamicSalt(), raising=False)


def _conn_error(*_a, **_kw):
    raise requests.ConnectionError("no route to host")


@pytest.fixture
def deploy_spec():
    return {
        "platform": {
            "ova_url": "/tmp/vrni-platform.ova",
            "target_host": "esx-1.lab.local",
            "target_user": "root",
            "target_password": "secret",
            "vm_name": "vrni-platform-01",
            "ovf_properties": {"role": "Platform"},
        },
        "wizard": {
            "admin_password": "AdminP@ss",
            "admin_email": "netops@example.com",
            "license_key": "AAAA-BBBB-CCCC-DDDD",
            "ntp_servers": ["ntp.example.com"],
        },
        "collectors": [
            {
                "ova_url": "/tmp/vrni-collector.ova",
                "target_host": "esx-2.lab.local",
                "target_user": "root",
                "target_password": "secret",
                "vm_name": "vrni-collector-01",
            },
            {
                "ova_url": "/tmp/vrni-collector.ova",
                "target_host": "esx-3.lab.local",
                "target_user": "root",
                "target_password": "secret",
                "vm_name": "vrni-collector-02",
            },
        ],
    }


def test_installed_success_no_version_constraint(monkeypatch):
    monkeypatch.setattr(
        c,
        "get_version",
        lambda o, profile=None: {"version": "6.14.0", "api_version": "1.5.0"},
    )
    ret = state.installed("vrni-prod")
    assert ret["result"] is True
    assert ret["changes"] == {}
    assert "6.14.0" in ret["comment"]


def test_installed_success_with_min_version(monkeypatch):
    monkeypatch.setattr(
        c,
        "get_version",
        lambda o, profile=None: {"version": "6.14.0"},
    )
    ret = state.installed("vrni-prod", min_version="6.14.0")
    assert ret["result"] is True
    assert ">= 6.14.0" in ret["comment"]


def test_installed_fails_below_min_version(monkeypatch):
    monkeypatch.setattr(
        c,
        "get_version",
        lambda o, profile=None: {"version": "6.10.0"},
    )
    ret = state.installed("vrni-prod", min_version="6.14.0")
    assert ret["result"] is False
    assert "below required" in ret["comment"]
    assert "6.10.0" in ret["comment"]


def test_installed_falls_back_to_api_version(monkeypatch):
    """Some Platform builds omit ``version``; the state must probe ``api_version``."""
    monkeypatch.setattr(
        c,
        "get_version",
        lambda o, profile=None: {"api_version": "1.5.0"},
    )
    ret = state.installed("vrni-prod")
    assert ret["result"] is True
    assert "1.5.0" in ret["comment"]


def test_installed_fails_when_platform_unreachable(monkeypatch):
    def _boom(*_a, **_kw):
        raise requests.ConnectionError("no route to host")

    monkeypatch.setattr(c, "get_version", _boom)
    ret = state.installed("vrni-prod")
    assert ret["result"] is False
    assert "not reachable" in ret["comment"]
    assert "no route to host" in ret["comment"]


def test_installed_fails_on_http_error(monkeypatch):
    fake_resp = requests.Response()
    fake_resp.status_code = 500

    def _boom(*_a, **_kw):
        raise requests.HTTPError("500 Server Error", response=fake_resp)

    monkeypatch.setattr(c, "get_version", _boom)
    ret = state.installed("vrni-prod")
    assert ret["result"] is False
    assert "not reachable" in ret["comment"]


def test_installed_passes_profile_through(monkeypatch):
    seen = {}

    def _fake(o, profile=None):
        seen["profile"] = profile
        return {"version": "6.14.0"}

    monkeypatch.setattr(c, "get_version", _fake)
    ret = state.installed("vrni-prod", profile="alt")
    assert ret["result"] is True
    assert seen["profile"] == "alt"


# ---------------------------------------------------------------------------
# Deploy-on-absence tests
# ---------------------------------------------------------------------------


def test_installed_present_noop(monkeypatch, deploy_spec):
    """When the Platform is reachable, deploy is NEVER attempted."""
    monkeypatch.setattr(c, "get_version", lambda o, profile=None: {"version": "6.14.0"})
    called = {"deploy": 0}
    monkeypatch.setattr(
        mod,
        "deploy",
        lambda *_a, **_kw: called.__setitem__("deploy", called["deploy"] + 1) or {},
    )
    ret = state.installed("vrni-prod", deploy_spec=deploy_spec)
    assert ret["result"] is True
    assert called["deploy"] == 0
    assert "6.14.0" in ret["comment"]


def test_installed_absent_deploy_spec_test_mode_describes_plan(monkeypatch, opts, deploy_spec):
    monkeypatch.setattr(c, "get_version", _conn_error)
    called = {"deploy": 0}
    monkeypatch.setattr(
        mod,
        "deploy",
        lambda *_a, **_kw: called.__setitem__("deploy", called["deploy"] + 1) or {},
    )
    opts["test"] = True
    try:
        ret = state.installed("vrni-prod", deploy_spec=deploy_spec)
    finally:
        opts["test"] = False
    assert ret["result"] is None
    assert called["deploy"] == 0
    assert "Platform OVA" in ret["comment"]
    assert "Collector OVA" in ret["comment"]
    assert ret["changes"]["plan"] == "deploy_vrni"
    assert ret["changes"]["platform_vm_name"] == "vrni-platform-01"
    assert ret["changes"]["collector_count"] == 2


def test_installed_absent_deploy_spec_real_mode(monkeypatch, deploy_spec):
    """Deploy end-to-end: Platform OVA + wizard + 2 Collectors joined via shared secret."""
    monkeypatch.setattr(c, "get_version", _conn_error)

    # Mock every external side-effect: OVA push, wizard wait/complete, shared-secret mint.
    ova_calls = []

    def fake_push_ova(spec, *, role):
        ova_calls.append((role, spec["vm_name"], (spec.get("ovf_properties") or {})))
        return {"role": role, "vm_name": spec["vm_name"], "vm_moid": f"vm-{len(ova_calls)}"}

    monkeypatch.setattr(mod, "_push_ova", fake_push_ova)

    wait_calls = {"n": 0}
    monkeypatch.setattr(
        c, "wait_for_setup_ready", lambda *a, **kw: wait_calls.__setitem__("n", wait_calls["n"] + 1)
    )

    wizard_calls = []
    monkeypatch.setattr(
        c,
        "complete_setup",
        lambda o, **kw: wizard_calls.append(kw) or {"status": "SETUP_COMPLETE"},
    )
    monkeypatch.setattr(c, "get_shared_secret", lambda o, profile=None: "sh4red-abc")

    ret = state.installed("vrni-prod", deploy_spec=deploy_spec)
    assert ret["result"] is True, ret
    # Platform + 2 collectors ⇒ 3 OVA pushes total.
    assert [role for role, *_ in ova_calls] == ["platform", "collector", "collector"]
    # Both collectors got the shared secret in their OVF properties.
    for role, _name, props in ova_calls:
        if role == "collector":
            assert props["Proxy_Shared_Secret"] == "sh4red-abc"
    # Wizard bootstrap fired exactly once.
    assert wait_calls["n"] == 1
    assert len(wizard_calls) == 1
    assert wizard_calls[0]["admin_password"] == "AdminP@ss"
    assert wizard_calls[0]["license_key"] == "AAAA-BBBB-CCCC-DDDD"
    # Changes surface the deploy summary.
    assert ret["changes"] == {
        "platform_deployed": True,
        "collectors_deployed": 2,
    }
    assert "Platform + 2 Collector" in ret["comment"]


def test_installed_absent_no_deploy_spec_fails(monkeypatch):
    monkeypatch.setattr(c, "get_version", _conn_error)
    ret = state.installed("vrni-prod")
    assert ret["result"] is False
    assert "not reachable" in ret["comment"]
    assert "deploy_spec" in ret["comment"]


def test_installed_deploy_spec_from_pillar(monkeypatch, opts, deploy_spec):
    """When the arg is omitted, pillar 'saltext.vcf:vrni:deploy_spec' is used."""
    opts["pillar"]["saltext.vcf"].setdefault("vrni", {})["deploy_spec"] = deploy_spec
    monkeypatch.setattr(c, "get_version", _conn_error)
    opts["test"] = True
    try:
        ret = state.installed("vrni-prod")
    finally:
        opts["test"] = False
    assert ret["result"] is None
    assert ret["changes"]["collector_count"] == 2


def test_installed_deploy_raises_becomes_result_false(monkeypatch, deploy_spec):
    """Any TimeoutError / RuntimeError from deploy is surfaced as result=False."""
    monkeypatch.setattr(c, "get_version", _conn_error)

    def _boom(*_a, **_kw):
        raise TimeoutError("wizard never came up")

    monkeypatch.setattr(mod, "deploy", _boom)
    ret = state.installed("vrni-prod", deploy_spec=deploy_spec)
    assert ret["result"] is False
    assert "wizard never came up" in ret["comment"]
