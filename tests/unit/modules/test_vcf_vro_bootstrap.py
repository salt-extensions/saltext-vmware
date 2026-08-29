"""Tests for :mod:`saltext.vcf.modules.vcf_vro_bootstrap`."""

import pytest

from saltext.vcf.modules import vcf_vro_bootstrap as m


@pytest.fixture
def bootstrap_opts(opts):
    opts["pillar"]["saltext.vcf"]["vro"] = {
        "host": "25.0.3.189",
        "password": "VMware123!VMware123!",
    }
    return opts


@pytest.fixture(autouse=True)
def inject_opts(monkeypatch, bootstrap_opts):
    monkeypatch.setattr(m, "__opts__", bootstrap_opts, raising=False)
    yield


class FakeSsh:
    """Records commands and returns scripted (rc, stdout, stderr) tuples."""

    def __init__(self):
        self.calls = []
        # Map (host, cmd-substring) -> (rc, stdout, stderr)
        self.responses = {}
        # Default response
        self.default = (0, "", "")

    def add(self, cmd_contains, rc=0, stdout="", stderr=""):
        self.responses[cmd_contains] = (rc, stdout, stderr)

    def __call__(self, host, password, cmd, timeout=60, check=True):
        self.calls.append((host, cmd))
        for needle, resp in self.responses.items():
            if needle in cmd:
                if check and resp[0] != 0:
                    raise m.SshError(f"scripted failure: {cmd!r}")
                return resp
        return self.default


@pytest.fixture
def fake_ssh(monkeypatch):
    ss = FakeSsh()
    monkeypatch.setattr(m, "_ssh", ss)
    monkeypatch.setattr(
        m,
        "_put_file",
        lambda h, p, c, path, mode="0644": ss.calls.append((h, f"PUT {path} {len(c)}b {mode}")),
    )
    return ss


def test_fix_hosts_rewrites_malformed(fake_ssh):
    fake_ssh.add("hostname", stdout="vro-25-0-0-61\n")
    fake_ssh.add(
        "ip -4 -o addr",
        stdout="25.0.3.189\n",
    )
    fake_ssh.add(
        "cat /etc/hosts",
        stdout=("127.0.0.1 localhost\n" "127.0.0.1 photon-36f70c46116d127.0.0.1 vra-k8s.local\n"),
    )
    r = m.fix_hosts()
    assert r["changed"] is True
    put = [c for c in fake_ssh.calls if "PUT /etc/hosts" in c[1]]
    assert put, "should have written /etc/hosts"


def test_fix_hosts_noop_when_already_sane(fake_ssh):
    fake_ssh.add("hostname", stdout="vro-25-0-0-61\n")
    fake_ssh.add("ip -4 -o addr", stdout="25.0.3.189\n")
    fake_ssh.add(
        "cat /etc/hosts",
        stdout=("127.0.0.1 vro-25-0-0-61\n" "127.0.0.1 localhost\n" "25.0.3.189 vro-25-0-0-61\n"),
    )
    r = m.fix_hosts()
    assert r["changed"] is False
    assert not any("PUT /etc/hosts" in c[1] for c in fake_ssh.calls)


def test_disable_setup_kubernetes_noop_when_already_moved(fake_ssh):
    # src absent, dst present -> already neutralised
    def sc(host, pw, cmd, timeout=60, check=True):
        fake_ssh.calls.append((host, cmd))
        if "test -f /etc/bootstrap/firstboot.d/02-setup-kubernetes" in cmd:
            return (1, "", "")
        if "test -f /root/02-setup-kubernetes.SKIPPED" in cmd:
            return (0, "", "")
        return (0, "", "")

    fake_ssh.__call__ = sc  # override
    original = m._ssh
    m._ssh = sc
    try:
        r = m.disable_setup_kubernetes_firstboot()
    finally:
        m._ssh = original
    assert r["changed"] is False
    assert "already neutralised" in r["reason"]


def test_rebootstrap_kubelet_noop_when_ready(fake_ssh):
    fake_ssh.add("test -f /etc/kubernetes/admin.conf", rc=0)
    fake_ssh.add("kubectl", stdout="vro-25-0-0-61=True\n")
    r = m.rebootstrap_kubelet_if_notready()
    assert r["changed"] is False
    assert "Ready" in r["reason"]


def test_ensure_envoy_dnat_defers_when_envoy_absent(fake_ssh, monkeypatch):
    monkeypatch.setattr(m, "_envoy_pod_ip", lambda h, p: None)
    r = m.ensure_envoy_dnat()
    assert r["changed"] is False
    assert "envoy" in r["reason"]


def test_ensure_envoy_dnat_installs_missing_rules(fake_ssh, monkeypatch):
    monkeypatch.setattr(m, "_envoy_pod_ip", lambda h, p: "10.244.0.14")
    monkeypatch.setattr(m, "_primary_ipv4", lambda h, p: "25.0.3.189")
    # No existing DNAT + no existing systemd unit
    fake_ssh.add("iptables-save -t nat", stdout="")
    fake_ssh.add("cat /etc/systemd/system/vro-envoy-dnat.service", stdout="")
    r = m.ensure_envoy_dnat()
    assert r["changed"] is True
    inserted = [c for c in fake_ssh.calls if "iptables -t nat -I" in c[1]]
    assert len(inserted) == 2  # PREROUTING + OUTPUT


def test_ensure_envoy_dnat_noop_when_rules_present(fake_ssh, monkeypatch):
    monkeypatch.setattr(m, "_envoy_pod_ip", lambda h, p: "10.244.0.14")
    monkeypatch.setattr(m, "_primary_ipv4", lambda h, p: "25.0.3.189")
    existing_save = (
        "-A PREROUTING -d 25.0.3.189/32 -p tcp -m tcp --dport 443 -j DNAT --to-destination 10.244.0.14:8443\n"
        "-A OUTPUT -d 25.0.3.189/32 -p tcp -m tcp --dport 443 -j DNAT --to-destination 10.244.0.14:8443\n"
    )
    fake_ssh.add("iptables-save -t nat", stdout=existing_save)
    # Assert no iptables inserts happened (no rewrite path exercised here).
    m.ensure_envoy_dnat()
    inserted = [c for c in fake_ssh.calls if "iptables -t nat -I" in c[1]]
    assert not inserted


def test_remediate_short_circuits_when_already_serving(monkeypatch):
    monkeypatch.setattr(
        m,
        "verify",
        lambda host=None, root_password=None, timeout=10: {
            "ok": True,
            "status_code": 200,
            "version": "9.0.2.0.25676793",
            "elapsed_s": 0.1,
        },
    )
    r = m.remediate()
    assert r["ok"] is True
    assert r["short_circuit"] is True
    assert r["steps"] == {}
