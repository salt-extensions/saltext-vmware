"""VCF Orchestrator (VRO) — read + deploy helpers over ``/vco/api``.

The MVP surface was verify-only. This module now also exposes the small
set of Control Center endpoints needed to bring a freshly-deployed OVA
into a serving state:

* :func:`get_version` — ``GET /vco/api/about`` (unauthenticated on some
  builds, authenticated on all recent ones). Returns the appliance's
  self-reported version dict.
* :func:`list_workflows` — ``GET /vco/api/workflows`` with a small
  ``maxResult`` cap. Confirms the workflow engine is actually serving
  API traffic, not just the login page.
* :func:`get_or_none` — case-insensitive workflow lookup by name;
  returns ``None`` when nothing matches (used by state idempotency).
* :func:`wait_for_setup_ready` — polls ``GET /vco/api/about`` until it
  returns HTTP 200 (used post-OVA-boot and post-SSO-restart).
* :func:`sso_join` — ``POST /vco-controlcenter/api/server/sso`` with
  ``{ssoUrl, adminUser, adminPassword}``. The appliance restarts after
  a successful SSO join.
* :func:`install_license` — ``POST /vco-controlcenter/api/server/license``
  with a license key (optional; skipped when the caller omits it).
"""

import logging
import time

import requests

from saltext.vcf.utils import vro as vro_util

log = logging.getLogger(__name__)

_ABOUT = "/vco/api/about"
_WORKFLOWS = "/vco/api/workflows"
_CC_SSO = "/vco-controlcenter/api/server/sso"
_CC_LICENSE = "/vco-controlcenter/api/server/license"


def get_version(opts, profile=None):
    """Return VRO's ``/vco/api/about`` payload.

    The dict typically carries ``version``, ``build-number``,
    ``build-date`` and ``api-version``. Raises ``requests.HTTPError``
    on non-2xx; callers wanting a "reachable?" check should catch that.
    """
    return vro_util.api_get(opts, _ABOUT, profile=profile)


def list_workflows(opts, profile=None, limit=1):
    """List workflow definitions (default ``limit=1``: functional probe).

    Uses VRO's HATEOAS ``links`` envelope. Returns the raw response so
    callers can inspect ``links`` or ``total`` as they need.
    """
    params = {"maxResult": limit} if limit else None
    return vro_util.api_get(opts, _WORKFLOWS, params=params, profile=profile)


def get_or_none(opts, name, profile=None):
    """Look up a workflow by name; ``None`` when absent.

    VRO returns 404 when a resource is missing and 200 with an empty
    ``links`` list when a search yields nothing — both surface as
    ``None`` here so state modules can treat them uniformly.
    """
    try:
        body = vro_util.api_get(
            opts,
            _WORKFLOWS,
            params={"conditions": f"name={name}"},
            profile=profile,
        )
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            return None
        raise
    links = (body or {}).get("link", []) or (body or {}).get("links", [])
    for entry in links:
        attrs = {a.get("name"): a.get("value") for a in entry.get("attributes", [])}
        wf_name = attrs.get("name")
        if wf_name and wf_name.lower() == name.lower():
            return entry
    return None


def wait_for_setup_ready(opts, timeout=1800, poll_interval=20, profile=None):
    """Poll ``GET /vco/api/about`` until the appliance answers with 200.

    Used twice by the deploy flow: once after the OVA has been pushed
    and powered on (initial first-boot can take 15-30 minutes), and
    again after the SSO join restart. Any 2xx counts as "ready";
    everything else — connection error, 5xx, 4xx — is treated as
    "still booting" and retried until the deadline.

    Raises :class:`TimeoutError` if *timeout* seconds elapse without a
    successful GET.
    """
    deadline = time.monotonic() + float(timeout)
    last_error = None
    while True:
        try:
            return get_version(opts, profile=profile)
        except (
            requests.exceptions.RequestException,
            requests.HTTPError,
        ) as exc:
            last_error = exc
            log.info("vro_orchestrator.wait_for_setup_ready: not ready yet: %s", exc)
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"VRO appliance /vco/api/about not ready within {timeout}s "
                f"(last error: {last_error})"
            )
        time.sleep(float(poll_interval))


def sso_join(
    opts,
    lookup_service_url,
    admin_user,
    admin_password,
    profile=None,
    timeout=600,
):
    """Join the VRO appliance to a vCenter SSO / Lookup Service.

    Posts ``{ssoUrl, adminUser, adminPassword}`` to
    ``/vco-controlcenter/api/server/sso``. The appliance validates the
    credentials, writes the SSO configuration, and restarts —
    :func:`wait_for_setup_ready` should be called afterwards to bridge
    the restart.

    Returns the JSON payload the appliance sends back (may be empty).
    """
    body = {
        "ssoUrl": lookup_service_url,
        "adminUser": admin_user,
        "adminPassword": admin_password,
    }
    return vro_util.api_post(
        opts,
        _CC_SSO,
        body=body,
        profile=profile,
        timeout=timeout,
    )


def install_license(opts, license_key, profile=None, timeout=60):
    """Install a license key on the VRO appliance.

    Posts ``{licenseKey}`` to ``/vco-controlcenter/api/server/license``.
    Returns the JSON payload (may be empty on success).
    """
    body = {"licenseKey": license_key}
    return vro_util.api_post(
        opts,
        _CC_LICENSE,
        body=body,
        profile=profile,
        timeout=timeout,
    )
