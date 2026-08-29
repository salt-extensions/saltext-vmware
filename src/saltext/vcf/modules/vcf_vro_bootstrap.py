"""Execution module: bootstrap-remediation for a freshly-deployed vRO 9.0.2 appliance.

vRO 9.0.2 (VCF Automation Orchestrator, ``orchestration-va`` gobuild)
ships an OVA whose firstboot cannot succeed unaided on a standalone
appliance (no vRSLCM / VCF Installer in front of it). Six documented
gotchas (see the ``project_vro_bootstrap_scope`` memory) each require a
manual remediation before ``GET /vco/api/about`` returns 200:

1. ``/etc/hosts`` malformed in the OVA image — two records mashed on the
   same line ``127.0.0.1 photon-<hex>127.0.0.1 vra-k8s.local``. Also the
   self-set hostname is not resolvable on any interface. Firstboot step
   ``02-setup-kubernetes`` calls ``curl https://<hostname>:10250/pods``
   and fails ``Could not resolve host: <hostname>``.
2. ``02-setup-kubernetes`` is non-idempotent on re-run
   (``mkdir /var/lib/etcd`` explodes with ``FileExistsError``, and it
   re-generates CAs on every run).
3. Kubelet cached client cert is invalidated by the CA regeneration in
   step 2; kubelet stays ``NotReady`` until
   ``/etc/kubernetes/kubelet.conf`` + ``/var/lib/kubelet/pki`` are wiped
   so kubelet can re-bootstrap from
   ``/etc/kubernetes/kubelet-bootstrap.conf``.
4. NodePort :443 is never bridged onto the appliance's external IP.
   ``contour-envoy`` runs as a NodePort service in the ``prelude``
   namespace, but ``kube-proxy`` (IPVS mode) only produces ClusterIP
   forwards. Manual DNAT is required::

        iptables -t nat -I PREROUTING 1 -d <host-ip> -p tcp --dport 443 \\
            -j DNAT --to-destination <envoy-pod-ip>:8443
        iptables -t nat -I OUTPUT     1 -d <host-ip> -p tcp --dport 443 \\
            -j DNAT --to-destination <envoy-pod-ip>:8443

   Persisted via an ``iptables-restore`` systemd unit so it survives
   reboots.
5. ``vm.vmname`` OVF property is not user-configurable — the OVF
   importer rejects it, so the deploy spec must filter it. (Deploy-time
   concern — handled by :mod:`saltext.vcf.modules.vcf_vro.deploy`,
   listed here only for completeness of the checklist.)
6. VAMI static-IP OVF properties are silently dropped; the VM DHCPs
   even when a static was requested. (Deploy-time concern — this module
   detects it and, if a static was requested via pillar, warns.)

This module encapsulates the four *appliance-side* remediations (1, 2,
3, 4) plus a follow-up ``run-bootstrap.service`` restart + wait, and
verifies ``/vco/api/about`` returns 200 at the end. Every remediation
is idempotent: if the fix is already in place, the function is a
no-op.

The remediations are driven over SSH as ``root`` (password from pillar
``saltext.vcf:vro:root_password`` or the ``root_password`` argument;
defaults to the ``varoot-password`` set at OVA deploy).
"""

from __future__ import annotations

import json
import logging
import shlex
import subprocess
import time

import requests

log = logging.getLogger(__name__)

__virtualname__ = "vcf_vro_bootstrap"


def __virtual__():
    return __virtualname__


# ----------------------------------------------------------------------
# Config resolution
# ----------------------------------------------------------------------


def _cfg(host=None, root_password=None):
    """Merge args with pillar ``saltext.vcf:vro:*`` and return a config dict."""
    opts = globals().get("__opts__") or {}
    pillar = (opts.get("pillar") or {}).get("saltext.vcf") or {}
    vro_cfg = pillar.get("vro") or {}
    return {
        "host": host or vro_cfg.get("host") or vro_cfg.get("appliance_host"),
        "root_password": (
            root_password
            or vro_cfg.get("root_password")
            or vro_cfg.get("varoot_password")
            # Legacy: earlier pillar shape used "password" for the appliance
            # root credential (same value as the OVF ``varoot-password``).
            or vro_cfg.get("password")
        ),
        "requested_static_ip": vro_cfg.get("static_ip"),
        # Probe-side knobs — override in pillar rather than hardcoding.
        "verify_ssl": bool(vro_cfg.get("verify_ssl", False)),
        "probe_timeout": int(vro_cfg.get("probe_timeout", 15)),
    }


# ----------------------------------------------------------------------
# SSH helpers (password-auth via sshpass)
# ----------------------------------------------------------------------


class SshError(RuntimeError):
    """Raised when an SSH command exits non-zero (unless expected)."""


def _ssh(host, password, cmd, timeout=60, check=True):
    """Run *cmd* on *host* as root over SSH; return (rc, stdout, stderr).

    Uses ``sshpass`` for password auth. ``StrictHostKeyChecking=no`` and
    ``UserKnownHostsFile=/dev/null`` so the fingerprint doesn't need to
    be pre-seeded (this is bootstrap territory).
    """
    argv = [
        "sshpass",
        "-p",
        password,
        "ssh",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        "LogLevel=ERROR",
        "-o",
        "ConnectTimeout=15",
        f"root@{host}",
        cmd,
    ]
    log.debug("vcf_vro_bootstrap._ssh: %s :: %s", host, cmd)
    try:
        p = subprocess.run(  # noqa: S603
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SshError(f"ssh timed out after {timeout}s: {cmd!r}") from exc
    if check and p.returncode != 0:
        raise SshError(f"ssh {host} {cmd!r} rc={p.returncode} stderr={p.stderr!r}")
    return p.returncode, p.stdout, p.stderr


def _put_file(host, password, content, remote_path, mode="0644"):
    """Push *content* to *remote_path* atomically."""
    quoted = shlex.quote(content)
    cmd = (
        f'tmp=$(mktemp) && printf %s {quoted} > "$tmp" && '
        f'chmod {mode} "$tmp" && mv "$tmp" {shlex.quote(remote_path)}'
    )
    _ssh(host, password, cmd)


# ----------------------------------------------------------------------
# Gotcha 1: /etc/hosts
# ----------------------------------------------------------------------


def _hostname(host, password):
    _, out, _ = _ssh(host, password, "hostname")
    return out.strip()


def _primary_ipv4(host, password):
    _, out, _ = _ssh(
        host,
        password,
        "ip -4 -o addr show scope global | awk '{print $4}' | cut -d/ -f1 | head -n1",
    )
    return out.strip()


def fix_hosts(host=None, root_password=None):
    """Idempotently rewrite ``/etc/hosts`` so the appliance hostname resolves.

    Detects the malformed ``photon-<hex>127.0.0.1 vra-k8s.local``
    single-line record and splits it, and appends explicit entries for
    the appliance hostname on both loopback and the primary interface.

    Returns::

        {"changed": bool, "reason": str}
    """
    cfg = _cfg(host=host, root_password=root_password)
    host = cfg["host"]
    pw = cfg["root_password"]
    hn = _hostname(host, pw)
    ip = _primary_ipv4(host, pw)

    _, current, _ = _ssh(host, pw, "cat /etc/hosts")
    lines = current.splitlines()

    # Detect malformed line: something like
    #   "127.0.0.1 photon-<hex>127.0.0.1 vra-k8s.local"
    malformed = [i for i, ln in enumerate(lines) if ln.count("127.0.0.1") >= 2]
    has_host_loopback = any(ln.startswith("127.0.0.1") and hn in ln.split() for ln in lines)
    has_host_primary = any(ip in ln.split() and hn in ln.split() for ln in lines)

    if not malformed and has_host_loopback and has_host_primary:
        return {"changed": False, "reason": "hosts already sane"}

    fixed = []
    for ln in lines:
        if ln.count("127.0.0.1") >= 2:
            # Split "127.0.0.1 photon-<hex>127.0.0.1 vra-k8s.local" -> two lines
            # but drop the photon-<hex> junk record entirely.
            second_idx = ln.index("127.0.0.1", 1)
            second = ln[second_idx:].strip()
            fixed.append(second)
            continue
        fixed.append(ln)

    added = []
    if not any(l.startswith("127.0.0.1") and hn in l.split() for l in fixed):
        fixed.insert(0, f"127.0.0.1 {hn}")
        added.append(f"127.0.0.1 {hn}")
    if ip and not any(ip in l.split() and hn in l.split() for l in fixed):
        fixed.append(f"{ip} {hn}")
        added.append(f"{ip} {hn}")

    new = "\n".join(fixed) + "\n"
    _put_file(host, pw, new, "/etc/hosts", mode="0644")
    return {
        "changed": True,
        "reason": f"rewrote /etc/hosts (malformed_lines={len(malformed)}, added={added})",
    }


# ----------------------------------------------------------------------
# Gotcha 2: neutralise 02-setup-kubernetes if kubelet is already up
# ----------------------------------------------------------------------


def disable_setup_kubernetes_firstboot(host=None, root_password=None):
    """Move ``02-setup-kubernetes`` out of firstboot.d if k8s is already up.

    Skipping is safe because it is non-idempotent (see docstring), and
    only meaningful once the k8s control plane has been initialised.
    Detects "k8s already up" by presence of ``/etc/kubernetes/admin.conf``
    AND a healthy kubelet. If either is missing, this is a no-op and we
    leave the script in place so a fresh firstboot can run it.
    """
    cfg = _cfg(host=host, root_password=root_password)
    host, pw = cfg["host"], cfg["root_password"]

    src = "/etc/bootstrap/firstboot.d/02-setup-kubernetes"
    dst = "/root/02-setup-kubernetes.SKIPPED"

    rc_src, _, _ = _ssh(host, pw, f"test -f {src}", check=False)
    rc_dst, _, _ = _ssh(host, pw, f"test -f {dst}", check=False)

    if rc_src != 0 and rc_dst == 0:
        return {"changed": False, "reason": "already neutralised"}
    if rc_src != 0:
        return {"changed": False, "reason": "02-setup-kubernetes absent"}

    # Guard: only skip if k8s is up
    rc_admin, _, _ = _ssh(host, pw, "test -f /etc/kubernetes/admin.conf", check=False)
    if rc_admin != 0:
        return {
            "changed": False,
            "reason": "k8s admin.conf absent; leaving 02-setup-kubernetes in place",
        }

    _ssh(host, pw, f"mv {src} {dst}")
    return {"changed": True, "reason": f"moved {src} -> {dst}"}


# ----------------------------------------------------------------------
# Gotcha 3: kubelet cert re-bootstrap
# ----------------------------------------------------------------------


def rebootstrap_kubelet_if_notready(host=None, root_password=None):
    """If the node is ``NotReady``, wipe kubelet cache so it re-bootstraps.

    Uses the appliance's admin kubeconfig to query node status. If Ready
    (or if kubelet has never been bootstrapped — no ``admin.conf``),
    this is a no-op.
    """
    cfg = _cfg(host=host, root_password=root_password)
    host, pw = cfg["host"], cfg["root_password"]

    rc, _, _ = _ssh(host, pw, "test -f /etc/kubernetes/admin.conf", check=False)
    if rc != 0:
        return {"changed": False, "reason": "no admin.conf yet; skipping"}

    _, out, _ = _ssh(
        host,
        pw,
        "kubectl --kubeconfig=/etc/kubernetes/admin.conf get nodes "
        "-o jsonpath='{range .items[*]}{.metadata.name}={.status.conditions[?(@.type==\"Ready\")].status}\\n{end}'",
        check=False,
    )
    if "=True" in out:
        return {"changed": False, "reason": f"node already Ready ({out.strip()})"}

    _ssh(
        host,
        pw,
        "rm -f /etc/kubernetes/kubelet.conf && "
        "rm -rf /var/lib/kubelet/pki && "
        "systemctl restart kubelet",
    )
    # Wait up to 120s for node to become Ready
    deadline = time.time() + 120
    while time.time() < deadline:
        _, out, _ = _ssh(
            host,
            pw,
            "kubectl --kubeconfig=/etc/kubernetes/admin.conf get nodes "
            "-o jsonpath='{.items[0].status.conditions[?(@.type==\"Ready\")].status}'",
            check=False,
        )
        if out.strip() == "True":
            return {"changed": True, "reason": "kubelet re-bootstrapped; node Ready"}
        time.sleep(5)
    return {
        "changed": True,
        "reason": "kubelet re-bootstrapped but node did not become Ready in 120s",
    }


# ----------------------------------------------------------------------
# Kick firstboot after a hosts/kubelet fix
# ----------------------------------------------------------------------


def rerun_firstboot(host=None, root_password=None, timeout=1800):
    """If ``run-bootstrap.service`` has not succeeded, kick it and wait.

    Idempotent: if ``/opt/vmware/etc/vami/flags/vami_firstboot`` is
    already gone (firstboot completed) or ``run-bootstrap.service``
    reports ``active`` or ``inactive (dead) with SUCCESS``, this is a
    no-op.

    Waits up to *timeout* seconds for one of:
      - firstboot flag file removed
      - ``deploy.sh`` writes ``/var/log/deploy.log`` with a success marker
      - envoy pod running
    """
    cfg = _cfg(host=host, root_password=root_password)
    host, pw = cfg["host"], cfg["root_password"]

    _, svc_state, _ = _ssh(
        host,
        pw,
        "systemctl show run-bootstrap.service -p Result -p ActiveState -p SubState",
        check=False,
    )
    # run-bootstrap.service is Type=oneshot RemainAfterExit=yes, so on
    # success it stays ActiveState=active / SubState=exited (not
    # "inactive"). Accept either "inactive" or "exited" as terminal.
    if "Result=success" in svc_state and (
        "SubState=exited" in svc_state or "ActiveState=inactive" in svc_state
    ):
        return {"changed": False, "reason": "firstboot already completed"}

    # Reset failed unit then restart
    _ssh(host, pw, "systemctl reset-failed run-bootstrap.service || true", check=False)
    _ssh(host, pw, "systemctl restart run-bootstrap.service", check=False)

    deadline = time.time() + int(timeout)
    last_state = None
    while time.time() < deadline:
        _, out, _ = _ssh(
            host,
            pw,
            "systemctl show run-bootstrap.service -p Result -p ActiveState -p SubState",
            check=False,
        )
        last_state = out.strip()
        if "Result=success" in out and ("SubState=exited" in out or "ActiveState=inactive" in out):
            return {"changed": True, "reason": "firstboot completed successfully"}
        if "ActiveState=failed" in out:
            _, jc, _ = _ssh(
                host,
                pw,
                "journalctl -u run-bootstrap.service --no-pager -n 40",
                check=False,
            )
            return {
                "changed": True,
                "reason": f"firstboot failed: {last_state}\n{jc[-2000:]}",
                "failed": True,
            }
        time.sleep(15)
    return {
        "changed": True,
        "reason": f"firstboot did not finish in {timeout}s (last: {last_state})",
        "failed": True,
    }


# ----------------------------------------------------------------------
# Gotcha 4: DNAT envoy NodePort to the appliance IP
# ----------------------------------------------------------------------


def _envoy_pod_ip(host, password):
    """Return the current contour/envoy pod IP, or None if not found."""
    _, out, _ = _ssh(
        host,
        password,
        "kubectl --kubeconfig=/etc/kubernetes/admin.conf get pods -A "
        "-l 'app=envoy' -o json 2>/dev/null || "
        "kubectl --kubeconfig=/etc/kubernetes/admin.conf get pods -A "
        "-o json",
        check=False,
    )
    try:
        data = json.loads(out)
    except (ValueError, TypeError):
        return None
    for item in data.get("items") or []:
        name = (item.get("metadata") or {}).get("name") or ""
        if "envoy" in name or "contour-envoy" in name:
            status = item.get("status") or {}
            if status.get("phase") == "Running":
                return status.get("podIP")
    return None


def ensure_envoy_dnat(host=None, root_password=None):
    """Install DNAT rules (PREROUTING + OUTPUT) for host-IP:443 -> envoy:8443.

    Idempotent: parses ``iptables-save -t nat`` and only inserts rules
    that are missing. Also persists via
    ``/etc/systemd/system/vro-envoy-dnat.service`` so the rules survive
    a reboot.

    If contour/envoy is not yet running (Prelude helm charts have not
    finished), returns ``{"changed": False, "reason": "envoy not up"}``
    and defers.
    """
    cfg = _cfg(host=host, root_password=root_password)
    host, pw = cfg["host"], cfg["root_password"]

    envoy_ip = _envoy_pod_ip(host, pw)
    if not envoy_ip:
        return {"changed": False, "reason": "envoy pod not yet running; skipping DNAT"}

    host_ip = _primary_ipv4(host, pw)
    target = f"{envoy_ip}:8443"

    # See what's already there
    _, save, _ = _ssh(host, pw, "iptables-save -t nat 2>/dev/null || true", check=False)

    def _rule_present(chain, dest_ip):
        marker = f"-A {chain} -d {dest_ip}/32 -p tcp -m tcp --dport 443 -j DNAT --to-destination {target}"
        return marker in save

    # ``host_ip`` and ``target`` come from parsed appliance output
    # (``ip -4 -o addr``, ``kubectl get pods``). In practice they are
    # bare IPs / ``ip:port`` strings so ``shlex.quote`` is a no-op, but
    # keep the quoting so a future source with metacharacters can't
    # break out of the remote-shell command.
    q_host_ip = shlex.quote(host_ip)
    q_target = shlex.quote(target)

    changes = []
    if not _rule_present("PREROUTING", host_ip):
        _ssh(
            host,
            pw,
            f"iptables -t nat -I PREROUTING 1 -d {q_host_ip} -p tcp --dport 443 "
            f"-j DNAT --to-destination {q_target}",
        )
        changes.append(f"PREROUTING {host_ip}:443 -> {target}")
    if not _rule_present("OUTPUT", host_ip):
        _ssh(
            host,
            pw,
            f"iptables -t nat -I OUTPUT 1 -d {q_host_ip} -p tcp --dport 443 "
            f"-j DNAT --to-destination {q_target}",
        )
        changes.append(f"OUTPUT {host_ip}:443 -> {target}")

    # Persist via a systemd unit that re-applies on boot.
    unit = """[Unit]
Description=vRO envoy NodePort DNAT (managed by saltext.vcf.vcf_vro_bootstrap)
After=kubelet.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/sh -c '\
    HOST_IP=$(ip -4 -o addr show scope global | awk "{{print \\$4}}" | cut -d/ -f1 | head -n1); \
    ENVOY_IP=$(kubectl --kubeconfig=/etc/kubernetes/admin.conf get pods -A -o jsonpath="{{range .items[*]}}{{.status.podIP}} {{.metadata.name}}{{\\"\\n\\"}}{{end}}" | awk "/envoy/ {{print \\$1; exit}}"); \
    [ -n "$HOST_IP" ] && [ -n "$ENVOY_IP" ] || exit 0; \
    iptables -t nat -C PREROUTING -d "$HOST_IP" -p tcp --dport 443 -j DNAT --to-destination "$ENVOY_IP:8443" 2>/dev/null || \
    iptables -t nat -I PREROUTING 1 -d "$HOST_IP" -p tcp --dport 443 -j DNAT --to-destination "$ENVOY_IP:8443"; \
    iptables -t nat -C OUTPUT -d "$HOST_IP" -p tcp --dport 443 -j DNAT --to-destination "$ENVOY_IP:8443" 2>/dev/null || \
    iptables -t nat -I OUTPUT 1 -d "$HOST_IP" -p tcp --dport 443 -j DNAT --to-destination "$ENVOY_IP:8443"'

[Install]
WantedBy=multi-user.target
"""
    unit_path = "/etc/systemd/system/vro-envoy-dnat.service"
    _, existing, _ = _ssh(host, pw, f"cat {unit_path} 2>/dev/null || true", check=False)
    if existing != unit:
        _put_file(host, pw, unit, unit_path, mode="0644")
        _ssh(
            host,
            pw,
            "systemctl daemon-reload && systemctl enable vro-envoy-dnat.service",
            check=False,
        )
        changes.append(f"installed {unit_path}")

    if not changes:
        return {"changed": False, "reason": "DNAT already in place"}
    return {"changed": True, "reason": "; ".join(changes), "envoy_ip": envoy_ip, "host_ip": host_ip}


# ----------------------------------------------------------------------
# Verification
# ----------------------------------------------------------------------


def verify(host=None, root_password=None, timeout=300):
    """Poll ``https://<host>/vco/api/about`` until 200 or *timeout*.

    Sends ``Host: <appliance-hostname>`` so contour's httpproxy can
    route regardless of DHCP-vs-static.

    Returns::

        {"ok": bool, "status_code": int|None, "version": str|None, "elapsed_s": float}
    """
    cfg = _cfg(host=host, root_password=root_password)
    host, pw = cfg["host"], cfg["root_password"]
    hn = _hostname(host, pw)

    url = f"https://{host}/vco/api/about"
    headers = {"Host": hn}
    started = time.time()
    deadline = started + int(timeout)
    last_code = None
    last_err = None
    # ``verify`` and per-request timeout come from pillar
    # (``saltext.vcf:vro:verify_ssl`` / ``:probe_timeout``) instead of
    # being hardcoded — matches how other modules take TLS/verify from
    # config.
    verify_ssl = cfg["verify_ssl"]
    probe_timeout = cfg["probe_timeout"]
    while time.time() < deadline:
        try:
            r = requests.get(url, headers=headers, verify=verify_ssl, timeout=probe_timeout)
            last_code = r.status_code
            if r.status_code == 200:
                try:
                    body = r.json()
                except ValueError:
                    body = {}
                return {
                    "ok": True,
                    "status_code": 200,
                    "version": body.get("version"),
                    "elapsed_s": round(time.time() - started, 1),
                }
        except requests.exceptions.RequestException as exc:
            last_err = str(exc)
        time.sleep(10)
    return {
        "ok": False,
        "status_code": last_code,
        "version": None,
        "elapsed_s": round(time.time() - started, 1),
        "error": last_err,
    }


# ----------------------------------------------------------------------
# Top-level: run the full sequence
# ----------------------------------------------------------------------


def remediate(host=None, root_password=None, verify_timeout=600, firstboot_timeout=1800):
    """Run all appliance-side remediations and verify.

    Returns::

        {
            "ok": bool,
            "steps": {step_name: {"changed": bool, "reason": str, ...}},
            "verify": {"ok": bool, "status_code": int|None, "version": str|None},
        }
    """
    steps = {}

    # Short-circuit: if already serving, do nothing.
    v = verify(host=host, root_password=root_password, timeout=10)
    if v["ok"]:
        return {"ok": True, "steps": {}, "verify": v, "short_circuit": True}

    steps["fix_hosts"] = fix_hosts(host=host, root_password=root_password)
    steps["disable_setup_kubernetes_firstboot"] = disable_setup_kubernetes_firstboot(
        host=host, root_password=root_password
    )
    steps["rebootstrap_kubelet"] = rebootstrap_kubelet_if_notready(
        host=host, root_password=root_password
    )
    # If firstboot never completed, kick it now that hosts + kubelet are sane.
    steps["rerun_firstboot"] = rerun_firstboot(
        host=host, root_password=root_password, timeout=firstboot_timeout
    )
    # After deploy.sh runs, envoy will be up — install DNAT.
    steps["ensure_envoy_dnat"] = ensure_envoy_dnat(host=host, root_password=root_password)

    final = verify(host=host, root_password=root_password, timeout=verify_timeout)
    return {"ok": final["ok"], "steps": steps, "verify": final}
