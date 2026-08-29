"""Tests for states.vcf_nsx_ldap."""

import pytest

from saltext.vcf.clients import nsx_ldap as c
from saltext.vcf.states import vcf_nsx_ldap as st


@pytest.fixture(autouse=True)
def inject_opts(monkeypatch, opts):
    monkeypatch.setattr(st, "__opts__", opts, raising=False)


def test_present_already_exists(monkeypatch):
    monkeypatch.setattr(
        c,
        "list_",
        lambda opts, profile=None: {"results": [{"id": "src-1", "display_name": "corp-ldap"}]},
    )
    ret = st.present("corp-ldap", [{"url": "ldaps://ldap.corp.example.test:636"}], "dc=corp")
    assert ret["changes"] == {}


def test_present_creates(monkeypatch):
    calls = []
    monkeypatch.setattr(c, "list_", lambda opts, profile=None: {"results": []})
    monkeypatch.setattr(
        c,
        "create",
        lambda opts, name, ldap_servers, base_dn, profile=None, **spec: calls.append(
            (name, ldap_servers, base_dn, spec)
        ),
    )
    ret = st.present(
        "corp-ldap",
        [{"url": "ldaps://ldap.corp.example.test:636"}],
        "dc=corp",
        domain_name="corp.example.test",
    )
    assert ret["changes"] == {"new": "corp-ldap"}
    assert calls == [
        (
            "corp-ldap",
            [{"url": "ldaps://ldap.corp.example.test:636"}],
            "dc=corp",
            {"domain_name": "corp.example.test"},
        )
    ]


def test_present_test_mode(monkeypatch):
    monkeypatch.setattr(c, "list_", lambda opts, profile=None: {"results": []})
    monkeypatch.setattr(st, "__opts__", {"test": True}, raising=False)
    ret = st.present("corp-ldap", [{"url": "ldaps://ldap.corp.example.test:636"}], "dc=corp")
    assert ret["result"] is None


def test_absent_already_absent(monkeypatch):
    monkeypatch.setattr(c, "list_", lambda opts, profile=None: {"results": []})
    ret = st.absent("corp-ldap")
    assert ret["changes"] == {}


def test_absent_deletes(monkeypatch):
    calls = []
    monkeypatch.setattr(
        c,
        "list_",
        lambda opts, profile=None: {"results": [{"id": "src-1", "display_name": "corp-ldap"}]},
    )
    monkeypatch.setattr(c, "delete", lambda opts, source_id, profile=None: calls.append(source_id))
    ret = st.absent("corp-ldap")
    assert ret["changes"] == {"deleted": "corp-ldap"}
    assert calls == ["src-1"]


def test_absent_test_mode(monkeypatch):
    monkeypatch.setattr(
        c,
        "list_",
        lambda opts, profile=None: {"results": [{"id": "src-1", "display_name": "corp-ldap"}]},
    )
    monkeypatch.setattr(st, "__opts__", {"test": True}, raising=False)
    ret = st.absent("corp-ldap")
    assert ret["result"] is None
