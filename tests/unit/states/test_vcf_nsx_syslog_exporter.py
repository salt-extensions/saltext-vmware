"""Tests for states.vcf_nsx_syslog_exporter."""

import pytest

from saltext.vcf.clients import nsx_syslog_exporter as c
from saltext.vcf.states import vcf_nsx_syslog_exporter as st


@pytest.fixture(autouse=True)
def inject_opts(monkeypatch, opts):
    monkeypatch.setattr(st, "__opts__", opts, raising=False)


def test_present_already_exists(monkeypatch):
    monkeypatch.setattr(
        c, "get_or_none", lambda opts, exporter_name, profile=None: {"exporter_name": exporter_name}
    )
    ret = st.present("loginsight", "loginsight.lab.local", 514, "UDP")
    assert ret["changes"] == {}


def test_present_creates(monkeypatch):
    calls = []
    monkeypatch.setattr(c, "get_or_none", lambda opts, exporter_name, profile=None: None)
    monkeypatch.setattr(
        c,
        "create",
        lambda opts, exporter_name, server, port, protocol, profile=None, **spec: calls.append(
            (exporter_name, server, port, protocol, spec)
        ),
    )
    ret = st.present("loginsight", "loginsight.lab.local", 514, "UDP", level="INFO")
    assert ret["changes"] == {"new": "loginsight"}
    assert calls == [("loginsight", "loginsight.lab.local", 514, "UDP", {"level": "INFO"})]


def test_present_test_mode(monkeypatch):
    monkeypatch.setattr(c, "get_or_none", lambda opts, exporter_name, profile=None: None)
    monkeypatch.setattr(st, "__opts__", {"test": True}, raising=False)
    ret = st.present("loginsight", "loginsight.lab.local", 514, "UDP")
    assert ret["result"] is None


def test_absent_already_absent(monkeypatch):
    monkeypatch.setattr(c, "get_or_none", lambda opts, exporter_name, profile=None: None)
    ret = st.absent("loginsight")
    assert ret["changes"] == {}


def test_absent_deletes(monkeypatch):
    calls = []
    monkeypatch.setattr(
        c, "get_or_none", lambda opts, exporter_name, profile=None: {"exporter_name": exporter_name}
    )
    monkeypatch.setattr(
        c, "delete", lambda opts, exporter_name, profile=None: calls.append(exporter_name)
    )
    ret = st.absent("loginsight")
    assert ret["changes"] == {"deleted": "loginsight"}
    assert calls == ["loginsight"]


def test_absent_test_mode(monkeypatch):
    monkeypatch.setattr(
        c, "get_or_none", lambda opts, exporter_name, profile=None: {"exporter_name": exporter_name}
    )
    monkeypatch.setattr(st, "__opts__", {"test": True}, raising=False)
    ret = st.absent("loginsight")
    assert ret["result"] is None
