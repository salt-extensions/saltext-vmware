"""Tests for states.vcf_nsx_dns_servers."""

import pytest

from saltext.vcf.clients import nsx_dns_servers as c
from saltext.vcf.states import vcf_nsx_dns_servers as st


@pytest.fixture(autouse=True)
def inject_opts(monkeypatch, opts):
    monkeypatch.setattr(st, "__opts__", opts, raising=False)


@pytest.fixture
def stub(monkeypatch):
    state = {
        "dns": {"name_servers": []},
        "dns_set_calls": [],
    }
    monkeypatch.setattr(c, "dns_get", lambda opts, profile=None: state["dns"])
    monkeypatch.setattr(
        c,
        "dns_set",
        lambda opts, servers, profile=None: state["dns_set_calls"].append(list(servers)),
    )
    return state


def test_dns_no_change(stub):
    stub["dns"] = {"name_servers": ["8.8.8.8", "1.1.1.1"]}
    ret = st.dns_servers("name", ["1.1.1.1", "8.8.8.8"])
    assert ret["changes"] == {}
    assert stub["dns_set_calls"] == []


def test_dns_changes_servers(stub):
    stub["dns"] = {"name_servers": ["1.1.1.1"]}
    ret = st.dns_servers("name", ["8.8.8.8"])
    assert ret["changes"]["servers"] == {"old": ["1.1.1.1"], "new": ["8.8.8.8"]}
    assert stub["dns_set_calls"] == [["8.8.8.8"]]


def test_dns_test_mode(monkeypatch, stub):
    stub["dns"] = {"name_servers": []}
    monkeypatch.setattr(st, "__opts__", {"test": True}, raising=False)
    ret = st.dns_servers("name", ["8.8.8.8"])
    assert ret["result"] is None
    assert stub["dns_set_calls"] == []
