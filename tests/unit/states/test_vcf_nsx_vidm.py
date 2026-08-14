"""Tests for states.vcf_nsx_vidm."""

import pytest

from saltext.vcf.clients import nsx_vidm as c
from saltext.vcf.states import vcf_nsx_vidm as st


@pytest.fixture(autouse=True)
def inject_opts(monkeypatch, opts):
    monkeypatch.setattr(st, "__opts__", opts, raising=False)


@pytest.fixture
def stub(monkeypatch):
    state = {"current": {"vidm_enable": False}, "update_calls": []}
    monkeypatch.setattr(c, "get", lambda opts, profile=None: state["current"])
    monkeypatch.setattr(
        c, "update", lambda opts, profile=None, **body: state["update_calls"].append(body)
    )
    return state


def test_enabled_no_change(stub):
    stub["current"] = {"vidm_enable": True, "vidm_hostname": "vidm.corp.example.test"}
    ret = st.enabled("nsx-vidm", vidm_enable=True, vidm_hostname="vidm.corp.example.test")
    assert ret["changes"] == {}
    assert stub["update_calls"] == []


def test_enabled_flips_and_preserves_other_fields(stub):
    stub["current"] = {
        "vidm_enable": False,
        "vidm_hostname": "vidm.corp.example.test",
        "vidm_domain": "corp.example.test",
    }
    ret = st.enabled("nsx-vidm", vidm_enable=True)
    assert ret["changes"] == {"vidm_enable": {"old": False, "new": True}}
    assert stub["update_calls"] == [
        {
            "vidm_enable": True,
            "vidm_hostname": "vidm.corp.example.test",
            "vidm_domain": "corp.example.test",
        }
    ]


def test_enabled_updates_extra_spec_field(stub):
    stub["current"] = {"vidm_enable": True, "vidm_hostname": "old.example.test"}
    ret = st.enabled("nsx-vidm", vidm_enable=True, vidm_hostname="new.example.test")
    assert ret["changes"] == {
        "vidm_hostname": {"old": "old.example.test", "new": "new.example.test"}
    }
    assert stub["update_calls"] == [{"vidm_enable": True, "vidm_hostname": "new.example.test"}]


def test_enabled_test_mode(monkeypatch, stub):
    stub["current"] = {"vidm_enable": False}
    monkeypatch.setattr(st, "__opts__", {"test": True}, raising=False)
    ret = st.enabled("nsx-vidm", vidm_enable=True)
    assert ret["result"] is None
    assert stub["update_calls"] == []
