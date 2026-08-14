"""Tests for states.vcf_nsx_localos_user."""

import pytest

from saltext.vcf.clients import nsx_localos_user as c
from saltext.vcf.states import vcf_nsx_localos_user as st


@pytest.fixture(autouse=True)
def inject_opts(monkeypatch, opts):
    monkeypatch.setattr(st, "__opts__", opts, raising=False)


def test_present_already_matches(monkeypatch):
    monkeypatch.setattr(
        c,
        "get_or_none",
        lambda opts, username, profile=None: {"userid": 5, "username": username, "role": "auditor"},
    )
    ret = st.present("svc-nsx-cli", "secret", "auditor")
    assert ret["changes"] == {}


def test_present_creates(monkeypatch):
    calls = []
    monkeypatch.setattr(c, "get_or_none", lambda opts, username, profile=None: None)
    monkeypatch.setattr(
        c,
        "create",
        lambda opts, username, password, role, profile=None, **spec: calls.append(
            (username, password, role, spec)
        ),
    )
    ret = st.present("svc-nsx-cli", "secret", "auditor")
    assert ret["changes"] == {"new": "svc-nsx-cli"}
    assert calls == [("svc-nsx-cli", "secret", "auditor", {})]


def test_present_updates_role(monkeypatch):
    calls = []
    monkeypatch.setattr(
        c,
        "get_or_none",
        lambda opts, username, profile=None: {"userid": 5, "username": username, "role": "auditor"},
    )
    monkeypatch.setattr(
        c, "update", lambda opts, userid, body, profile=None: calls.append((userid, body))
    )
    ret = st.present("svc-nsx-cli", "secret", "admin")
    assert ret["changes"] == {"role": {"old": "auditor", "new": "admin"}}
    assert calls == [(5, {"role": "admin"})]


def test_present_test_mode(monkeypatch):
    monkeypatch.setattr(c, "get_or_none", lambda opts, username, profile=None: None)
    monkeypatch.setattr(st, "__opts__", {"test": True}, raising=False)
    ret = st.present("svc-nsx-cli", "secret", "auditor")
    assert ret["result"] is None


def test_absent_already_absent(monkeypatch):
    monkeypatch.setattr(c, "get_or_none", lambda opts, username, profile=None: None)
    ret = st.absent("svc-nsx-cli")
    assert ret["changes"] == {}


def test_absent_deletes(monkeypatch):
    calls = []
    monkeypatch.setattr(
        c,
        "get_or_none",
        lambda opts, username, profile=None: {"userid": 5, "username": username, "role": "auditor"},
    )
    monkeypatch.setattr(c, "delete", lambda opts, userid, profile=None: calls.append(userid))
    ret = st.absent("svc-nsx-cli")
    assert ret["changes"] == {"deleted": "svc-nsx-cli"}
    assert calls == [5]


def test_absent_test_mode(monkeypatch):
    monkeypatch.setattr(
        c,
        "get_or_none",
        lambda opts, username, profile=None: {"userid": 5, "username": username, "role": "auditor"},
    )
    monkeypatch.setattr(st, "__opts__", {"test": True}, raising=False)
    ret = st.absent("svc-nsx-cli")
    assert ret["result"] is None
