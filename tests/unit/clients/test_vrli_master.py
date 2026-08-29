"""Tests for clients.vrli_master and utils.vrli.

Covers two distinct auth surfaces:

* Post-install ``/api/v2/*`` Bearer flow via
  :mod:`saltext.vcf.utils.vrli` — ``get_version``, ``list_hosts``,
  ``get_or_none`` (the existing helpers, unchanged).
* First-run wizard flow — ``wait_for_setup_ready`` (any HTTP < 500 =
  reachable) and ``bootstrap_master`` (3-call form-encoded CSRF
  sequence). See ``~/src/saltext-opsdev/VRLI-MANUAL-INSTALL-NOTES.md``
  for the working manual-install transcript this covers.

The ``opts`` fixture from tests/conftest.py has no ``vrli`` pillar entry,
so each test synthesizes an opts dict with the ``saltext.vcf.vrli`` block.
"""

import subprocess
from unittest.mock import MagicMock

import pytest
import requests
import responses

from saltext.vcf.clients import vrli_master
from saltext.vcf.utils import vrli as vrli_utils

VRLI_HOST = "vrli.test"
VRLI_BASE = f"https://{VRLI_HOST}:9543"
VRLI_SESSIONS_URL = f"{VRLI_BASE}/api/v2/sessions"
VRLI_VERSION_URL = f"{VRLI_BASE}/api/v2/version"
VRLI_HOSTS_URL = f"{VRLI_BASE}/api/v2/hosts"
VRLI_CSRF_URL = f"{VRLI_BASE}/csrf"
VRLI_LOGIN_URL = f"{VRLI_BASE}/login"
VRLI_STARTUP_URL = f"{VRLI_BASE}/admin/startup"
VRLI_ROOT_URL = f"{VRLI_BASE}/"


@pytest.fixture
def vrli_opts():
    """Salt-style opts dict with only the ``saltext.vcf.vrli`` pillar branch."""
    return {
        "pillar": {
            "saltext.vcf": {
                "vrli": {
                    "host": VRLI_HOST,
                    "username": "admin",
                    "password": "p",
                    "verify_ssl": False,
                },
            },
        },
        "test": False,
    }


@pytest.fixture(autouse=True)
def _clear_vrli_cache():
    vrli_utils._TOKEN_CACHE.clear()
    yield
    vrli_utils._TOKEN_CACHE.clear()


@pytest.fixture
def vrli_authed(mocked_responses):
    """Pre-register the vRLI POST /api/v2/sessions login endpoint."""
    mocked_responses.add(
        responses.POST,
        VRLI_SESSIONS_URL,
        json={"userId": "u-1", "sessionId": "vrli-tok-abc", "ttl": 1800},
        status=200,
    )
    return mocked_responses


# ---------------------------------------------------------------------------
# Post-install /api/v2/* Bearer helpers (unchanged)
# ---------------------------------------------------------------------------


def test_get_version_returns_release_and_version(vrli_opts, vrli_authed):
    vrli_authed.add(
        responses.GET,
        VRLI_VERSION_URL,
        json={"releaseName": "VMware Log Insight 8.18.0", "version": "8.18.0-12345"},
        status=200,
    )
    info = vrli_master.get_version(vrli_opts)
    assert info == {
        "releaseName": "VMware Log Insight 8.18.0",
        "version": "8.18.0-12345",
    }


def test_get_version_sends_bearer_from_sessions_login(vrli_opts, vrli_authed):
    vrli_authed.add(
        responses.GET,
        VRLI_VERSION_URL,
        json={"version": "8.18.0"},
        status=200,
    )
    vrli_master.get_version(vrli_opts)

    login_req = vrli_authed.calls[0].request
    assert login_req.url == VRLI_SESSIONS_URL
    body = login_req.body
    if isinstance(body, bytes):
        body = body.decode("utf-8")
    assert '"provider": "Local"' in body
    assert '"username": "admin"' in body

    version_req = vrli_authed.calls[-1].request
    assert version_req.headers.get("Authorization") == "Bearer vrli-tok-abc"


def test_get_version_caches_session_token(vrli_opts, vrli_authed):
    vrli_authed.add(responses.GET, VRLI_VERSION_URL, json={"version": "8.18.0"}, status=200)
    vrli_authed.add(responses.GET, VRLI_VERSION_URL, json={"version": "8.18.0"}, status=200)
    vrli_master.get_version(vrli_opts)
    vrli_master.get_version(vrli_opts)
    login_calls = [c for c in vrli_authed.calls if c.request.url == VRLI_SESSIONS_URL]
    assert len(login_calls) == 1


def test_get_version_raises_when_host_missing():
    opts = {"pillar": {"saltext.vcf": {"vrli": {}}}, "test": False}
    with pytest.raises(RuntimeError, match="host is not configured"):
        vrli_master.get_version(opts)


def test_get_version_raises_when_master_returns_5xx(vrli_opts, vrli_authed):
    vrli_authed.add(
        responses.GET,
        VRLI_VERSION_URL,
        json={"errorMessage": "boom"},
        status=503,
    )
    with pytest.raises(requests.HTTPError):
        vrli_master.get_version(vrli_opts)


def test_get_version_login_missing_session_id_raises(vrli_opts, mocked_responses):
    mocked_responses.add(
        responses.POST,
        VRLI_SESSIONS_URL,
        json={"userId": "u-1"},  # no sessionId
        status=200,
    )
    with pytest.raises(RuntimeError, match="did not return sessionId"):
        vrli_master.get_version(vrli_opts)


def test_list_hosts_returns_body(vrli_opts, vrli_authed):
    vrli_authed.add(
        responses.GET,
        VRLI_HOSTS_URL,
        json={
            "hosts": [
                {"id": "h-1", "hostname": "vrli-master", "role": "MASTER"},
                {"id": "h-2", "hostname": "vrli-w-1", "role": "WORKER"},
            ]
        },
        status=200,
    )
    assert vrli_master.list_hosts(vrli_opts) == {
        "hosts": [
            {"id": "h-1", "hostname": "vrli-master", "role": "MASTER"},
            {"id": "h-2", "hostname": "vrli-w-1", "role": "WORKER"},
        ]
    }


def test_get_or_none_returns_none_on_404(vrli_opts, vrli_authed):
    vrli_authed.add(
        responses.GET,
        f"{VRLI_HOSTS_URL}/nope",
        json={"errorMessage": "not found"},
        status=404,
    )
    assert vrli_master.get_or_none(vrli_opts, "nope") is None


def test_get_or_none_returns_body_on_200(vrli_opts, vrli_authed):
    vrli_authed.add(
        responses.GET,
        f"{VRLI_HOSTS_URL}/h-1",
        json={"id": "h-1", "role": "MASTER"},
        status=200,
    )
    assert vrli_master.get_or_none(vrli_opts, "h-1") == {"id": "h-1", "role": "MASTER"}


def test_get_or_none_re_raises_non_404(vrli_opts, vrli_authed):
    vrli_authed.add(
        responses.GET,
        f"{VRLI_HOSTS_URL}/h-1",
        json={"errorMessage": "server error"},
        status=500,
    )
    with pytest.raises(requests.HTTPError):
        vrli_master.get_or_none(vrli_opts, "h-1")


def test_session_expired_401_triggers_relogin_and_retry(vrli_opts, mocked_responses):
    # First login → good token; version call returns "Session expired" 401;
    # helper invalidates + re-logs-in and the retry succeeds.
    mocked_responses.add(
        responses.POST,
        VRLI_SESSIONS_URL,
        json={"sessionId": "tok-1", "ttl": 1800},
        status=200,
    )
    mocked_responses.add(
        responses.GET,
        VRLI_VERSION_URL,
        json={"errorMessage": "Session expired"},
        status=401,
    )
    mocked_responses.add(
        responses.POST,
        VRLI_SESSIONS_URL,
        json={"sessionId": "tok-2", "ttl": 1800},
        status=200,
    )
    mocked_responses.add(
        responses.GET,
        VRLI_VERSION_URL,
        json={"version": "8.18.0"},
        status=200,
    )
    assert vrli_master.get_version(vrli_opts) == {"version": "8.18.0"}
    # Two login POSTs and two version GETs.
    urls = [c.request.url for c in mocked_responses.calls]
    assert urls.count(VRLI_SESSIONS_URL) == 2
    assert urls.count(VRLI_VERSION_URL) == 2


def test_non_session_expired_401_does_not_retry(vrli_opts, mocked_responses):
    mocked_responses.add(
        responses.POST,
        VRLI_SESSIONS_URL,
        json={"sessionId": "tok-1", "ttl": 1800},
        status=200,
    )
    mocked_responses.add(
        responses.GET,
        VRLI_VERSION_URL,
        json={"errorMessage": "Forbidden"},
        status=401,
    )
    with pytest.raises(requests.HTTPError):
        vrli_master.get_version(vrli_opts)
    urls = [c.request.url for c in mocked_responses.calls]
    assert urls.count(VRLI_SESSIONS_URL) == 1  # no relogin


# ---------------------------------------------------------------------------
# wait_for_setup_ready — hits the wizard root (or /api/v2/version); < 500 =
# reachable. No session-token dependency (wizard predates sessions API).
# ---------------------------------------------------------------------------


def test_wait_for_setup_ready_accepts_200_html_landing(vrli_opts, mocked_responses, monkeypatch):
    mocked_responses.add(
        responses.GET,
        VRLI_ROOT_URL,
        body="<html>wizard</html>",
        status=200,
        content_type="text/html",
    )
    monkeypatch.setattr(vrli_master.time, "sleep", lambda _s: None)
    monkeypatch.setattr(vrli_master.time, "monotonic", lambda: 0.0)

    result = vrli_master.wait_for_setup_ready(vrli_opts, timeout=60, poll_interval=1)
    assert result["status"] == 200
    assert result["path"] == "/"


def test_wait_for_setup_ready_accepts_401_as_reachable(vrli_opts, mocked_responses, monkeypatch):
    """401/403/302 all count as reachable — the wizard is up, just not accepted yet.

    Any HTTP status < 500 means the appliance's web layer is answering; the
    caller then advances the wizard. Only 5xx / connection errors mean
    "still booting".
    """
    mocked_responses.add(
        responses.GET,
        VRLI_ROOT_URL,
        json={"errorMessage": "not authenticated"},
        status=401,
    )
    monkeypatch.setattr(vrli_master.time, "sleep", lambda _s: None)
    monkeypatch.setattr(vrli_master.time, "monotonic", lambda: 0.0)

    result = vrli_master.wait_for_setup_ready(vrli_opts, timeout=60, poll_interval=1)
    assert result["status"] == 401
    assert result["path"] == "/"


def test_wait_for_setup_ready_polls_past_5xx_and_connection_errors(
    vrli_opts, mocked_responses, monkeypatch
):
    # First attempt: root=503 then /api/v2/version=connection error → loop.
    # Second attempt: root=200 → return.
    mocked_responses.add(
        responses.GET,
        VRLI_ROOT_URL,
        json={"errorMessage": "starting"},
        status=503,
    )
    mocked_responses.add(
        responses.GET,
        VRLI_VERSION_URL,
        body=requests.exceptions.ConnectionError("refused"),
    )
    mocked_responses.add(
        responses.GET,
        VRLI_ROOT_URL,
        body="<html>wizard</html>",
        status=200,
        content_type="text/html",
    )
    sleeps = []
    monkeypatch.setattr(vrli_master.time, "sleep", sleeps.append)
    monkeypatch.setattr(vrli_master.time, "monotonic", lambda: 0.0)

    result = vrli_master.wait_for_setup_ready(vrli_opts, timeout=60, poll_interval=2)
    assert result["status"] == 200
    # Exactly one sleep between the two poll iterations.
    assert sleeps == [2]


def test_wait_for_setup_ready_times_out(vrli_opts, mocked_responses, monkeypatch):
    mocked_responses.add(
        responses.GET,
        VRLI_ROOT_URL,
        json={"errorMessage": "starting"},
        status=503,
    )
    mocked_responses.add(
        responses.GET,
        VRLI_VERSION_URL,
        json={"errorMessage": "starting"},
        status=503,
    )
    mocked_responses.add(
        responses.GET,
        VRLI_ROOT_URL,
        json={"errorMessage": "starting"},
        status=503,
    )
    mocked_responses.add(
        responses.GET,
        VRLI_VERSION_URL,
        json={"errorMessage": "starting"},
        status=503,
    )
    monkeypatch.setattr(vrli_master.time, "sleep", lambda _s: None)
    seq = iter([0.0, 0.0, 999.0])
    monkeypatch.setattr(vrli_master.time, "monotonic", lambda: next(seq))

    with pytest.raises(TimeoutError, match="did not become reachable within 60s"):
        vrli_master.wait_for_setup_ready(vrli_opts, timeout=60, poll_interval=1)


# ---------------------------------------------------------------------------
# bootstrap_master — 3-call CSRF form-encoded flow
# ---------------------------------------------------------------------------


def _csrf_callback(request):
    """Return the CSRF token in the *response header* (not the body)."""
    assert request.headers.get("X-CSRF-Token") == "Fetch"
    return (200, {"X-CSRF-Token": "csrf-tok-xyz"}, '{"succ":true}')


def test_bootstrap_master_fetches_csrf_then_login_then_startup(vrli_opts, mocked_responses):
    """The 3-call CSRF form-encoded flow.

    Verifies:

    * ``GET /csrf`` first, with ``X-CSRF-Token: Fetch`` header; token
      is read from the *response header*.
    * ``POST /login`` second, with ``X-CSRF-Token: <token>`` +
      ``X-Requested-With: XMLHttpRequest``, form-encoded body carrying
      ``authMethod=DEFAULT``.
    * ``POST /admin/startup`` third, form-encoded
      ``_eventName=newDeployment``.
    """
    # VRLI 9.x needs a GET / first to seed JSESSIONID before /csrf.
    mocked_responses.add(responses.GET, VRLI_ROOT_URL, status=302, body="")
    mocked_responses.add_callback(
        responses.GET,
        VRLI_CSRF_URL,
        callback=_csrf_callback,
        content_type="application/json",
    )
    mocked_responses.add(
        responses.POST,
        VRLI_LOGIN_URL,
        json={"succ": True},
        status=200,
    )
    mocked_responses.add(
        responses.POST,
        VRLI_STARTUP_URL,
        json={"succ": True, "operationId": "wiz-1"},
        status=200,
    )

    result = vrli_master.bootstrap_master(vrli_opts, admin_password="VMware123!VMware123!")

    assert result["csrf_token"] == "csrf-tok-xyz"
    assert result["login"] == {"succ": True}
    assert result["startup"] == {"succ": True, "operationId": "wiz-1"}

    # Verify call sequence: initial GET / (to seed JSESSIONID on 9.x), then
    # the 3-call CSRF form flow.
    urls = [c.request.url for c in mocked_responses.calls]
    assert urls == [VRLI_ROOT_URL, VRLI_CSRF_URL, VRLI_LOGIN_URL, VRLI_STARTUP_URL]

    # /csrf request sent the Fetch header.
    csrf_req = mocked_responses.calls[1].request
    assert csrf_req.headers.get("X-CSRF-Token") == "Fetch"

    # /login carries the token from the /csrf response header + XHR marker +
    # form-encoded content-type + authMethod=DEFAULT in the body.
    login_req = mocked_responses.calls[2].request
    assert login_req.headers.get("X-CSRF-Token") == "csrf-tok-xyz"
    assert login_req.headers.get("X-Requested-With") == "XMLHttpRequest"
    assert login_req.headers.get("Content-Type") == "application/x-www-form-urlencoded"
    login_body = login_req.body
    if isinstance(login_body, bytes):
        login_body = login_body.decode("utf-8")
    assert "_eventName=loginAjax" in login_body
    assert "username=admin" in login_body
    assert "authMethod=DEFAULT" in login_body
    # Password may be URL-encoded (! → %21). Check both forms defensively.
    assert (
        "password=VMware123%21VMware123%21" in login_body
        or "password=VMware123!VMware123!" in login_body
    )

    # /admin/startup carries the same token + XHR marker and the
    # _eventName=newDeployment body.
    startup_req = mocked_responses.calls[3].request
    assert startup_req.headers.get("X-CSRF-Token") == "csrf-tok-xyz"
    assert startup_req.headers.get("X-Requested-With") == "XMLHttpRequest"
    assert startup_req.headers.get("Content-Type") == "application/x-www-form-urlencoded"
    startup_body = startup_req.body
    if isinstance(startup_body, bytes):
        startup_body = startup_body.decode("utf-8")
    assert "_eventName=newDeployment" in startup_body


def test_bootstrap_master_rejects_local_authmethod_ambiguity(vrli_opts, mocked_responses):
    """The wizard body MUST send ``authMethod=DEFAULT``, not ``Local``.

    The vRLI 9.0.2 appliance rejects ``authMethod=Local`` with
    "Not a valid value" — the hidden field on the login page is
    ``<input id="login-auth-method" value="DEFAULT">``. This test
    guards against a regression that swaps in the more Bearer-API-like
    ``Local`` value the post-install sessions helper uses.
    """
    mocked_responses.add(responses.GET, VRLI_ROOT_URL, status=302, body="")
    mocked_responses.add_callback(
        responses.GET,
        VRLI_CSRF_URL,
        callback=_csrf_callback,
        content_type="application/json",
    )
    mocked_responses.add(responses.POST, VRLI_LOGIN_URL, json={"succ": True}, status=200)
    mocked_responses.add(responses.POST, VRLI_STARTUP_URL, json={"succ": True}, status=200)

    vrli_master.bootstrap_master(vrli_opts, admin_password="pw")

    # First call = GET /, second = GET /csrf, third = POST /login.
    login_body = mocked_responses.calls[2].request.body
    if isinstance(login_body, bytes):
        login_body = login_body.decode("utf-8")
    assert "authMethod=DEFAULT" in login_body
    assert "authMethod=Local" not in login_body


def test_bootstrap_master_raises_when_csrf_header_and_cookie_both_missing(
    vrli_opts, mocked_responses
):
    """``GET /csrf`` with neither ``X-CSRF-Token`` header nor ``cs`` cookie is a hard error."""
    mocked_responses.add(responses.GET, VRLI_ROOT_URL, status=302, body="")
    mocked_responses.add(
        responses.GET,
        VRLI_CSRF_URL,
        json={"succ": True},
        status=200,
        # Deliberately no X-CSRF-Token header and no cs cookie.
    )
    with pytest.raises(RuntimeError, match="did not return X-CSRF-Token header or cs cookie"):
        vrli_master.bootstrap_master(vrli_opts, admin_password="pw")


def test_bootstrap_master_falls_back_to_cs_cookie_when_header_empty(vrli_opts, mocked_responses):
    """VRLI 9.x sends the CSRF token in a ``cs`` cookie and returns an empty
    ``X-CSRF-Token`` header. The client must pick up the cookie value.
    """
    mocked_responses.add(responses.GET, VRLI_ROOT_URL, status=302, body="")
    mocked_responses.add(
        responses.GET,
        VRLI_CSRF_URL,
        json={"succ": True},
        status=200,
        # Empty X-CSRF-Token header — this is the 9.x behaviour.
        headers={
            "X-CSRF-Token": "",
            "Set-Cookie": "cs=cookie-tok-9x; Path=/; Secure; HttpOnly",
        },
    )
    mocked_responses.add(responses.POST, VRLI_LOGIN_URL, json={"succ": True}, status=200)
    mocked_responses.add(responses.POST, VRLI_STARTUP_URL, json={"succ": True}, status=200)
    result = vrli_master.bootstrap_master(vrli_opts, admin_password="pw")
    assert result["csrf_token"] == "cookie-tok-9x"


def test_bootstrap_master_propagates_login_5xx(vrli_opts, mocked_responses):
    mocked_responses.add(responses.GET, VRLI_ROOT_URL, status=302, body="")
    mocked_responses.add_callback(
        responses.GET,
        VRLI_CSRF_URL,
        callback=_csrf_callback,
        content_type="application/json",
    )
    mocked_responses.add(
        responses.POST,
        VRLI_LOGIN_URL,
        json={"errorMessage": "boom"},
        status=500,
    )
    with pytest.raises(requests.HTTPError):
        vrli_master.bootstrap_master(vrli_opts, admin_password="pw")


def test_bootstrap_master_raises_when_host_missing():
    opts = {"pillar": {"saltext.vcf": {"vrli": {}}}, "test": False}
    with pytest.raises(RuntimeError, match="host is not configured"):
        vrli_master.bootstrap_master(opts, admin_password="pw")


# ---------------------------------------------------------------------------
# reset_admin_password_via_ssh — sshpass + ssh + li-reset-admin-passwd.sh
# ---------------------------------------------------------------------------


def test_reset_admin_password_via_ssh_invokes_sshpass_correctly(monkeypatch):
    """Verify sshpass -e argv shape (password via SSHPASS env, not argv) + shlex-quoted remote pw."""
    monkeypatch.setattr(
        vrli_master.shutil, "which", lambda name: "/usr/bin/sshpass" if name == "sshpass" else None
    )
    fake_run = MagicMock(
        return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    )
    monkeypatch.setattr(vrli_master.subprocess, "run", fake_run)

    ok = vrli_master.reset_admin_password_via_ssh(
        host="25.0.0.60",
        root_password="VMware123!VMware123!",
        new_admin_password="NewPw!42",
    )
    assert ok is True

    argv = fake_run.call_args.args[0]
    assert argv[0] == "/usr/bin/sshpass"
    # -e keeps the password out of argv (was -p <password>); read from $SSHPASS instead.
    assert argv[1] == "-e"
    assert "VMware123!VMware123!" not in argv
    assert argv[2] == "ssh"
    # StrictHostKeyChecking off + throwaway known-hosts (idempotent across reruns).
    assert "-o" in argv and "StrictHostKeyChecking=no" in argv
    assert "UserKnownHostsFile=/dev/null" in argv
    # Target is root@<host> — default ssh_user is 'root'.
    assert "root@25.0.0.60" in argv
    # Final positional is the remote command with --resetAdminPassword and the new pw shlex.quoted.
    remote_cmd = argv[-1]
    assert "/opt/vmware/bin/li-reset-admin-passwd.sh" in remote_cmd
    assert "--resetAdminPassword 'NewPw!42'" in remote_cmd
    # Password is now in the child process's environment.
    env = fake_run.call_args.kwargs.get("env")
    assert env is not None and env.get("SSHPASS") == "VMware123!VMware123!"
    # subprocess.run called with check=False (we surface the return code ourselves)
    # and a timeout.
    assert fake_run.call_args.kwargs.get("check") is False
    assert fake_run.call_args.kwargs.get("timeout") == 60


def test_reset_admin_password_via_ssh_honours_custom_user_and_timeout(monkeypatch):
    monkeypatch.setattr(vrli_master.shutil, "which", lambda name: "/usr/local/bin/sshpass")
    fake_run = MagicMock(
        return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    )
    monkeypatch.setattr(vrli_master.subprocess, "run", fake_run)

    vrli_master.reset_admin_password_via_ssh(
        host="vrli.test",
        root_password="rpw",
        new_admin_password="apw",
        ssh_user="admin",
        timeout=120,
    )
    argv = fake_run.call_args.args[0]
    assert "admin@vrli.test" in argv
    assert fake_run.call_args.kwargs.get("timeout") == 120


def test_reset_admin_password_via_ssh_missing_sshpass_raises(monkeypatch):
    monkeypatch.setattr(vrli_master.shutil, "which", lambda _name: None)
    with pytest.raises(RuntimeError, match="sshpass binary not found"):
        vrli_master.reset_admin_password_via_ssh(
            host="vrli.test",
            root_password="rpw",
            new_admin_password="apw",
        )


def test_reset_admin_password_via_ssh_nonzero_exit_raises(monkeypatch):
    monkeypatch.setattr(vrli_master.shutil, "which", lambda _name: "/usr/bin/sshpass")
    fake_run = MagicMock(
        return_value=subprocess.CompletedProcess(
            args=[], returncode=5, stdout="", stderr="permission denied"
        )
    )
    monkeypatch.setattr(vrli_master.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="exited 5"):
        vrli_master.reset_admin_password_via_ssh(
            host="vrli.test",
            root_password="rpw",
            new_admin_password="apw",
        )


# ---------------------------------------------------------------------------
# join_worker stub — MVP-out-of-scope guard.
# ---------------------------------------------------------------------------


def test_join_worker_is_not_implemented():
    with pytest.raises(NotImplementedError, match="not implemented"):
        vrli_master.join_worker()


# ---------------------------------------------------------------------------
# Custom port from pillar (post-install Bearer path)
# ---------------------------------------------------------------------------


def test_custom_port_from_pillar(mocked_responses):
    opts = {
        "pillar": {
            "saltext.vcf": {
                "vrli": {
                    "host": VRLI_HOST,
                    "port": 8443,
                    "username": "admin",
                    "password": "p",
                    "verify_ssl": False,
                },
            },
        },
        "test": False,
    }
    mocked_responses.add(
        responses.POST,
        f"https://{VRLI_HOST}:8443/api/v2/sessions",
        json={"sessionId": "tok"},
        status=200,
    )
    mocked_responses.add(
        responses.GET,
        f"https://{VRLI_HOST}:8443/api/v2/version",
        json={"version": "8.18.0"},
        status=200,
    )
    assert vrli_master.get_version(opts) == {"version": "8.18.0"}
