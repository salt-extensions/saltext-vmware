"""vRLI master-node read/deploy helpers.

The endpoints used here cover both the "is this vRLI master installed and
reachable" verify path and the first-boot wizard flow used by the
``installed`` state's deploy-on-absence branch.

Post-install (session-Bearer, ``/api/v2/...``) endpoints — go through the
:mod:`saltext.vcf.utils.vrli` helpers (Bearer auth on ``/api/v2/sessions``):

- ``GET /api/v2/version`` — ``{"releaseName": "...", "version": "..."}``.
- ``GET /api/v2/hosts``   — cluster node inventory.
- :func:`get_or_none` — 404-tolerant single-host lookup.

First-boot wizard (form-encoded, CSRF header, no session helper) — the
appliance runs a small stateful web flow at ``https://<vrli>/`` **before**
``/api/v2/sessions`` exists. See
``~/src/saltext-opsdev/VRLI-MANUAL-INSTALL-NOTES.md`` for the manual-install
transcript this flow was derived from:

- :func:`wait_for_setup_ready` — polls the wizard root until it answers
  reachable (``<500``: HTML, 401/403 pre-accept, or the post-install 200).
- :func:`bootstrap_master` — 3-call sequence:
    1. ``GET  /csrf``          with header ``X-CSRF-Token: Fetch`` — token
       comes back in the *response header* ``X-CSRF-Token``.
    2. ``POST /login``         form-encoded,
       ``_eventName=loginAjax&username=admin&password=<pw>&authMethod=DEFAULT``.
       (``authMethod=Local`` is rejected upstream; DEFAULT is the only
       accepted value.)
    3. ``POST /admin/startup`` form-encoded,
       ``_eventName=newDeployment&...``. Appliance briefly 302s to ``/login``
       while it "becomes master"; wait for ``/api/v2/version`` = 200 to
       confirm it has finished.
- :func:`reset_admin_password_via_ssh` — idempotent CLI shortcut that runs
  ``/opt/vmware/bin/li-reset-admin-passwd.sh --resetAdminPassword '<pw>'``
  over ``sshpass + ssh``. Faster and stateless than the wizard change-pw
  POST; safe to re-run.
- :func:`join_worker` — Worker join is out of scope for the MVP; see the
  notes for follow-up.
"""

import logging
import os
import shlex
import shutil
import subprocess
import time

import requests
import urllib3

from saltext.vcf.utils import vrli

log = logging.getLogger(__name__)


_VERSION_PATH = "/api/v2/version"
_HOSTS_PATH = "/api/v2/hosts"
_CSRF_PATH = "/csrf"
_LOGIN_PATH = "/login"
_STARTUP_PATH = "/admin/startup"


def get_version(opts, profile=None):
    """Return the ``{"releaseName", "version"}`` dict from ``/api/v2/version``."""
    return vrli.api_get(opts, _VERSION_PATH, profile=profile)


def list_hosts(opts, profile=None):
    """Return the raw ``/api/v2/hosts`` response (cluster node inventory)."""
    return vrli.api_get(opts, _HOSTS_PATH, profile=profile)


def get(opts, host_id, profile=None):
    """Fetch a single cluster node by id (``/api/v2/hosts/{id}``)."""
    return vrli.api_get(opts, f"{_HOSTS_PATH}/{host_id}", profile=profile)


def get_or_none(opts, host_id, profile=None):
    """Return the host dict for *host_id*, or ``None`` if the API returns 404."""
    try:
        return get(opts, host_id, profile=profile)
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            return None
        raise


def _wizard_base_url(opts, profile=None):
    """Build the ``https://<host>:<port>`` base for wizard-flow calls.

    Uses the same pillar config as the post-install helpers (host/port/
    verify_ssl come from :func:`saltext.vcf.utils.vrli.get_config`) but
    does not mint a session token — the wizard is form-encoded and lives
    *before* ``/api/v2/sessions`` exists.
    """
    cfg = vrli.get_config(opts, profile=profile)
    if not cfg["host"]:
        raise RuntimeError("saltext.vcf.vrli.host is not configured; cannot reach vRLI master")
    return f"https://{cfg['host']}:{cfg['port']}", cfg["verify_ssl"]


def wait_for_setup_ready(opts, timeout=1800, poll_interval=20, profile=None):
    """Block until the wizard root at ``/`` (or ``/api/v2/version``) is reachable.

    "Reachable" is deliberately loose: any HTTP status < 500 counts. The
    wizard responds with:

    * 200 + HTML on the pre-accept landing page.
    * 401 or 403 while login is required but not yet issued.
    * 200 (JSON) once ``/api/v2/version`` starts answering post-bootstrap.

    Anything 5xx or a raw connection error is treated as "still booting"
    and retried. Raises ``TimeoutError`` on deadline expiry.

    Returns a small dict describing the probe that finally succeeded::

        {"status": <int>, "path": "/", "content_type": "text/html"}
    """
    base, verify = _wizard_base_url(opts, profile=profile)
    if not verify:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    deadline = time.monotonic() + float(timeout)
    last_exc = None
    last_status = None
    while True:
        for path in ("/", _VERSION_PATH):
            try:
                resp = requests.get(
                    f"{base}{path}",
                    verify=verify,
                    timeout=10,
                    allow_redirects=False,
                )
            except requests.RequestException as exc:
                last_exc = exc
                log.info(
                    "vrli_master.wait_for_setup_ready: %s not ready yet: %s",
                    path,
                    exc,
                )
                continue
            last_status = resp.status_code
            if resp.status_code < 500:
                return {
                    "status": resp.status_code,
                    "path": path,
                    "content_type": resp.headers.get("Content-Type", ""),
                }
            log.info(
                "vrli_master.wait_for_setup_ready: %s answered %s (still starting)",
                path,
                resp.status_code,
            )
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"vRLI master wizard did not become reachable within "
                f"{timeout}s (last status: {last_status}, last error: {last_exc})"
            )
        time.sleep(float(poll_interval))


def bootstrap_master(opts, admin_password, profile=None, timeout=600):
    """Drive the vRLI first-run wizard via the 3-call CSRF form flow.

    The wizard is *not* the ``/api/v2/sessions`` Bearer API used
    post-install — it is a form-encoded, CSRF-header protected flow at
    ``https://<vrli>/``. See
    ``~/src/saltext-opsdev/VRLI-MANUAL-INSTALL-NOTES.md`` for the manual
    transcript this implementation mirrors.

    Sequence:

    1. ``GET /csrf`` with header ``X-CSRF-Token: Fetch``. Body is
       ``{"succ":true}``; the token itself is in the response header
       ``X-CSRF-Token``.
    2. ``POST /login`` (form-encoded):
       ``_eventName=loginAjax&username=admin&password=<admin_password>``
       ``&authMethod=DEFAULT``. **``authMethod=Local`` is rejected** by
       the appliance ("Not a valid value"); DEFAULT is the only accepted
       value.
    3. ``POST /admin/startup`` (form-encoded):
       ``_eventName=newDeployment&...``. The appliance then briefly 302s
       to ``/login`` while it "becomes master"; the caller should wait
       for ``/api/v2/version`` = 200 before hitting anything else.

    All three calls share a ``requests.Session()`` with the CSRF token
    propagated in the ``X-CSRF-Token`` header (and any cookies the
    appliance sets on ``/csrf`` or ``/login``).

    :param opts: Salt opts / pillar dict — used only to resolve the
        base URL and ``verify_ssl`` flag.
    :param admin_password: The password to authenticate ``admin`` with
        for the wizard. On a fresh OVA this is whatever the appliance
        came up with (default: the ``rootpw`` OVF property supplied at
        deploy time; consult the pillar / OVF spec — do not hardcode).
    :param profile: Optional pillar profile.
    :param timeout: Per-request timeout for the wizard POSTs (seconds).
    :returns: ``{"csrf_token": "<tok>", "login": <body>, "startup": <body>}``.
    """
    base, verify = _wizard_base_url(opts, profile=profile)
    if not verify:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    session = requests.Session()
    session.verify = verify

    # 0. Establish JSESSIONID (VRLI 9.x requires a prior GET to seed the session).
    session.get(f"{base}/", timeout=timeout, allow_redirects=False)

    # 1. Fetch CSRF token. VRLI 9.x sends it in the `cs` cookie rather than
    #    the X-CSRF-Token response header (older builds used the header).
    csrf_resp = session.get(
        f"{base}{_CSRF_PATH}",
        headers={"X-CSRF-Token": "Fetch"},
        timeout=timeout,
    )
    csrf_resp.raise_for_status()
    token = csrf_resp.headers.get("X-CSRF-Token") or session.cookies.get("cs")
    if not token:
        raise RuntimeError(
            f"vRLI wizard GET {_CSRF_PATH} did not return X-CSRF-Token header or cs cookie "
            f"(body={csrf_resp.text!r})"
        )

    wizard_headers = {
        "X-CSRF-Token": token,
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    # 2. Login as admin (authMethod=DEFAULT — Local is rejected).
    login_body = {
        "_eventName": "loginAjax",
        "username": "admin",
        "password": admin_password,
        "authMethod": "DEFAULT",
    }
    login_resp = session.post(
        f"{base}{_LOGIN_PATH}",
        headers=wizard_headers,
        data=login_body,
        timeout=timeout,
    )
    login_resp.raise_for_status()
    login_body_parsed = _parse_wizard_body(login_resp)

    # 3. Advance the wizard: bootstrap this appliance as the master.
    startup_body = {"_eventName": "newDeployment"}
    startup_resp = session.post(
        f"{base}{_STARTUP_PATH}",
        headers=wizard_headers,
        data=startup_body,
        timeout=timeout,
    )
    startup_resp.raise_for_status()
    startup_body_parsed = _parse_wizard_body(startup_resp)

    return {
        "csrf_token": token,
        "login": login_body_parsed,
        "startup": startup_body_parsed,
    }


def _parse_wizard_body(resp):
    """Best-effort JSON parse for a wizard response; fall back to raw text."""
    if not resp.content:
        return {}
    try:
        return resp.json()
    except ValueError:
        return {"_raw": resp.text}


def reset_admin_password_via_ssh(
    host,
    root_password,
    new_admin_password,
    ssh_user="root",
    timeout=60,
):
    """Reset the vRLI ``admin`` password via SSH + ``li-reset-admin-passwd.sh``.

    This is the CLI shortcut recorded in
    ``~/src/saltext-opsdev/VRLI-MANUAL-INSTALL-NOTES.md``: idempotent,
    stateless, and does not depend on the wizard having advanced. It
    writes a salted SHA256 hash directly into the Cassandra
    ``logdb.user_auth`` table.

    Equivalent to (with ``SSHPASS=<root_password>`` in the environment)::

        sshpass -e ssh -o StrictHostKeyChecking=no \\
            <ssh_user>@<host> \\
            /opt/vmware/bin/li-reset-admin-passwd.sh \\
            --resetAdminPassword '<new_admin_password>'

    :param host: vRLI appliance IP or FQDN.
    :param root_password: Root password on the appliance (the OVF
        ``rootpw`` property supplied at deploy time; caller provides,
        never a hardcoded default).
    :param new_admin_password: New ``admin`` UI password to install.
    :param ssh_user: SSH user (default ``root``).
    :param timeout: Subprocess timeout in seconds (default 60).
    :returns: ``True`` on success.
    :raises RuntimeError: If ``sshpass`` is missing or the remote
        command exits non-zero.
    """
    sshpass = shutil.which("sshpass")
    if not sshpass:
        raise RuntimeError(
            "sshpass binary not found on PATH; required for the SSH "
            "admin-password reset shortcut"
        )
    # shlex.quote so a password containing shell metacharacters (``'``,
    # ``$``, ``;``, …) can't break out of the remote-shell arg.
    remote_cmd = (
        "/opt/vmware/bin/li-reset-admin-passwd.sh "
        f"--resetAdminPassword {shlex.quote(new_admin_password)}"
    )
    # ``sshpass -e`` reads the password from ``$SSHPASS`` instead of
    # taking it as an argv value with ``-p``; keeps it out of the
    # process command line (``ps aux``) for the lifetime of the
    # subprocess.
    argv = [
        sshpass,
        "-e",
        "ssh",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        f"{ssh_user}@{host}",
        remote_cmd,
    ]
    env = os.environ.copy()
    env["SSHPASS"] = root_password
    log.info(
        "vrli_master.reset_admin_password_via_ssh: running li-reset-admin-passwd.sh "
        "against %s@%s",
        ssh_user,
        host,
    )
    result = subprocess.run(  # noqa: S603 — argv is fixed shape
        argv,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"li-reset-admin-passwd.sh on {host} exited "
            f"{result.returncode}: stderr={result.stderr!r}"
        )
    return True


def join_worker(*_args, **_kwargs):
    """Not implemented — Worker join is out of scope for the MVP.

    The manual-install notes at
    ``~/src/saltext-opsdev/VRLI-MANUAL-INSTALL-NOTES.md`` only cover the
    single-node master deploy. Adding a worker requires driving a
    separate wizard flow (``/admin/cluster`` or similar) against the
    already-bootstrapped master; the endpoint shape has not been
    reverse-engineered yet.
    """
    raise NotImplementedError(
        "vRLI worker join is not implemented — MVP only covers single-node "
        "master deploys. See ~/src/saltext-opsdev/VRLI-MANUAL-INSTALL-NOTES.md."
    )
