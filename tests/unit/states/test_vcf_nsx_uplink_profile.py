"""Tests for states.vcf_nsx_uplink_profile."""

import pytest

from saltext.vcf.clients import nsx_uplink_profile as c
from saltext.vcf.states import vcf_nsx_uplink_profile as st


@pytest.fixture(autouse=True)
def inject_opts(monkeypatch, opts):
    monkeypatch.setattr(st, "__opts__", opts, raising=False)


TEAMING = {
    "policy": "FAILOVER_ORDER",
    "active_list": [{"uplink_name": "uplink-1", "uplink_type": "PNIC"}],
}


def test_present_already_exists(monkeypatch):
    monkeypatch.setattr(c, "get_or_none", lambda opts, profile_id, profile=None: {"id": profile_id})
    ret = st.present("uplink-profile-01", TEAMING)
    assert ret["changes"] == {}


def test_present_creates(monkeypatch):
    calls = []
    monkeypatch.setattr(c, "get_or_none", lambda opts, profile_id, profile=None: None)
    monkeypatch.setattr(
        c,
        "create",
        lambda opts, profile_id, teaming, profile=None, **spec: calls.append(
            (profile_id, teaming, spec)
        ),
    )
    ret = st.present("uplink-profile-01", TEAMING, mtu=9000)
    assert ret["changes"] == {"new": "uplink-profile-01"}
    assert calls == [("uplink-profile-01", TEAMING, {"mtu": 9000})]


def test_present_test_mode(monkeypatch):
    monkeypatch.setattr(c, "get_or_none", lambda opts, profile_id, profile=None: None)
    monkeypatch.setattr(st, "__opts__", {"test": True}, raising=False)
    ret = st.present("uplink-profile-01", TEAMING)
    assert ret["result"] is None


def test_absent_already_absent(monkeypatch):
    monkeypatch.setattr(c, "get_or_none", lambda opts, profile_id, profile=None: None)
    ret = st.absent("uplink-profile-01")
    assert ret["changes"] == {}


def test_absent_deletes(monkeypatch):
    calls = []
    monkeypatch.setattr(c, "get_or_none", lambda opts, profile_id, profile=None: {"id": profile_id})
    monkeypatch.setattr(
        c, "delete", lambda opts, profile_id, profile=None: calls.append(profile_id)
    )
    ret = st.absent("uplink-profile-01")
    assert ret["changes"] == {"deleted": "uplink-profile-01"}
    assert calls == ["uplink-profile-01"]


def test_absent_test_mode(monkeypatch):
    monkeypatch.setattr(c, "get_or_none", lambda opts, profile_id, profile=None: {"id": profile_id})
    monkeypatch.setattr(st, "__opts__", {"test": True}, raising=False)
    ret = st.absent("uplink-profile-01")
    assert ret["result"] is None
