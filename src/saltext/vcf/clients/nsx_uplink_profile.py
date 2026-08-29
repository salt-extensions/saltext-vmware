"""NSX uplink (host-switch) profiles (Policy API ``/infra/host-switch-profiles``).

Defines NIC teaming (and transport VLAN/MTU) for a host or Edge transport
node's uplinks — attached to a transport node profile / transport node,
not created here.

Field names below follow the standard NSX-T Policy API shape but haven't
been exercised against a live NSX Manager — verify before real use.
"""

import requests

from saltext.vcf.utils import nsx

PATH = "/policy/api/v1/infra/host-switch-profiles"


def list_(opts, profile=None):
    return nsx.api_get(opts, PATH, profile=profile)


def get(opts, profile_id, profile=None):
    return nsx.api_get(opts, f"{PATH}/{profile_id}", profile=profile)


def get_or_none(opts, profile_id, profile=None):
    try:
        return get(opts, profile_id, profile=profile)
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            return None
        raise


def create(opts, profile_id, teaming, profile=None, **spec):
    """Create or update an uplink profile (Policy API uses PUT).

    *teaming* is a dict like ``{"policy": "FAILOVER_ORDER", "active_list":
    [{"uplink_name": "uplink-1", "uplink_type": "PNIC"}]}``. Extra fields
    (``mtu``, ``transport_vlan``, ``named_teamings``, ...) pass through via
    *spec*.
    """
    body = {
        "display_name": spec.pop("display_name", profile_id),
        "resource_type": "PolicyUplinkHostSwitchProfile",
        "teaming": teaming,
    }
    body.update(spec)
    return nsx.api_put(opts, f"{PATH}/{profile_id}", body=body, profile=profile)


def delete(opts, profile_id, profile=None):
    return nsx.api_delete(opts, f"{PATH}/{profile_id}", profile=profile)
