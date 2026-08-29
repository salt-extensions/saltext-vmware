"""Tests for :mod:`saltext.vcf.states.vcf_vro_bootstrap`."""

import pytest

from saltext.vcf.states import vcf_vro_bootstrap as s


@pytest.fixture
def bootstrap_opts(opts):
    opts["pillar"]["saltext.vcf"]["vro"] = {
        "host": "25.0.3.189",
        "password": "VMware123!VMware123!",
    }
    return opts


@pytest.fixture(autouse=True)
def inject_opts(monkeypatch, bootstrap_opts):
    monkeypatch.setattr(s, "__opts__", bootstrap_opts, raising=False)
    yield


def test_test_mode_describes_plan(monkeypatch, bootstrap_opts):
    bootstrap_opts["test"] = True
    monkeypatch.setattr(s, "__opts__", bootstrap_opts)
    r = s.remediate("vro")
    assert r["result"] is None
    assert "Would run" in r["comment"]


def test_short_circuit_when_already_serving(monkeypatch):
    def fake_remediate(**kw):
        return {
            "ok": True,
            "short_circuit": True,
            "steps": {},
            "verify": {"ok": True, "status_code": 200, "version": "9.0.2"},
        }

    monkeypatch.setattr(
        s, "__salt__", {"vcf_vro_bootstrap.remediate": fake_remediate}, raising=False
    )
    r = s.remediate("vro")
    assert r["result"] is True
    assert r["changes"] == {}
    assert "already serving" in r["comment"]


def test_remediation_success_reports_fired_steps(monkeypatch):
    def fake_remediate(**kw):
        return {
            "ok": True,
            "steps": {
                "fix_hosts": {"changed": True, "reason": "rewrote"},
                "rebootstrap_kubelet": {"changed": False, "reason": "Ready"},
                "ensure_envoy_dnat": {"changed": True, "reason": "installed"},
            },
            "verify": {"ok": True, "status_code": 200, "version": "9.0.2.0.25676793"},
        }

    monkeypatch.setattr(
        s, "__salt__", {"vcf_vro_bootstrap.remediate": fake_remediate}, raising=False
    )
    r = s.remediate("vro")
    assert r["result"] is True
    assert "2/3 steps fired" in r["comment"]
    assert "9.0.2.0.25676793" in r["comment"]
    assert "fix_hosts" in r["changes"]["steps"]
    assert "ensure_envoy_dnat" in r["changes"]["steps"]
    # non-fired step is not in the changes dict
    assert "rebootstrap_kubelet" not in r["changes"]["steps"]


def test_remediation_failure_reports_verify_error(monkeypatch):
    def fake_remediate(**kw):
        return {
            "ok": False,
            "steps": {
                "fix_hosts": {"changed": True, "reason": "rewrote"},
            },
            "verify": {
                "ok": False,
                "status_code": None,
                "version": None,
                "error": "ConnectionError",
            },
        }

    monkeypatch.setattr(
        s, "__salt__", {"vcf_vro_bootstrap.remediate": fake_remediate}, raising=False
    )
    r = s.remediate("vro")
    assert r["result"] is False
    assert "not 200" in r["comment"]
    assert "ConnectionError" in r["comment"]
