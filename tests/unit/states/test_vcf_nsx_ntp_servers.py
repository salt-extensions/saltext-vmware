"""Tests for states.vcf_nsx_ntp_servers."""

import pytest

from saltext.vcf.clients import nsx_ntp_servers as c
from saltext.vcf.states import vcf_nsx_ntp_servers as st


@pytest.fixture(autouse=True)
def inject_opts(monkeypatch, opts):
    monkeypatch.setattr(st, "__opts__", opts, raising=False)


@pytest.fixture
def stub(monkeypatch):
    state = {
        "ntp": {"ntp_servers": []},
        "ntp_set_calls": [],
    }
    monkeypatch.setattr(c, "ntp_get", lambda opts, profile=None: state["ntp"])
    monkeypatch.setattr(
        c,
        "ntp_set",
        lambda opts, servers, profile=None: state["ntp_set_calls"].append(list(servers)),
    )
    return state


def test_ntp_no_change(stub):
    stub["ntp"] = {"ntp_servers": ["time.a.com", "time.b.com"]}
    ret = st.ntp_servers("name", ["time.b.com", "time.a.com"])
    assert ret["changes"] == {}
    assert stub["ntp_set_calls"] == []


def test_ntp_changes_servers(stub):
    stub["ntp"] = {"ntp_servers": ["time.a.com"]}
    ret = st.ntp_servers("name", ["time.b.com"])
    assert ret["changes"]["servers"] == {"old": ["time.a.com"], "new": ["time.b.com"]}
    assert stub["ntp_set_calls"] == [["time.b.com"]]


def test_ntp_test_mode(monkeypatch, stub):
    stub["ntp"] = {"ntp_servers": []}
    monkeypatch.setattr(st, "__opts__", {"test": True}, raising=False)
    ret = st.ntp_servers("name", ["time.a.com"])
    assert ret["result"] is None
    assert stub["ntp_set_calls"] == []
