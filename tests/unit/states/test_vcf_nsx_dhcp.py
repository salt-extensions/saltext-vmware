"""Tests for states.vcf_nsx_dhcp (DHCP server + relay profiles)."""

import pytest

from saltext.vcf.clients import nsx_dhcp as c
from saltext.vcf.states import vcf_nsx_dhcp as st


@pytest.fixture(autouse=True)
def inject_opts(monkeypatch, opts):
    monkeypatch.setattr(st, "__opts__", opts, raising=False)


def test_server_present_already_exists(monkeypatch):
    monkeypatch.setattr(
        c, "server_get_or_none", lambda opts, server_id, profile=None: {"id": server_id}
    )
    ret = st.server_present("dhcp-1", ["10.0.0.2/24"])
    assert ret["changes"] == {}


def test_server_present_creates(monkeypatch):
    calls = []
    monkeypatch.setattr(c, "server_get_or_none", lambda opts, server_id, profile=None: None)
    monkeypatch.setattr(
        c,
        "server_create",
        lambda opts, server_id, profile=None, **spec: calls.append((server_id, spec)),
    )
    ret = st.server_present("dhcp-1", ["10.0.0.2/24"], lease_time=86400)
    assert ret["changes"] == {"new": "dhcp-1"}
    assert calls == [("dhcp-1", {"server_addresses": ["10.0.0.2/24"], "lease_time": 86400})]


def test_server_present_test_mode(monkeypatch):
    monkeypatch.setattr(c, "server_get_or_none", lambda opts, server_id, profile=None: None)
    monkeypatch.setattr(st, "__opts__", {"test": True}, raising=False)
    ret = st.server_present("dhcp-1", ["10.0.0.2/24"])
    assert ret["result"] is None


def test_server_absent_already_absent(monkeypatch):
    monkeypatch.setattr(c, "server_get_or_none", lambda opts, server_id, profile=None: None)
    ret = st.server_absent("dhcp-1")
    assert ret["changes"] == {}


def test_server_absent_deletes(monkeypatch):
    calls = []
    monkeypatch.setattr(
        c, "server_get_or_none", lambda opts, server_id, profile=None: {"id": server_id}
    )
    monkeypatch.setattr(
        c, "server_delete", lambda opts, server_id, profile=None: calls.append(server_id)
    )
    ret = st.server_absent("dhcp-1")
    assert ret["changes"] == {"deleted": "dhcp-1"}
    assert calls == ["dhcp-1"]


def test_server_absent_test_mode(monkeypatch):
    monkeypatch.setattr(
        c, "server_get_or_none", lambda opts, server_id, profile=None: {"id": server_id}
    )
    monkeypatch.setattr(st, "__opts__", {"test": True}, raising=False)
    ret = st.server_absent("dhcp-1")
    assert ret["result"] is None


def test_relay_present_already_exists(monkeypatch):
    monkeypatch.setattr(
        c, "relay_get_or_none", lambda opts, relay_id, profile=None: {"id": relay_id}
    )
    ret = st.relay_present("relay-1", ["10.0.0.53"])
    assert ret["changes"] == {}


def test_relay_present_creates(monkeypatch):
    calls = []
    monkeypatch.setattr(c, "relay_get_or_none", lambda opts, relay_id, profile=None: None)
    monkeypatch.setattr(
        c,
        "relay_create",
        lambda opts, relay_id, server_addresses, profile=None, **spec: calls.append(
            (relay_id, server_addresses, spec)
        ),
    )
    ret = st.relay_present("relay-1", ["10.0.0.53"], display_name="relay-1-display")
    assert ret["changes"] == {"new": "relay-1"}
    assert calls == [("relay-1", ["10.0.0.53"], {"display_name": "relay-1-display"})]


def test_relay_present_test_mode(monkeypatch):
    monkeypatch.setattr(c, "relay_get_or_none", lambda opts, relay_id, profile=None: None)
    monkeypatch.setattr(st, "__opts__", {"test": True}, raising=False)
    ret = st.relay_present("relay-1", ["10.0.0.53"])
    assert ret["result"] is None


def test_relay_absent_already_absent(monkeypatch):
    monkeypatch.setattr(c, "relay_get_or_none", lambda opts, relay_id, profile=None: None)
    ret = st.relay_absent("relay-1")
    assert ret["changes"] == {}


def test_relay_absent_deletes(monkeypatch):
    calls = []
    monkeypatch.setattr(
        c, "relay_get_or_none", lambda opts, relay_id, profile=None: {"id": relay_id}
    )
    monkeypatch.setattr(
        c, "relay_delete", lambda opts, relay_id, profile=None: calls.append(relay_id)
    )
    ret = st.relay_absent("relay-1")
    assert ret["changes"] == {"deleted": "relay-1"}
    assert calls == ["relay-1"]


def test_relay_absent_test_mode(monkeypatch):
    monkeypatch.setattr(
        c, "relay_get_or_none", lambda opts, relay_id, profile=None: {"id": relay_id}
    )
    monkeypatch.setattr(st, "__opts__", {"test": True}, raising=False)
    ret = st.relay_absent("relay-1")
    assert ret["result"] is None
